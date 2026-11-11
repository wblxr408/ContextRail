"""Use the original memory profile as an optional, low-priority briefing field."""

import re
import time
import unicodedata

from .models import canonical
from .query_sources import ExistingMemory, SourceFailure


def _normalized(value):
    return unicodedata.normalize("NFKC", value).casefold()


def load_profile(binding, state, *, prompt=None, seconds=2):
    if binding is None or not binding.personal_profile:
        return None
    try:
        value = ExistingMemory(binding).invoke(
            "personal_profile", [], {"scope": binding.workspace},
            time.monotonic() + min(seconds, binding.timeout_seconds))
        if (not isinstance(value, dict) or value.get("schema") != "contextrail.personal-profile/v1"
                or not isinstance(value.get("entries"), list) or not isinstance(value.get("withheld"), dict)):
            raise SourceFailure("invalid_profile")
    except SourceFailure:
        return {"status": "unavailable", "entries": [], "priority": "low_priority_user_history"}
    excluded = {_normalized(topic) for topic in binding.profile_exclude}
    current = [state.get("objective", ""), *state.get("constraints", ()), *state.get("acceptance", ())]
    current.extend(item.get("text", "") for item in state.get("task_state", ())
                   if item.get("status") == "active" and item.get("kind") in {"constraint", "decision", "excluded"}
                   and not item.get("inferred"))
    if isinstance(prompt, str):
        current.append(prompt[:8192])
    context = "\n".join(_normalized(text) for text in current if isinstance(text, str))
    entries = []
    blocked = 0
    for row in value["entries"]:
        if not isinstance(row, dict) or row.get("source_role") != "user":
            continue
        topic = row.get("topic")
        if not isinstance(topic, str) or not topic:
            continue
        topic = _normalized(topic)
        pattern = r"(?<![a-z0-9_])" + re.escape(topic) + r"(?![a-z0-9_])"
        if topic in excluded or re.search(pattern, context):
            blocked += 1
        else:
            entries.append(row)
    return {**value, "entries": entries, "withheld": {**value.get("withheld", {}),
                                                     "current_or_host_rule": blocked}}


def attach_profile(envelope, binding, state, budget):
    profile = load_profile(binding, state)
    if profile is None:
        return envelope
    envelope["personal_profile"] = profile
    while len(canonical(envelope).encode("utf-8")) > budget and profile["entries"]:
        profile["entries"].pop()
        profile["omitted"] = profile.get("omitted", 0) + 1
    if len(canonical(envelope).encode("utf-8")) > budget:
        envelope.pop("personal_profile")
    return envelope
