"""Per-turn context governance for hosts that own the model request loop.

The controller is deliberately provider-neutral: a host supplies units for its
system prompt and tool schema, while ContextRail compiles the mutable evidence
slot and records controlled cold-page recovery.  It does not assume a model
remembers any previous request.
"""

from __future__ import annotations

from dataclasses import dataclass

from .context import Compiler
from .errors import BudgetExceeded, InvalidRequest
from .models import CacheLayout, IndexPolicy, Packet, Ref, Scope, integer


_PHASES = frozenset({"explore", "locate", "modify", "verify", "handoff"})


@dataclass(frozen=True)
class RequestBudget:
    """Budget for the final outbound request, not merely a ContextRail packet."""

    context_window: int
    system_units: int = 0
    protocol_units: int = 0
    output_reserve: int = 0
    tool_reserve: int = 0
    safety_reserve: int = 0

    def __post_init__(self) -> None:
        for value in (self.context_window, self.system_units, self.protocol_units, self.output_reserve,
                      self.tool_reserve, self.safety_reserve):
            integer(value)
        if self.context_window < 1:
            raise InvalidRequest("Context window must be positive.")
        if self.working_set_units < 1:
            raise BudgetExceeded("Final request reserves leave no working-set capacity.")

    @property
    def working_set_units(self) -> int:
        return self.context_window - self.system_units - self.protocol_units - self.output_reserve - self.tool_reserve - self.safety_reserve

    @property
    def compiler_budget(self) -> int:
        """Packet budget before its output/tool reserves are applied."""
        return self.context_window - self.system_units - self.protocol_units - self.safety_reserve


@dataclass(frozen=True)
class TurnContext:
    phase: str
    packet: Packet
    requested: tuple[Ref, ...]
    recovery_count: int


