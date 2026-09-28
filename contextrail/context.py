"""Deterministic, recoverable context selection; no summary/model calls."""

from dataclasses import asdict
from typing import Callable

from .errors import AccessDenied, BudgetExceeded, InvalidRequest
from .models import CacheLayout, IndexPolicy, Packet, Ref, Scope, canonical, digest, identifier, integer
from .store import Store

# 确定的、可恢复的上下文选择；不进行摘要/模型调用。
class Compiler:
    def __init__(self, store: Store, *, measure: Callable[[str], int] | None = None,
                 unit: str = "utf8_bytes"):
        identifier(unit)
        if measure is None and unit != "utf8_bytes":
            raise InvalidRequest("A custom budget unit requires an explicit measurement function.")
        self.store = store
        self.measure = measure if measure is not None else lambda value: len(value.encode("utf-8"))
        self.unit = unit

    def _size(self, text: str) -> int:
        size = self.measure(text)
        integer(size)
        return size

    def compile(self, scope: Scope, snapshot: str, session: str, *, budget: int,
                requested: tuple[Ref, ...] = (), layout: CacheLayout | None = None,
                index: IndexPolicy | None = None) -> Packet:
        """Requested pages are mandatory for this view, without mutating the snapshot.

        The budget covers the entire rendered JSON body, including the cold index.
        It excludes host system prompts, other messages, tools and output reserve.
        """
        with self.store.read_transaction():
            return self._compile(scope, snapshot, session, budget=budget, requested=requested,
                                 layout=layout, index=index)

    def _compile(self, scope: Scope, snapshot: str, session: str, *, budget: int,
                 requested: tuple[Ref, ...], layout: CacheLayout | None,
                 index: IndexPolicy | None) -> Packet:
        integer(budget, 1)
        if layout is not None and not isinstance(layout, CacheLayout):
            raise InvalidRequest("Cache layout must be a CacheLayout value.")
        if index is not None and not isinstance(index, IndexPolicy):
            raise InvalidRequest("Cold index policy must be an IndexPolicy value.")
        reserve = layout.reserve if layout is not None else 0
        if reserve >= budget:
            raise BudgetExceeded("Configured output, tool and history reserves leave no context budget.")
        context_budget = budget - reserve
        if not isinstance(requested, (tuple, list)) or any(not isinstance(r, Ref) for r in requested):
            raise InvalidRequest("Requested pages must be evidence references.")
        if len(set(requested)) != len(requested):
            raise InvalidRequest("Duplicate requested pages.")
        target = self.store.session(scope, session)
        state = self.store.load_snapshot(scope, snapshot)
        entries = state.pop("evidence")
        groups = state.pop("selection_groups", None)
        # Stable evidence must be required (validated by Selection) and stays at
        # the beginning of the packet. Dynamic task data and cold navigation are
        # rendered afterwards, preserving a provider-cache-friendly prefix.
        entries.sort(key=lambda e: (not e.get("cache_stable", False), not e["required"],
                                    -e["priority"], canonical(e["ref"])))
        refs = {Ref(**e["ref"]) for e in entries}
        if any(r not in refs for r in requested):
            raise AccessDenied("Requested evidence is outside this snapshot.")
        if groups:
            return self._compile_grouped(scope, snapshot, target, state, entries, groups,
                                         requested=requested, budget=budget, context_budget=context_budget,
                                         layout=layout, index=index, reserve=reserve)
        forced = set(requested)
        selected: list[dict] = []
        omitted: list[dict] = []
        optional: list[dict] = []
        for entry in entries:
            if entry["required"] or Ref(**entry["ref"]) in forced:
                try:
                    selected.append(self._load(scope, entry))
                except InvalidRequest:
                    # A required/requested page must be injectable text. Binary or
                    # unaligned-UTF-8 evidence cannot be, so state that constraint
                    # explicitly instead of surfacing the generic decode error.
                    ref = Ref(**entry["ref"])
                    raise InvalidRequest(
                        f"Required evidence {ref.name}@{ref.revision} is not UTF-8 text and cannot be "
                        "injected into the context packet; read it as a binary cold page via context.get "
                        "or mark the selection optional.") from None
            else:
                optional.append(entry)
                omitted.append(entry)

        def render() -> str:
            return self._render(scope, snapshot, target, state, selected, omitted, layout, index)

        body = render()
        if self._size(body) > context_budget:
            raise BudgetExceeded("Mandatory evidence and navigation exceed the context budget; nothing was silently dropped.")
        for entry in optional:
            try:
                page = self._load(scope, entry)
            except InvalidRequest:
                # Binary/unaligned optional pages remain exactly recoverable through context.get.
                continue
            selected.append(page)
            original_index = omitted.index(entry)
            omitted.remove(entry)
            candidate = render()
            if self._size(candidate) <= context_budget:
                body = candidate
            else:
                selected.pop()
                omitted.insert(original_index, entry)
        # Re-render after selection to ensure metadata ordering and accounting match exactly.
        body = render()
        stable_digest = None
        if layout is not None:
            stable_digest = digest(canonical({"scope": asdict(scope),
                                              "stable_evidence": [entry for entry in selected
                                                                  if entry.get("cache_stable", False)]}).encode("utf-8"))
        return Packet(snapshot, target, body, digest(body.encode("utf-8")), self._size(body), budget,
                      self.unit, tuple(Ref(**e["ref"]) for e in selected), tuple(Ref(**e["ref"]) for e in omitted),
                      reserve, layout.cache_key if layout else None, stable_digest)

    def _render(self, scope: Scope, snapshot: str, target, state: dict, selected: list[dict],
                omitted: list[dict], layout: CacheLayout | None, index: IndexPolicy | None) -> str:
        # Artifact content is explicitly data. Hosts must not promote it to system instructions.
        if layout is None and index is None:
            # Preserve the v1 packet exactly for hosts that have not opted in
            # to cache layout or a bounded cold index.
            return canonical({"schema": "contextrail.context/v1", "snapshot": snapshot,
                              "scope": asdict(scope), "target": asdict(target),
                              "task": state, "evidence": selected, "available": omitted,
                              "read_tool": "context.get", "range_unit": "bytes"})
        preview = omitted if index is None else omitted[:index.max_entries]
        stable = [entry for entry in selected if entry.get("cache_stable", False)]
        dynamic = [entry for entry in selected if not entry.get("cache_stable", False)]
        # Only bytes that precede dynamic task state belong to the stable
        # cache identity.  A task-version change must not invalidate an
        # otherwise identical approved evidence prefix.
        cache = None if layout is None else {
            "key": layout.cache_key,
            "stable_digest": digest(canonical({"scope": asdict(scope),
                                                "stable_evidence": stable}).encode("utf-8")),
            "reserves": {"output": layout.output_reserve, "tool": layout.tool_reserve,
                         "history": layout.history_reserve},
        }
        # json.dumps preserves insertion order. Keeping static data first is
        # intentional: it gives a host a stable byte prefix without claiming
        # that unrelated providers share a cache implementation.
        pieces = [
            ('schema', 'contextrail.context/v2'), ('cache', cache), ('scope', asdict(scope)),
            ('stable_evidence', stable), ('task', state), ('evidence', dynamic),
            ('available', preview), ('available_total', len(omitted)),
            ('available_truncated', len(preview) != len(omitted)), ('read_tool', 'context.get'),
            ('list_tool', 'context.list'), ('range_unit', 'bytes'), ('snapshot', snapshot),
            ('target', asdict(target)),
        ]
        return "{" + ",".join(canonical(key) + ":" + canonical(value) for key, value in pieces) + "}"

    def _compile_grouped(self, scope: Scope, snapshot: str, target, state: dict, entries: list[dict],
                         groups: list[dict], *, requested: tuple[Ref, ...], budget: int, context_budget: int,
                         layout: CacheLayout | None, index: IndexPolicy | None, reserve: int) -> Packet:
        """Compile a snapshot with atomic group semantics.

        A required group loads every member or fails (never partial); a
        requested ref forces its whole containing group required for this view;
        an optional group loads whole or stays entirely cold.  After selection,
        every included group's members are verified present, so a group can
        never appear half-loaded in the packet.
        """
        by_ref = {Ref(**entry["ref"]): entry for entry in entries}
        parsed = [(group, tuple(Ref(**ref) for ref in group["members"])) for group in groups]
        forced = set(requested)
        # A requested ref pins every group that contains it: recovering one
        # member must bring its whole dependency closure, not an isolated span.
        forced_group_ids = {group["id"] for group, members in parsed if forced & set(members)}

        def is_required(group: dict) -> bool:
            return bool(group.get("required")) or group["id"] in forced_group_ids

        grouped_refs = {ref for _, members in parsed for ref in members}
        # Evidence not bound to any group keeps its individual selection
        # semantics (required stays hot, optional may be packed later).
        ungrouped = [entry for entry in entries if Ref(**entry["ref"]) not in grouped_refs]

        selected: list[dict] = []
        omitted: list[dict] = []
        loaded_refs: set[Ref] = set()

        def load_member(ref: Ref) -> dict:
            entry = by_ref[ref]
            try:
                return self._load(scope, entry)
            except InvalidRequest:
                raise InvalidRequest(
                    f"Required evidence {ref.name}@{ref.revision} is not UTF-8 text and cannot be injected "
                    "into the context packet; read it as a binary cold page via context.get.") from None

        # 1. Required (and requested-pinned) groups are mandatory and atomic.
        for group, members in parsed:
            if not is_required(group):
                continue
            for ref in members:
                if ref in loaded_refs:
                    continue
                selected.append(load_member(ref))
                loaded_refs.add(ref)
        # Individually-required ungrouped evidence stays hot as before.
        for entry in ungrouped:
            ref = Ref(**entry["ref"])
            if entry["required"] or ref in forced:
                if ref not in loaded_refs:
                    selected.append(load_member(ref))
                    loaded_refs.add(ref)
            else:
                omitted.append(entry)

        def render() -> str:
            return self._render(scope, snapshot, target, state, selected, omitted, layout, index)

        body = render()
        if self._size(body) > context_budget:
            raise BudgetExceeded("Mandatory groups and navigation exceed the context budget; no group was split.")

        # 2. Optional groups, highest priority first, load whole or not at all.
        optional_groups = sorted((item for item in parsed if not is_required(item[0])),
                                 key=lambda item: (-item[0].get("priority", 0), item[0]["id"]))
        for group, members in optional_groups:
            new_members = [ref for ref in members if ref not in loaded_refs]
            snapshot_selected = list(selected)
            snapshot_omitted = list(omitted)
            try:
                pages = [load_member(ref) for ref in new_members]
            except InvalidRequest:
                # A non-injectable member keeps the whole optional group cold;
                # its bytes stay exactly recoverable through context.get.
                continue
            for page in pages:
                selected.append(page)
            group_ref_set = set(members)
            omitted[:] = [entry for entry in omitted if Ref(**entry["ref"]) not in group_ref_set]
            candidate = render()
            if self._size(candidate) <= context_budget:
                body = candidate
                loaded_refs.update(new_members)
            else:
                # Reject the whole group atomically; restore prior selection.
                selected[:] = snapshot_selected
                omitted[:] = snapshot_omitted
        # Any grouped ref that never loaded is a cold, recoverable candidate.
        for group, members in parsed:
            for ref in members:
                if ref not in loaded_refs and by_ref[ref] not in omitted:
                    omitted.append(by_ref[ref])
        body = render()

        # 3. Completeness gate: every included group must be whole.  A member
        # missing from the final included set is a compiler bug, never a
        # silently accepted partial group.
        included = {Ref(**entry["ref"]) for entry in selected}
        for group, members in parsed:
            present = [ref for ref in members if ref in included]
            if present and len(present) != len(members):
                raise BudgetExceeded(
                    f"Group {group['id']} would be partially loaded; groups load whole or stay cold.")

        stable_digest = None
        if layout is not None:
            stable_digest = digest(canonical({"scope": asdict(scope),
                                              "stable_evidence": [entry for entry in selected
                                                                  if entry.get("cache_stable", False)]}).encode("utf-8"))
        return Packet(snapshot, target, body, digest(body.encode("utf-8")), self._size(body), budget,
                      self.unit, tuple(Ref(**e["ref"]) for e in selected), tuple(Ref(**e["ref"]) for e in omitted),
                      reserve, layout.cache_key if layout else None, stable_digest)

    def _load(self, scope: Scope, entry: dict) -> dict:
        artifact = self.store.get(scope, Ref(**entry["ref"]))
        try:
            text = artifact.content.decode("utf-8")
        except UnicodeDecodeError:
            raise InvalidRequest("Context evidence must be UTF-8 text with byte ranges aligned to characters.") from None
        return {**entry, "media_type": artifact.media_type, "trust": "untrusted_evidence", "text": text}

    def for_handoff(self, scope: Scope, handoff_id: str, *, budget: int,
                    requested: tuple[Ref, ...] = (), layout: CacheLayout | None = None,
                    index: IndexPolicy | None = None) -> Packet:
        h = self.store.handoff(scope, handoff_id)
        packet = self.compile(scope, h.snapshot, h.target.session, budget=budget, requested=requested,
                              layout=layout, index=index)
        self.store.bind_packet(scope, handoff_id, packet.sha256)
        return packet
