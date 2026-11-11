"""Explicit experience review over the original, processed native event stream."""

from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
import json
import re
import sqlite3
import time
from uuid import NAMESPACE_URL, uuid5

from .errors import BudgetExceeded, Conflict, InvalidRequest, Unavailable
from .models import identifier, integer
from .query_sources import SourceFailure, connect_events
from .sanitization import CaptureError, clean_text
from .source_events import canonical, workspace_key
from .timeline_index import classification, exec_outcomes, occurrence

MAX_EVENTS_PER_SESSION = 400
MAX_EVENT_BYTES = 65536
MAX_ACTION_BYTES = 512
MAX_WORKFLOW_GAP_SECONDS = 300
EXEC_COMPLETED = re.compile(r"Script completed\r?\nWall time [0-9]+(?:\.[0-9]+)? seconds\r?\nOutput:\s*")
EXEC_CHILD_CALL = re.compile(r"\btools\.exec_command\s*\(")

REJECTIONS = """
CREATE TABLE IF NOT EXISTS experience_rejections (
    id INTEGER PRIMARY KEY,
    workspace TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    evidence_version TEXT NOT NULL,
    event_ids TEXT NOT NULL,
    reason TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    UNIQUE(workspace, candidate_id, evidence_version)
)
"""


def _id(kind, value):
    return uuid5(NAMESPACE_URL, "contextrail/experience/v1/" + kind + "/" + canonical(value)).hex


def _window(binding, since, until):
    if binding is None or len({session for _, session in binding.native_sessions}) < 2:
        raise InvalidRequest("Bind at least two distinct native sessions for experience review.")
    start, start_seconds, _ = occurrence(since)
    stop, stop_seconds, _ = occurrence(until)
    if ((since is not None and start is None) or (until is not None and stop is None)
            or (start is not None and stop is not None and start_seconds >= stop_seconds)):
        raise InvalidRequest("Use an increasing time range with explicit time zones.")
    return start, stop


def _action(body):
    tool = body.get("tool")
    args = body.get("arguments")
    if not isinstance(tool, str) or not tool:
        return None
    if tool == "exec" and isinstance(args, str) and args.strip():
        action = {"tool": tool, "script": args.strip()}
    elif tool in {"functions.exec_command", "Bash"} and isinstance(args, dict):
        command = args.get("cmd", args.get("command", args.get("argv")))
        if not (isinstance(command, str) and command.strip() or
                isinstance(command, list) and command and all(isinstance(arg, str) for arg in command)):
            return None
        # An explicit workdir carries meaning. Transport timeouts and output caps do not.
        cwd = args.get("workdir") or args.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            return None
        action = {"tool": "Bash", "command": command, "workdir": workspace_key(cwd) if cwd else None}
        # Shell selection and profile loading can change the same command.
        for name in ("shell", "login", "env"):
            if name in args:
                action[name] = args[name]
    elif isinstance(args, (dict, list)):
        action = {"tool": body.get("original_tool") or tool, "arguments": args}
    else:
        return None
    encoded = canonical(action)
    if "[REDACTED" in encoded or len(encoded.encode("utf-8")) > MAX_ACTION_BYTES:
        return None
    return action


def _outcome(client, action, body):
    state = body.get("error_state")
    output = body.get("output")
    codes, failed_output, incomplete = [], "", False
    if client == "codex" and action["tool"] == "Bash":
        codes, failed_output, incomplete = exec_outcomes(output)
    elif client == "codex" and action["tool"] == "exec":
        if (EXEC_CHILD_CALL.search(action.get("script", ""))
                and isinstance(output, list) and len(output) > 1
                and isinstance(output[0], dict)
                and output[0].get("type") in {"input_text", "text", "output_text"}
                and EXEC_COMPLETED.fullmatch(str(output[0].get("text", "")))):
            codes, failed_output, incomplete = exec_outcomes(output[1:])
            incomplete |= len(codes) != len(output) - 1
        else:
            incomplete = True
    if state == "source_error" or any(codes):
        sample = failed_output or (output if isinstance(output, str) else canonical(output))
        category, _ = classification(sample, action["tool"], "source_error")
        return "repeated_failure", category
    if incomplete:
        return None, None
    if codes or state == "source_ok":
        return "repeated_step", None
    return None, None


@dataclass(frozen=True)
class Observation:
    id: str
    sequence: int
    event_type: str
    call_id: str | None
    turn_id: str | None
    time: str | None
    body: dict | None


