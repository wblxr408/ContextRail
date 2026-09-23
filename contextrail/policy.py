"""Provider-neutral cache, usage, and routing policy contracts.

These classes deliberately do not call a model API.  A host may translate their
outputs to a provider cache, billing response, or handoff invocation.
"""

from typing import Callable

from .context import Compiler
from .errors import AccessDenied, IntegrityError, InvalidRequest
from .models import CacheLayout, IndexPolicy, Packet, RoutingDecision, RoutingRequest, Scope, Target, digest, identifier
from .store import Store


class CacheLayoutPolicy:
    """Compile packets with an explicit stable prefix and cold-index bound."""

    def __init__(self, layout: CacheLayout, *, index: IndexPolicy = IndexPolicy()):
        if not isinstance(layout, CacheLayout) or not isinstance(index, IndexPolicy):
            raise InvalidRequest("Cache layout policy requires CacheLayout and IndexPolicy values.")
        self.layout = layout
        self.index = index

    def compile(self, compiler: Compiler, scope: Scope, snapshot: str, session: str, *, budget: int,
                requested: tuple = ()) -> Packet:
        if not isinstance(compiler, Compiler):
            raise InvalidRequest("Cache layout policy requires a ContextRail compiler.")
        return compiler.compile(scope, snapshot, session, budget=budget, requested=requested,
                                layout=self.layout, index=self.index)


class UsageLedger:
    """Persist host-reported usage against a verified compiled packet."""

    def __init__(self, store: Store):
        self.store = store

    def record(self, scope: Scope, packet: Packet, *, unit: str, input_units: int,
               cached_input_units: int = 0, cache_write_units: int = 0, output_units: int = 0,
               latency_ms: int = 0):
        if not isinstance(packet, Packet):
            raise InvalidRequest("Usage must be associated with a compiled packet.")
        if digest(packet.body.encode("utf-8")) != packet.sha256:
            raise IntegrityError("Packet integrity check failed before recording usage.")
        target = self.store.session(scope, packet.target.session)
        if target != packet.target:
            raise AccessDenied("Packet target is not the registered session identity.")
        return self.store.record_usage(scope, packet.target.session, packet_sha256=packet.sha256, unit=unit,
                                       input_units=input_units, cached_input_units=cached_input_units,
                                       cache_write_units=cache_write_units, output_units=output_units,
                                       latency_ms=latency_ms)

    def totals(self, scope: Scope, *, session: str | None = None) -> dict:
        return self.store.usage_totals(scope, session=session)

    def record_request(self, scope: Scope, packet: Packet, *, unit: str,
                       input_units: int | None, cached_input_units: int | None = None,
                       cache_write_units: int | None = None, output_units: int | None = None,
                       latency_ms: int | None = None, request_id: str | None = None,
                       run_id: str | None = None, attempt: int = 1,
                       model_revision: str | None = None, request_digest: str | None = None):
        """Persist a request-granular observation, preserving missing usage."""
        if not isinstance(packet, Packet):
            raise InvalidRequest("Usage must be associated with a compiled packet.")
        if digest(packet.body.encode("utf-8")) != packet.sha256:
            raise IntegrityError("Packet integrity check failed before recording usage.")
        target = self.store.session(scope, packet.target.session)
        if target != packet.target:
            raise AccessDenied("Packet target is not the registered session identity.")
        return self.store.record_request_usage(
            scope, packet.target.session, packet_sha256=packet.sha256, snapshot=packet.snapshot, unit=unit,
            input_units=input_units, cached_input_units=cached_input_units,
            cache_write_units=cache_write_units, output_units=output_units, latency_ms=latency_ms,
            request_id=request_id, run_id=run_id, attempt=attempt, model_revision=model_revision,
            request_digest=request_digest,
        )


class ModelPolicyProvider:
    """Validate a host-owned routing callback before the host begins handoff.

    The selector may score quality, cost, latency, cache affinity, or task type.
    Its result is *only* a decision: it cannot bypass the handoff receipt/epoch
    state machine that transfers ownership.
    """

    def __init__(self, selector: Callable[[RoutingRequest], RoutingDecision | Target]):
        if not callable(selector):
            raise InvalidRequest("Model policy selector must be callable.")
        self.selector = selector

    def decide(self, store: Store, scope: Scope, request: RoutingRequest) -> RoutingDecision:
        if not isinstance(request, RoutingRequest):
            raise InvalidRequest("Expected a routing request.")
        selected = self.selector(request)
        if isinstance(selected, Target):
            decision = RoutingDecision(selected, "policy_callback")
        elif isinstance(selected, RoutingDecision):
            decision = selected
        else:
            raise InvalidRequest("Model policy must return a Target or RoutingDecision.")
        if decision.target not in request.candidates:
            raise AccessDenied("Model policy selected a target outside the approved candidate set.")
        store.permit_target(scope, decision.target)
        store.record_routing_decision(scope, purpose=request.purpose, target=decision.target,
                                      reason=decision.reason, estimated_input_units=request.estimated_input_units)
        return decision
