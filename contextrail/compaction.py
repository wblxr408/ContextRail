"""Snapshot-bound compaction lifecycle; hosts provide the actual model call."""

from dataclasses import dataclass
from typing import Callable

from .errors import InvalidRequest
from .models import Compaction, Lease, Ref, Scope, Summary
from .store import Store


@dataclass(frozen=True)
class SummaryDraft:
    """An uncommitted summary that validators may reject without data loss."""

    scope: Scope
    snapshot: str
    title: str
    text: str
    spans: tuple[Ref, ...]


@dataclass(frozen=True)
class CompactionEstimate:
    """Host-provided estimate used to avoid lossy compaction with no net gain."""

    future_input_savings: int
    generation_cost: int
    validation_cost: int = 0
    expected_recovery_cost: int = 0

    def __post_init__(self) -> None:
        for value in (self.future_input_savings, self.generation_cost, self.validation_cost,
                      self.expected_recovery_cost):
            if type(value) is not int or value < 0:
                raise InvalidRequest("Compaction estimates must be nonnegative integers.")

    @property
    def net_savings(self) -> int:
        return self.future_input_savings - self.generation_cost - self.validation_cost - self.expected_recovery_cost

    @property
    def worthwhile(self) -> bool:
        return self.net_savings > 0


def validate_summary_draft(store: Store, draft: SummaryDraft) -> None:
    """Minimum pre-commit gate: nonempty, current, exact source references.

    Semantic validators supplied by a host can additionally check required
    numbers, negations, and assertions before this draft becomes durable.
    """
    if not isinstance(draft, SummaryDraft) or not draft.spans:
        raise InvalidRequest("A summary draft requires at least one source span.")
    store.load_snapshot(draft.scope, draft.snapshot)
    for ref in draft.spans:
        store.get(draft.scope, ref)


class CompactionLifecycle:
    """Coordinate prepare/complete/abort around host-generated summary text.

    ``before`` is invoked after a durable prepared record exists. If it fails,
    the record is aborted. ``after`` is an observer: completion has already been
    committed, so observers must be idempotent and must not be correctness gates.
    """

    def __init__(self, store: Store, *, before: Callable[[Compaction], None] | None = None,
                 validate: Callable[[SummaryDraft], None] | None = None,
                 after: Callable[[Summary], None] | None = None):
        self.store = store
        self.before = before
        self.validate = validate
        self.after = after

    def begin(self, scope: Scope, lease: Lease, snapshot: str, *, reason: str) -> Compaction:
        compaction = self.store.begin_compaction(scope, lease, snapshot, reason=reason)
        if self.before is not None:
            try:
                self.before(compaction)
            except BaseException:
                self.store.abort_compaction(scope, lease, compaction.id)
                raise
        return compaction

    @staticmethod
    def should_begin(estimate: CompactionEstimate) -> bool:
        if not isinstance(estimate, CompactionEstimate):
            raise InvalidRequest("Expected a compaction estimate.")
        return estimate.worthwhile

    def complete(self, scope: Scope, lease: Lease, compaction_id: str, *, title: str,
                 text: str, spans: tuple[Ref, ...]) -> Summary:
        compaction = self.store.compaction(scope, compaction_id)
        draft = SummaryDraft(scope, compaction.snapshot, title, text, spans)
        validate_summary_draft(self.store, draft)
        if self.validate is not None:
            self.validate(draft)
        summary = self.store.complete_compaction(scope, lease, compaction_id, title=title, text=text, spans=spans)
        if self.after is not None:
            self.after(summary)
        return summary

    def abort(self, scope: Scope, lease: Lease, compaction_id: str) -> None:
        self.store.abort_compaction(scope, lease, compaction_id)