def _observations(db, workspace, client, session, since, until):
    where = ["n.workspace=?", "n.client=?", "n.native_session=?",
             "n.event_type IN ('tool.call', 'tool.result')"]
    args = [workspace_key(workspace), client, session]
    for timestamp, operator in ((since, ">="), (until, "<")):
        if timestamp is not None:
            where.append("julianday(json_extract(n.metadata,'$.occurred_at'))" + operator + "julianday(?)")
            args.append(timestamp)
    rows = db.execute("""
        SELECT n.event_id, n.sequence, n.event_type, n.metadata,
               CASE WHEN length(CAST(s.content AS BLOB)) <= ? THEN s.content END AS content
        FROM normalized_events n LEFT JOIN source_events s ON s.id=n.event_id
        WHERE """ + " AND ".join(where) + " ORDER BY n.sequence DESC LIMIT ?",
        [MAX_EVENT_BYTES, *args, MAX_EVENTS_PER_SESSION + 1]).fetchall()
    truncated = len(rows) > MAX_EVENTS_PER_SESSION
    events = []
    for row in reversed(rows[:MAX_EVENTS_PER_SESSION]):
        meta = json.loads(row["metadata"])
        body = None
        if row["content"] is not None and meta.get("body_state") == "complete":
            body = json.loads(row["content"]).get("final")
        events.append(Observation(
            row["event_id"], row["sequence"], row["event_type"], meta.get("call_id"),
            meta.get("turn_id"), meta.get("occurred_at"), body if isinstance(body, dict) else None))
    return events, truncated


def _adjacent(previous, call):
    previous_call, previous_result, _ = previous
    if previous_result.sequence >= call.sequence:
        return False
    if previous_call.turn_id != call.turn_id:
        return False
    if previous_call.turn_id:
        return True
    _, end, _ = occurrence(previous_result.time)
    _, start, _ = occurrence(call.time)
    return end is not None and start is not None and 0 <= start - end <= MAX_WORKFLOW_GAP_SECONDS


def _session_patterns(events, workspace, client, session):
    calls = defaultdict(list)
    results = defaultdict(list)
    for event in events:
        if event.call_id:
            index = calls if event.event_type == "tool.call" else results
            index[event.call_id].append(event)

    source = {"client": client, "native_session": session}
    patterns = {}
    unknown = 0
    previous = None

    def record(kind, category, actions, evidence):
        key = canonical([kind, category, actions])
        refs = [{"reader": "memory.stream_get", "workspace": workspace, **source, "event_id": item.id}
                for item in evidence]
        patterns.setdefault(key, (kind, category, actions, {**source, "refs": refs}))

    for call in (event for event in events if event.event_type == "tool.call"):
        matching = results.get(call.call_id, ())
        action = _action(call.body) if call.body is not None else None
        if (not action or len(calls.get(call.call_id, ())) != 1 or len(matching) != 1
                or matching[0].body is None):
            unknown += 1
            previous = None
            continue
        result = matching[0]
        kind, category = _outcome(client, action, result.body)
        if kind is None:
            unknown += 1
            previous = None
            continue
        record(kind, category, [action], [call, result])
        if kind == "repeated_step":
            if previous and _adjacent(previous, call) and call.sequence < result.sequence:
                previous_call, previous_result, previous_action = previous
                record("repeated_workflow", None, [previous_action, action],
                       [previous_call, previous_result, call, result])
            previous = (call, result, action) if call.sequence < result.sequence else None
        else:
            previous = None
    return patterns, unknown


def _candidates(db, binding, since, until):
    patterns = defaultdict(dict)
    coverage = []
    for client, session in binding.native_sessions:
        events, truncated = _observations(db, binding.workspace, client, session, since, until)
        entries, unknown = _session_patterns(events, binding.workspace, client, session)
        coverage.append({"client": client, "native_session": session, "scanned_tool_events": len(events),
                         "older_events_omitted": truncated, "unknown_outcomes": unknown})
        for key, pattern in entries.items():
            patterns[key].setdefault(session, pattern)

    candidates = []
    for key, by_session in sorted(patterns.items()):
        if len(by_session) < 2:
            continue
        kind, category, actions, _ = next(iter(by_session.values()))
        evidence = [pattern[3] for _, pattern in sorted(by_session.items())]
        candidate_id = _id("candidate", [workspace_key(binding.workspace), key])
        ids = sorted(ref["event_id"] for item in evidence for ref in item["refs"])
        candidates.append({"id": candidate_id, "kind": kind, "category": category, "actions": actions,
                           "evidence": evidence, "evidence_version": _id("evidence", [candidate_id, ids]),
                           "trust": "candidate_from_observed_tool_events"})
    return candidates, coverage


def _event_ids(candidate):
    return {ref["event_id"] for item in candidate["evidence"] for ref in item["refs"]}


def _decision(row):
    return {field: row[field] for field in ("candidate_id", "evidence_version", "reason", "recorded_at")}


