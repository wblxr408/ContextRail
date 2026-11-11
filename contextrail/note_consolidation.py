"""Reversible presentation of similar Basic Memory notes in the original owner."""

from collections import Counter
from hashlib import sha256
from itertools import combinations
import json
import os
from pathlib import Path
import time
from uuid import NAMESPACE_URL, uuid5


NOTE_LIMIT = 12
BODY_LIMIT = 16384
SIMILARITY = 0.64
SCHEMA = "contextrail.note-consolidation/v1"

DECISIONS = """
CREATE TABLE IF NOT EXISTS note_consolidation_events (
    sequence INTEGER PRIMARY KEY,
    workspace TEXT NOT NULL,
    group_id TEXT NOT NULL,
    revision TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('applied', 'undone')),
    representative TEXT NOT NULL,
    at REAL NOT NULL
)
"""


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _scope(value):
    return os.path.normcase(str(value)).replace("\\", "/").rstrip("/")


def _identity(kind, value):
    return uuid5(NAMESPACE_URL, "contextrail/note-consolidation/" + kind + "/" + _json(value)).hex


def _text(value, label, limit=512):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise ValueError(f"Provide a bounded {label}.")
    return value.strip()


def _version(note, relations):
    content = [note["permalink"], note["path"], note["title"], note["scope"], note["status"],
               note["date"], note["content"], relations]
    return "note-v1:" + sha256(_json(content).encode("utf-8")).hexdigest()


def _note_snapshot(note, relations):
    from capture_sanitizer import sanitize_capture

    clean = sanitize_capture({
        "permalink": note["permalink"], "path": note["path"], "title": note["title"],
        "status": note["status"], "text": note["content"], "relations": relations,
    }, "document").value
    clean["version"] = _version(note, relations)
    clean["ref"] = {"reader": "basic-memory.read_note", "project": "main",
                    "identifier": note["permalink"]}
    return clean


def _summary(note):
    return {key: note[key] for key in ("permalink", "path", "title", "status", "version", "ref")}


def _similar(left, right):
    shared = len(left & right)
    return shared / len(left | right) if shared >= 4 else 0


def _clusters(notes, tokenize):
    terms = [set(tokenize(note["text"])) for note in notes]
    scores = {}
    for left, right in combinations(range(len(notes)), 2):
        if notes[left]["text"].strip() and notes[left]["text"] == notes[right]["text"]:
            scores[left, right] = 1.0
        else:
            scores[left, right] = _similar(terms[left], terms[right])
    clusters = []
    for index in range(len(notes)):
        # Complete-link membership prevents a single bridging note from hiding
        # two notes whose actual content is materially different.
        matching = next((members for members in clusters
                         if all(scores[member, index] >= SIMILARITY for member in members)), None)
        if matching is None:
            clusters.append([index])
        else:
            matching.append(index)
    return [[notes[index] for index in group] for group in clusters if len(group) > 1]


def _candidate(notes, workspace):
    strengths = {"accepted": 3, "verified": 2, "experimental": 1}
    representative = min(notes, key=lambda n: (-strengths.get(n["status"], 0),
                                                -len(n["text"]), n["permalink"]))
    group_id = _identity("group", [workspace, sorted(note["permalink"] for note in notes)])
    revision = _identity("revision", [group_id, sorted(note["version"] for note in notes)])
    bodies = [[(number, line) for number, line in enumerate(note["text"].splitlines(), 1)
               if line.strip()] for note in notes]
    common = Counter(line for _, line in bodies[0])
    for body in bodies[1:]:
        common &= Counter(line for _, line in body)
    members = []
    edges = {}
    for note, body in zip(notes, bodies, strict=True):
        remaining = common.copy()
        differences = []
        for number, line in body:
            if remaining[line]:
                remaining[line] -= 1
            else:
                differences.append({"line": number, "text": line[:512], "truncated": len(line) > 512})
        for relation in note["relations"]:
            edges[_json(relation)] = relation
        members.append({**_summary(note), "differences": differences[:24],
                        "difference_lines": len(differences),
                        "omitted_difference_lines": max(0, len(differences) - 24)})
    relations = list(edges.values())
    return {"id": group_id, "revision": revision, "representative": representative["permalink"],
            "members": members, "relations": relations[:24], "relation_count": len(relations),
            "omitted_relations": max(0, len(relations) - 24), "shared_lines": sum(common.values()),
            "has_declared_contradiction": any(edge["kind"] == "contradicts" for edge in relations),
            "recovery": {"action": "undo", "group_id": group_id, "revision": revision}}


