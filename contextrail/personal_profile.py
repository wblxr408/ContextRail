"""Source-bound personal defaults in the existing Markdown memory owner."""

from collections import defaultdict
from pathlib import Path
import os
import unicodedata
from uuid import NAMESPACE_URL, uuid5


SCHEMA = "contextrail.personal-profile/v1"
MAX_ENTRIES = 8


def _scope(value):
    return os.path.normcase(str(value)).replace("\\", "/").rstrip("/")


def _topic(value):
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _text(value, label, limit):
    if (not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > limit
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise ValueError(f"Provide a bounded {label}.")
    return value.strip()


def _select(memory, workspace):
    from memory_runtime import date_value, retained_event

    groups = defaultdict(list)
    decisions = set()
    for note in memory.notes():
        if note["status"] in {"archived", "superseded", "expired", "disputed", "rejected"}:
            continue
        event = retained_event(note)
        if event is None:
            continue
        event_id, payload = event
        for index, claim in enumerate(payload["claims"]):
            claim_scope = claim.get("scope")
            if claim["source"] != "user" or not isinstance(claim_scope, str):
                continue
            local = _scope(claim_scope) == workspace
            global_scope = claim_scope == "global" and claim.get("profile_scope") == "global"
            if not local and not global_scope:
                continue
            topic = _topic(claim["subject"])
            if not topic or len(topic)>128:
                continue
            if local and claim.get("kind") == "decision":
                # The captured user quotation can suppress a default without
                # adopting the classifier's paraphrased decision as a rule.
                decisions.add(topic)
                continue
            if claim.get("kind") != "preference" or claim.get("authority") is not True:
                continue
            action = claim.get("profile_action", "set")
            if action not in {"set", "revoke"}:
                continue
            row = {
                "subject": claim["subject"][:128], "topic": topic[:128],
                "value": claim["quote"][:384], "quote": claim["quote"][:384],
                "truncated": len(claim["quote"]) > 384,
                "scope": "project" if local else "global", "action": action,
                "source_role": "user", "event_id": event_id,
                "ref": {"reader": "basic-memory.read_note", "project": "main", "identifier": note["permalink"]},
            }
            observed = (date_value(payload.get("occurred_at") or payload.get("recorded_at")),
                        payload.get("recorded_at") or "", event_id, index)
            groups[(topic, row["scope"])].append((observed, row))
    entries = []
    withheld = {"project_decision": 0, "revoked": 0}
    for topic in sorted({topic for topic, _ in groups}):
        if topic in decisions:
            withheld["project_decision"] += 1
            continue
        choices = groups.get((topic, "project")) or groups[(topic, "global")]
        chosen = max(choices, key=lambda item: item[0])[1]
        if chosen["action"] == "revoke":
            withheld["revoked"] += 1
            continue
        entries.append(chosen)
    entries.sort(key=lambda row: (row["scope"] != "project", row["topic"]))
    return entries, withheld


def personal_profile(memory, *, scope, action="view", record_scope="project", event_id=None,
                     subject=None, statement=None, limit=MAX_ENTRIES):
    from capture_sanitizer import require_safe_identity

    if not isinstance(scope, str) or not Path(scope).is_absolute():
        raise ValueError("A personal profile requires an exact project workspace.")
    if action == "view":
        if type(limit) is not int or not 1 <= limit <= MAX_ENTRIES:
            raise ValueError("Personal profile limit must be 1–8.")
        with memory.lock:
            entries, withheld = _select(memory, _scope(scope))
        return {"schema": SCHEMA, "status": "observed", "entries": entries[:limit],
                "omitted": max(0, len(entries) - limit), "withheld": withheld,
                "priority": "defaults_after_current_request_and_project_decisions",
                "trust": "user_history_defaults"}
    if action not in {"set", "revoke"} or record_scope not in {"global", "project"}:
        raise ValueError("Choose a profile action and explicit scope.")
    event_id = _text(event_id, "event ID", 128)
    subject = _text(subject, "preference subject", 128)
    statement = _text(statement, "user statement", 512)
    for value in (event_id, subject, statement):
        require_safe_identity(value)
    claim_scope = "global" if record_scope == "global" else scope
    identity = "profile:" + uuid5(NAMESPACE_URL, _scope(claim_scope) + "\n" + event_id).hex
    claim = {"subject": subject, "predicate": "preference", "value": statement, "quote": statement,
             "source": "user", "kind": "preference", "authority": True, "scope": claim_scope,
             "profile_scope": record_scope, "profile_action": action}
    result = memory.retain(identity, [claim], {"user": statement})
    return {**result, "schema": SCHEMA, "action": action, "profile_scope": record_scope}