def _decisions(db, workspace, candidate, reconsider):
    history = db.execute("""SELECT * FROM experience_rejections
        WHERE workspace=? AND candidate_id=? ORDER BY id DESC""", (workspace, candidate["id"]))
    latest = next(history, None)
    if latest is None:
        return "first_review", None, []
    if reconsider == candidate["id"]:
        return "explicit_reconsideration", _decision(latest), []
    missing = _event_ids(candidate)
    missing.difference_update(json.loads(latest["event_ids"]))
    for previous in history:
        if not missing:
            break
        missing.difference_update(json.loads(previous["event_ids"]))
    return ("new_evidence" if missing else "suppressed"), _decision(latest), sorted(missing)


def review_experiences(binding, *, since=None, until=None, reconsider=None,
                       offset=0, limit=20, budget=65536):
    since, until = _window(binding, since, until)
    integer(offset)
    if not 1 <= limit <= 100:
        raise InvalidRequest("Experience review limit must be 1..100.")
    if reconsider is not None:
        identifier(reconsider)
    deadline = time.monotonic() + binding.timeout_seconds
    try:
        with closing(connect_events(binding, deadline)) as db:
            candidates, coverage = _candidates(db, binding, since, until)
            decisions_exist = db.execute("""SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='experience_rejections'""").fetchone()
            visible = []
            suppressed = 0
            workspace = workspace_key(binding.workspace)
            for candidate in candidates:
                reason, previous, new_ids = (_decisions(db, workspace, candidate, reconsider)
                                             if decisions_exist else ("first_review", None, []))
                if reason == "suppressed":
                    suppressed += 1
                    continue
                visible.append({**candidate, "review_reason": reason, "last_rejection": previous,
                                "new_evidence_event_ids": new_ids})
    except (sqlite3.Error, SourceFailure):
        raise Unavailable("The bound memory events or experience decisions could not be read.") from None

    if reconsider is not None:
        visible.sort(key=lambda item: item["id"] != reconsider)
    report = {"schema": "contextrail.experience-review/v1", "workspace": binding.workspace,
              "window": {"since": since, "until": until}, "coverage": coverage, "candidates": [],
              "visible_count": len(visible), "suppressed_count": suppressed, "next_offset": None,
              "reconsideration_found": (any(item["id"] == reconsider for item in candidates)
                                        if reconsider is not None else None)}
    if len(canonical(report).encode("utf-8")) > budget:
        raise BudgetExceeded("Experience review coverage exceeds the response budget.")
    for item in visible[offset:offset + limit]:
        report["candidates"].append(item)
        position = offset + len(report["candidates"])
        report["next_offset"] = position if position < len(visible) else None
        if len(canonical(report).encode("utf-8")) > budget:
            report["candidates"].pop()
            if not report["candidates"]:
                raise BudgetExceeded("A complete experience candidate exceeds the response budget.")
            report["next_offset"] = position - 1
            break
    return report


def reject_experience(binding, candidate_id, version, reason, *, since=None, until=None):
    since, until = _window(binding, since, until)
    identifier(candidate_id)
    identifier(version)
    try:
        reason = clean_text(reason.strip())
    except CaptureError:
        raise InvalidRequest("Invalid experience rejection reason.") from None
    if not reason or len(reason.encode("utf-8")) > 1024:
        raise InvalidRequest("Provide a rejection reason of at most 1024 UTF-8 bytes.")
    try:
        uri = (binding.memory_home / "state.sqlite").as_uri() + "?mode=rw"
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as db, db:
            db.row_factory = sqlite3.Row
            deadline = time.monotonic() + binding.timeout_seconds
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            db.execute("BEGIN IMMEDIATE")
            candidates, _ = _candidates(db, binding, since, until)
            candidate = next((item for item in candidates if item["id"] == candidate_id), None)
            if candidate is None or candidate["evidence_version"] != version:
                raise Conflict("Experience evidence changed. Review the current selection before rejecting.")
            db.execute(REJECTIONS)
            workspace = workspace_key(binding.workspace)
            db.execute("""INSERT INTO experience_rejections
                (workspace, candidate_id, evidence_version, event_ids, reason, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace, candidate_id, evidence_version) DO NOTHING""",
                (workspace, candidate_id, version, canonical(sorted(_event_ids(candidate))), reason, time.time()))
            saved = db.execute("""SELECT * FROM experience_rejections
                WHERE workspace=? AND candidate_id=? AND evidence_version=?""",
                (workspace, candidate_id, version)).fetchone()
            if saved["reason"] != reason:
                raise Conflict("This evidence version already has a different rejection reason.")
            return {**_decision(saved), "reopen_when": ["new_evidence", "explicit_reconsideration"]}
    except sqlite3.Error:
        raise Unavailable("The bound memory database could not record the rejection.") from None