def _latest(db, workspace, group_id, *, exists):
    if not exists:
        return None
    row = db.execute("""SELECT group_id,revision,state,representative,at FROM note_consolidation_events
        WHERE workspace=? AND group_id=? ORDER BY sequence DESC LIMIT 1""",
        (workspace, group_id)).fetchone()
    return dict(row) if row else None


def _change(db, workspace, group_id, revision, action, *, candidate=None, representative=None):
    state = "applied" if action == "apply" else "undone"
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(DECISIONS)
        previous = _latest(db, workspace, group_id, exists=True)
        same_revision = previous is not None and previous["revision"] == revision
        if (action == "apply" and candidate is None) or (action == "undo" and not same_revision):
            return {"schema": SCHEMA, "status": "source_changed", "group_id": group_id, "revision": revision}
        if representative is None:
            representative = previous["representative"] if same_revision else candidate["representative"]
        if same_revision and previous["state"] == state and previous["representative"] == representative:
            return {"schema": SCHEMA, "status": "recorded", **previous}
        timestamp = time.time()
        db.execute("""INSERT INTO note_consolidation_events
            (workspace,group_id,revision,state,representative,at) VALUES (?,?,?,?,?,?)""",
                   (workspace, group_id, revision, state, representative, timestamp))
        return {"schema": SCHEMA, "status": "recorded", "group_id": group_id,
                "revision": revision, "state": state, "representative": representative, "at": timestamp}


def _review_notes(catalog, related, identifiers, query, tokenize):
    from capture_sanitizer import CaptureError, require_safe_identity

    exclusions = []
    if identifiers is not None:
        selected = []
        for identifier in identifiers:
            matches = related.resolve(identifier)
            if len(matches) != 1:
                exclusions.append({"identifier": identifier, "reason": "ambiguous" if matches else "unavailable"})
            elif matches[0] not in selected:
                selected.append(matches[0])
    else:
        terms = set(tokenize(query))
        if not terms:
            raise ValueError("Query contains no supported terms.")
        scored = []
        for note in catalog:
            score = len(terms & set(tokenize(note["title"] + "\n" + note["content"][:BODY_LIMIT])))
            if score:
                scored.append((-score, note["permalink"], note["path"], note))
        selected = [item[3] for item in sorted(scored, key=lambda item: item[:3])[:NOTE_LIMIT]]
    counts = Counter(note["permalink"] for note in catalog)
    result = []
    for note in selected:
        try:
            require_safe_identity(note["permalink"])
        except CaptureError:
            exclusions.append({"reason": "note_identity_excluded_by_capture_policy"})
            continue
        if counts[note["permalink"]] != 1:
            exclusions.append({"identifier": note["permalink"], "reason": "ambiguous_permalink"})
        elif len(note["content"].encode("utf-8")) > BODY_LIMIT:
            exclusions.append({"identifier": note["permalink"], "reason": "note_body_limit"})
        else:
            result.append(note)
    return sorted(result, key=lambda note: note["permalink"]), exclusions


def _bound(report, budget):
    while len(_json(report).encode("utf-8")) > budget:
        groups = report["groups"]
        available = [member for group in groups for member in group["members"] if member["differences"]]
        if available:
            member = max(available, key=lambda n: len(_json(n["differences"])))
            member["differences"].pop()
            member["omitted_difference_lines"] += 1
            continue
        with_edges = [group for group in groups if group["relations"]]
        if with_edges:
            group = max(with_edges, key=lambda item: len(item["relations"]))
            group["relations"].pop()
            group["omitted_relations"] += 1
            continue
        raise ValueError("Consolidation metadata exceeds the response budget. Select fewer notes or increase it.")
    return report