class ContextController:
    """Compile a current packet on every turn and coordinate cold-page recovery."""

    def __init__(self, compiler: Compiler, scope: Scope, snapshot: str, session: str, *,
                 budget: RequestBudget, cache_key: str = "contextrail-turn", index: IndexPolicy = IndexPolicy(),
                 dependencies: dict[Ref, tuple[Ref, ...]] | None = None):
        if not isinstance(compiler, Compiler) or not isinstance(scope, Scope) or not isinstance(budget, RequestBudget):
            raise InvalidRequest("Context controller requires Compiler, Scope, and RequestBudget.")
        if not isinstance(snapshot, str) or not snapshot or not isinstance(session, str) or not session:
            raise InvalidRequest("Context controller requires a snapshot and session.")
        if not isinstance(index, IndexPolicy):
            raise InvalidRequest("Context controller requires an IndexPolicy.")
        self.compiler, self.scope, self.snapshot, self.session = compiler, scope, snapshot, session
        self.budget, self.cache_key, self.index = budget, cache_key, index
        state = compiler.store.load_snapshot(scope, snapshot)
        self._snapshot_refs = {Ref(**entry["ref"]) for entry in state["evidence"]}
        if dependencies is not None and (not isinstance(dependencies, dict) or any(
                not isinstance(ref, Ref) or not isinstance(deps, tuple) or any(not isinstance(dep, Ref) for dep in deps)
                for ref, deps in dependencies.items())):
            raise InvalidRequest("Recovery dependencies must map references to reference tuples.")
        self._dependencies = dict(dependencies or {})
        for ref, deps in self._dependencies.items():
            self._validate_snapshot_ref(ref)
            for dependency in deps:
                self._validate_snapshot_ref(dependency)
        self._requested: set[Ref] = set()
        self._recoveries: set[Ref] = set()
        # Roots the host/model explicitly asked to recover.  The requested set is
        # always the union of these roots' dependency closures, so evicting one
        # root is a matter of recomputing that union — a shared dependency of a
        # still-active root is retained without special-casing.
        self._recovery_roots: set[Ref] = set()

    def compile(self, phase: str) -> TurnContext:
        if phase not in _PHASES:
            raise InvalidRequest("Unsupported task phase.")
        requested = tuple(sorted(self._requested, key=lambda ref: (ref.name, ref.revision, ref.start,
                                                                     ref.end if ref.end is not None else 2**63 - 1)))
        layout = CacheLayout(self.cache_key, output_reserve=self.budget.output_reserve,
                             tool_reserve=self.budget.tool_reserve)
        try:
            packet = self.compiler.compile(self.scope, self.snapshot, self.session,
                                           budget=self.budget.compiler_budget, requested=requested,
                                           layout=layout, index=self.index)
        except BudgetExceeded as exc:
            raise BudgetExceeded("Required task state or recovered evidence cannot fit the final request budget.") from exc
        return TurnContext(phase, packet, requested, len(self._recoveries))

    def request(self, ref: Ref) -> bool:
        """Schedule a page and its declared dependencies; deduplicate reads.

        When a ref has a declared dependency closure, requesting the root pulls
        the whole transitive closure (cycle-safe) so recovering one member never
        leaves a hard dependency behind.  Requested pages become mandatory for
        the recompiled view.
        """
        self._validate_snapshot_ref(ref)
        added = ref not in self._recovery_roots
        self._recovery_roots.add(ref)
        self._rebuild_recovery()
        return added

    def _rebuild_recovery(self) -> None:
        """Recompute the requested set as the union of active roots' closures."""
        union: set[Ref] = set()
        for root in self._recovery_roots:
            union.update(self._closure(root))
        self._requested = union
        self._recoveries = set(union)

    def _closure(self, ref: Ref) -> list[Ref]:
        """Cycle-safe transitive dependency closure over declared dependencies."""
        seen: set[Ref] = set()
        order: list[Ref] = []
        stack = [ref]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            order.append(current)
            for dependency in self._dependencies.get(current, ()):
                if dependency not in seen:
                    stack.append(dependency)
        return order

    def register_dependencies(self, ref: Ref, dependencies: tuple[Ref, ...]) -> None:
        """Declare host-verified related evidence for dependency-aware recovery."""
        self._validate_snapshot_ref(ref)
        if not isinstance(dependencies, tuple) or any(not isinstance(item, Ref) for item in dependencies):
            raise InvalidRequest("Dependencies must be a tuple of evidence references.")
        if len(set(dependencies)) != len(dependencies):
            raise InvalidRequest("Duplicate recovery dependency.")
        for dependency in dependencies:
            self._validate_snapshot_ref(dependency)
        self._dependencies[ref] = dependencies

    def observe_tool_result(self, name: str, result: dict) -> bool:
        """Promote a successful ``context.get`` result into the next packet.

        A ``context.get`` may read a byte window, whose ref carries ``start``/
        ``end`` and is therefore not the whole-file ref recorded in the
        snapshot.  Promoting the ranged ref would be declined by snapshot
        fencing, so we promote the underlying whole-file ``(name, revision)``
        page instead: it is the snapshot member the window was taken from, and
        keeping the whole page hot is the correct recovery, not a fence bypass.
        """
        if name != "context.get" or not isinstance(result, dict) or "ref" not in result:
            return False
        ref = Ref(**result["ref"])
        if ref not in self._snapshot_refs and (ref.start or ref.end is not None):
            whole = Ref(ref.name, ref.revision)
            if whole in self._snapshot_refs:
                ref = whole
        return self.request(ref)

    def clear_recovery(self, ref: Ref) -> None:
        """Retire a recovered root, keeping dependencies other roots still need.

        Removing a recovery root drops the whole requested set to the union of
        the remaining active roots' closures.  A dependency shared with a root
        that is still active is therefore preserved automatically, and a root's
        private dependencies are released — evicting one root can never silently
        strand another root's material, nor keep dead pages hot.
        """
        self._validate_snapshot_ref(ref)
        self._recovery_roots.discard(ref)
        self._rebuild_recovery()

    def _validate_snapshot_ref(self, ref: Ref) -> None:
        if not isinstance(ref, Ref):
            raise InvalidRequest("Recovery requires an evidence reference.")
        if ref not in self._snapshot_refs:
            raise InvalidRequest("Recovery evidence is outside the current snapshot; create a verified new snapshot first.")
