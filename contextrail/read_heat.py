"""Rank snapshot evidence from the original host-observed read receipts."""

from collections import defaultdict
import json

from .errors import BudgetExceeded, InvalidRequest
from .models import Ref, canonical, integer


DAY = 86400
HISTORY_DAYS = 90
HALF_LIFE_DAYS = 7
REVIEW_AFTER_DAYS = 21
RECEIPT_LIMIT = 2000


def _ref_key(ref):
    return ref.name, ref.revision


def _overlaps(left, right):
    return (_ref_key(left) == _ref_key(right)
            and (left.end is None or right.start < left.end)
            and (right.end is None or left.start < right.end)
            and (left.end is None or left.start < left.end)
            and (right.end is None or right.start < right.end))


class ReadHeat:
    """One bounded, scoped view of application-layer delivery metadata."""

    def __init__(self, store, scope):
        self.now = store.clock()
        rows = store.db.execute("""
            SELECT sequence, at, metadata FROM events
            WHERE scope=? AND kind='telemetry.tool' AND at BETWEEN ? AND ?
                AND json_extract(metadata,'$.status')='succeeded'
                AND (json_extract(metadata,'$.readback_bytes')>0
                     OR json_array_length(metadata,'$.delivered_reads')>0)
            ORDER BY sequence DESC LIMIT ?
        """, (scope.key, self.now - HISTORY_DAYS * DAY, self.now, RECEIPT_LIMIT + 1)).fetchall()
        self.truncated = len(rows) > RECEIPT_LIMIT
        self.receipts = rows[:RECEIPT_LIMIT]
        self.windows = defaultdict(list)
        self.stats = {}
        for row in self.receipts:
            value = json.loads(row["metadata"])
            reads = value.get("delivered_reads", []) if value.get("tool") == "context.batch" else []
            if value.get("tool") == "context.get":
                reads = [value]
            for read in reads:
                byte_count = read.get("readback_bytes")
                if type(byte_count) is not int or byte_count <= 0 or not isinstance(read.get("ref"), dict):
                    continue
                try:
                    ref = Ref(**read["ref"])
                except (InvalidRequest, TypeError):
                    continue
                end = ref.start + byte_count
                if ref.end is not None and end != ref.end:
                    continue
                window = Ref(ref.name, ref.revision, ref.start, end)
                self.windows[_ref_key(ref)].append((row["sequence"], row["at"], window))

    def for_ref(self, ref):
        if ref not in self.stats:
            times = {sequence: at for sequence, at, window in self.windows.get(_ref_key(ref), ())
                     if _overlaps(ref, window)}
            score = sum(2 ** ((at - self.now) / (HALF_LIFE_DAYS * DAY)) for at in times.values())
            self.stats[ref] = (len(times), max(times.values(), default=None), score)
        return self.stats[ref]

    def entry_key(self, entry, pinned=frozenset()):
        ref = Ref(**entry["ref"])
        heat = 0 if entry["required"] or ref in pinned else self.for_ref(ref)[2]
        return (not entry.get("cache_stable", False), not entry["required"], -entry["priority"],
                -heat, canonical(entry["ref"]))


def group_components(groups):
    """Join groups that share evidence so ranked prefetch cannot split them."""
    membership = defaultdict(list)
    for index, (_, refs) in enumerate(groups):
        for ref in refs:
            membership[ref].append(index)
    pending = set(range(len(groups)))
    components = []
    for start in range(len(groups)):
        if start not in pending:
            continue
        stack, joined, refs = [start], [], {}
        pending.remove(start)
        while stack:
            group, members = groups[stack.pop()]
            joined.append(group)
            for ref in members:
                if ref in refs:
                    continue
                refs[ref] = None
                for index in membership[ref]:
                    if index in pending:
                        pending.remove(index)
                        stack.append(index)
        components.append((
            {"id": min(group["id"] for group in joined),
             "required": any(group["required"] for group in joined),
             "priority": max(group.get("priority", 0) for group in joined)},
            tuple(refs),
        ))
    return components


def required_group_refs(state):
    required = {Ref(**entry["ref"]) for entry in state["evidence"] if entry["required"]}
    groups = [(group, tuple(Ref(**ref) for ref in group["members"]))
              for group in state.get("selection_groups", ())]
    for group, members in group_components(groups):
        if group["required"] or not required.isdisjoint(members):
            required.update(members)
    return required


def _bridge_authority(ref):
    if not ref.name.startswith("bridge/") or "/exports/" not in ref.name:
        return False
    name = ref.name.split("/exports/", 1)[1].partition("/")[2]
    return name in {"manifest", "ledger", "resume-intent"} or name.startswith("sources/")


def read_heat(store, scope, snapshot, *, offset=0, limit=20, budget=8192):
    integer(offset)
    integer(limit, 1)
    integer(budget, 1)
    if limit > 100:
        raise InvalidRequest("Read heat page size exceeds 100.")
    with store.read_transaction():
        state = store.load_snapshot(scope, snapshot)
        heat = ReadHeat(store, scope)
        required = required_group_refs(state)
        group_members = {Ref(**ref) for group in state.get("selection_groups", ())
                         for ref in group["members"]}
        active_sources = [Ref(**ref) for item in state.get("task_state", ()) if item["status"] == "active"
                          for ref in item["sources"]]
        protected = defaultdict(set)
        for reason, refs in (("required_evidence", required), ("selection_group", group_members),
                             ("active_task_source", active_sources)):
            for ref in refs:
                protected[_ref_key(ref)].add((reason, ref))

        entries = sorted(state["evidence"], key=heat.entry_key)
        report = {
            "schema": "contextrail.read-heat/v1", "snapshot": snapshot, "offset": offset,
            "total": len(entries), "entries": [], "next_offset": None,
            "basis": {"signal": "host_application_readback", "lookback_days": HISTORY_DAYS,
                      "half_life_days": HALF_LIFE_DAYS, "review_after_days": REVIEW_AFTER_DAYS,
                      "receipts_inspected": len(heat.receipts), "older_receipts_omitted": heat.truncated},
        }
        if len(canonical(report).encode("utf-8")) > budget:
            raise BudgetExceeded("Read heat metadata exceeds the response budget.")
        for entry in entries[offset:offset + limit]:
            ref = Ref(**entry["ref"])
            count, latest, score = heat.for_ref(ref)
            older = latest is not None and heat.now - latest >= REVIEW_AFTER_DAYS * DAY
            reasons = {reason for reason, other in protected.get(_ref_key(ref), ())
                       if ref == other or _overlaps(ref, other)}
            if _bridge_authority(ref):
                reasons.add("bridge_authority")
            if reasons:
                suggestion = "protected"
            elif older and not heat.truncated:
                suggestion = "review_for_deprioritization_or_merge"
            else:
                suggestion = None
            report["entries"].append({
                "ref": entry["ref"], "priority": entry["priority"],
                "observed_reads": count if count else None, "last_read_at": latest,
                "score": round(score, 8) if count else None,
                "heat_state": "observed_older" if older else "observed_recent" if count else "unobserved",
                "suggestion": suggestion, "protection_reasons": sorted(reasons),
            })
            position = offset + len(report["entries"])
            report["next_offset"] = position if position < len(entries) else None
            if len(canonical(report).encode("utf-8")) > budget:
                report["entries"].pop()
                if not report["entries"]:
                    raise BudgetExceeded("A complete read heat entry exceeds the response budget.")
                report["next_offset"] = position - 1
                break
    return report