def consolidation(memory, *, scope, action="review", identifiers=None, query=None, group_id=None,
                  revision=None, representative=None, identifier=None, note_version=None,
                  start_line=1, limit=80, budget=32768):
    from memory_runtime import NoteRelations, scope_matches, tokens

    if not isinstance(scope, str) or not Path(scope).is_absolute():
        raise ValueError("Consolidation requires an exact workspace.")
    if action not in ("review", "apply", "undo", "open") or type(budget) is not int or not 2048 <= budget <= 65536:
        raise ValueError("Invalid consolidation action or response budget.")
    workspace = _scope(scope)
    if action in ("apply", "undo"):
        group_id = _text(group_id, "group ID", 128)
        revision = _text(revision, "group revision", 128)
    if representative is not None:
        if action != "apply":
            raise ValueError("Choose a representative only when applying a candidate.")
        representative = _text(representative, "representative identifier")
    with memory.lock:
        if action == "undo":
            return _change(memory.db, workspace, group_id, revision, action)
        all_notes = memory.notes()
        catalog = [note for note in all_notes if _scope(note["scope"]) == workspace]
        selected_index = NoteRelations(catalog)
        related = NoteRelations([note for note in all_notes if scope_matches(note["scope"], scope)])
        if action == "open":
            identity = _text(identifier, "note identifier")
            version = _text(note_version, "note version", 128)
            if type(start_line) is not int or start_line < 1 or type(limit) is not int or not 1 <= limit <= 400:
                raise ValueError("Invalid note line page.")
            matches = selected_index.resolve(identity)
            if len(matches) != 1:
                return {"schema": SCHEMA, "status": "unavailable", "identifier": identity}
            note = matches[0]
            if len(note["content"].encode("utf-8")) > BODY_LIMIT:
                return {"schema": SCHEMA, "status": "source_changed", "identifier": identity}
            snapshot = _note_snapshot(note, related.by_note.get(note["path"], []))
            if snapshot["version"] != version:
                return {"schema": SCHEMA, "status": "source_changed", "identifier": identity}
            lines = snapshot["text"].splitlines(keepends=True)
            position = start_line - 1
            end = min(position + limit, len(lines))
            report = {"schema": SCHEMA, "status": "observed", "source": _summary(snapshot),
                      "content": "", "start_line": start_line, "next_line": None, "total_lines": len(lines)}
            while end > position:
                report.update(content="".join(lines[position:end]), next_line=end + 1 if end < len(lines) else None)
                if len(_json(report).encode("utf-8")) <= budget:
                    return report
                end -= 1
            if position < len(lines):
                raise ValueError("A complete source line exceeds the response budget.")
            return report

        if (identifiers is None) == (query is None):
            raise ValueError("Select exact note identifiers or one text query.")
        if identifiers is not None:
            if not isinstance(identifiers, list) or not 2 <= len(identifiers) <= NOTE_LIMIT:
                raise ValueError("Select 2–12 note identifiers.")
            identifiers = [_text(identity, "note identifier") for identity in identifiers]
            if len(set(identifiers)) != len(identifiers):
                raise ValueError("Note identifiers must be unique.")
        else:
            query = _text(query, "query", 1024)
        selected, exclusions = _review_notes(catalog, selected_index, identifiers, query, tokens)
        snapshots = [_note_snapshot(note, related.by_note.get(note["path"], [])) for note in selected]
        groups = [_candidate(notes, workspace) for notes in _clusters(snapshots, tokens)]
        if action == "apply":
            candidate = next((group for group in groups if group["id"] == group_id
                              and group["revision"] == revision), None)
            if candidate and representative is not None and not any(
                    member["permalink"] == representative for member in candidate["members"]):
                return {"schema": SCHEMA, "status": "invalid_representative", "group_id": group_id}
            return _change(memory.db, workspace, group_id, revision, action,
                           candidate=candidate, representative=representative)
        decisions_exist = memory.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='note_consolidation_events'").fetchone()
        display = []
        folded = set()
        for group in groups:
            previous = _latest(memory.db, workspace, group["id"], exists=decisions_exist)
            group["state"] = ("proposed" if previous is None else
                              previous["state"] if previous["revision"] == group["revision"] else "stale")
            group["last_decision"] = previous
            if group["state"] == "applied":
                members = [item["permalink"] for item in group["members"]]
                group["representative"] = previous["representative"]
                representative_ref = next(item["ref"] for item in group["members"]
                                          if item["permalink"] == group["representative"])
                folded.update(members)
                display.append({"kind": "consolidated", "group_id": group["id"], "revision": group["revision"],
                                "representative": group["representative"], "representative_ref": representative_ref,
                                "has_declared_contradiction": group["has_declared_contradiction"],
                                "difference_lines": sum(item["difference_lines"] for item in group["members"]),
                                "members": members})
        display.extend({"kind": "note", **_summary(note)} for note in snapshots if note["permalink"] not in folded)
        return _bound({"schema": SCHEMA, "status": "reviewed", "workspace": scope, "groups": groups,
                       "display": display, "excluded": exclusions, "candidate_limit": NOTE_LIMIT,
                       "selection": "explicit_identifiers" if identifiers is not None else "bounded_project_text_overlap",
                       "effect": "reviewed_presentation_grouping"}, budget)
