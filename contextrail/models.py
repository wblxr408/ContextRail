"""Provider-independent value objects. No credentials or hidden model state."""

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any

from .errors import InvalidRequest


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def identifier(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 512 or any(ord(c) < 32 for c in value):
        raise InvalidRequest("Expected a nonempty identifier without control characters (max 512 characters).")
    unicode_text(value)


def unicode_text(value: str) -> None:
    if not isinstance(value, str):
        raise InvalidRequest("Expected text.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise InvalidRequest("Text contains an invalid Unicode scalar value.") from None


def integer(value: int, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum or value > 2**63 - 1:
        raise InvalidRequest("Expected an integer in the supported range.")


@dataclass(frozen=True)
class Scope:
    tenant: str
    project: str
    branch: str
    task: str

    def __post_init__(self) -> None:
        for value in asdict(self).values():
            identifier(value)

    @property
    def key(self) -> str:
        return digest(canonical(asdict(self)).encode("utf-8"))


@dataclass(frozen=True)
class Target:
    session: str
    provider: str
    model: str

    def __post_init__(self) -> None:
        for value in asdict(self).values():
            identifier(value)


@dataclass(frozen=True)
class Lease:
    session: str
    epoch: int

    def __post_init__(self) -> None:
        identifier(self.session)
        integer(self.epoch)


@dataclass(frozen=True)
class Ref:
    name: str
    revision: int
    start: int = 0
    end: int | None = None

    def __post_init__(self) -> None:
        identifier(self.name)
        integer(self.revision, 1)
        integer(self.start)
        if self.end is not None:
            integer(self.end)
            if self.end < self.start:
                raise InvalidRequest("End must not precede start.")


@dataclass(frozen=True)
class Selection:
    ref: Ref
    required: bool = False
    priority: int = 0
    current: bool = True
    cache_stable: bool = False

    def __post_init__(self) -> None:
        if (not isinstance(self.ref, Ref) or type(self.required) is not bool or
                type(self.current) is not bool or type(self.cache_stable) is not bool):
            raise InvalidRequest("Invalid evidence selection.")
        if self.cache_stable and not self.required:
            raise InvalidRequest("Cache-stable evidence must be required so the stable prefix cannot be dropped.")
        integer(self.priority)


@dataclass(frozen=True)
class SelectionGroup:
    """An atomic load unit: a set of evidence refs that load whole or not at all.

    A snapshot carrying groups is compiled with group semantics: a ``required``
    group either loads every member or fails with ``BudgetExceeded`` (never a
    partial), and an optional group either loads whole or stays entirely cold.
    ``members`` is the already-expanded dependency closure produced by the
    planner, so the compiler enforces atomicity without re-deriving relations.
    """

    id: str
    members: tuple[Ref, ...]
    required: bool = False
    priority: int = 0

    def __post_init__(self) -> None:
        identifier(self.id)
        if not isinstance(self.members, tuple) or not self.members or any(
                not isinstance(ref, Ref) for ref in self.members):
            raise InvalidRequest("A selection group needs a nonempty tuple of references.")
        if len(set(self.members)) != len(self.members):
            raise InvalidRequest("Selection group members must be unique.")
        if type(self.required) is not bool:
            raise InvalidRequest("Selection group required flag must be boolean.")
        integer(self.priority)


@dataclass(frozen=True)
class Artifact:
    ref: Ref
    sha256: str
    size: int
    media_type: str
    content: bytes


@dataclass(frozen=True)
class Packet:
    snapshot: str
    target: Target
    body: str
    sha256: str
    units: int
    budget: int
    unit: str
    included: tuple[Ref, ...]
    omitted: tuple[Ref, ...]
    reserved: int = 0
    cache_key: str | None = None
    stable_digest: str | None = None


@dataclass(frozen=True)
class CacheLayout:
    """Provider-neutral cache layout hints for a compiled context packet.

    ``cache_key`` is intentionally opaque: a host maps it to a provider's cache
    mechanism, if that provider has one.  The three reserves keep non-context
    budget explicit instead of silently consuming the whole model window.
    """

    cache_key: str
    output_reserve: int = 0
    tool_reserve: int = 0
    history_reserve: int = 0

    def __post_init__(self) -> None:
        identifier(self.cache_key)
        integer(self.output_reserve)
        integer(self.tool_reserve)
        integer(self.history_reserve)

    @property
    def reserve(self) -> int:
        return self.output_reserve + self.tool_reserve + self.history_reserve


@dataclass(frozen=True)
class IndexPolicy:
    """Bound the cold-index preview; remaining references are paged by a tool."""

    max_entries: int = 20

    def __post_init__(self) -> None:
        integer(self.max_entries, 1)
        if self.max_entries > 1_000:
            raise InvalidRequest("Cold-index entry limit exceeds 1000.")


@dataclass(frozen=True)
class UsageRecord:
    id: str
    session: str
    provider: str
    model: str
    packet_sha256: str
    unit: str
    input_units: int
    cached_input_units: int
    cache_write_units: int
    output_units: int
    latency_ms: int
    at: float


@dataclass(frozen=True)
class RequestUsage:
    """One actual provider request, including explicitly unknown measurements.

    Unlike the legacy aggregate usage record, nullable measurements mean the
    provider did not report a value.  They must never be interpreted as zero.
    """

    id: str
    session: str
    provider: str
    model: str
    model_revision: str | None
    request_id: str | None
    run_id: str | None
    attempt: int
    packet_sha256: str
    request_digest: str | None
    snapshot: str
    unit: str
    input_units: int | None
    cached_input_units: int | None
    cache_write_units: int | None
    output_units: int | None
    latency_ms: int | None
    usage_known: bool
    at: float


@dataclass(frozen=True)
class RoutingRequest:
    """Facts supplied to a host policy callback before it starts a handoff."""

    purpose: str
    candidates: tuple[Target, ...]
    estimated_input_units: int = 0
    cache_key: str | None = None

    def __post_init__(self) -> None:
        identifier(self.purpose)
        if not isinstance(self.candidates, tuple) or not self.candidates or any(not isinstance(t, Target) for t in self.candidates):
            raise InvalidRequest("Routing candidates must be a nonempty tuple of targets.")
        if len(set(self.candidates)) != len(self.candidates):
            raise InvalidRequest("Duplicate routing candidates.")
        integer(self.estimated_input_units)
        if self.cache_key is not None:
            identifier(self.cache_key)


@dataclass(frozen=True)
class RoutingDecision:
    target: Target
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.target, Target):
            raise InvalidRequest("Routing decision requires a target.")
        identifier(self.reason)


@dataclass(frozen=True)
class Compaction:
    id: str
    snapshot: str
    session: str
    reason: str
    phase: str


@dataclass(frozen=True)
class Summary:
    id: str
    snapshot: str
    title: str
    text: str
    spans: tuple[Ref, ...]
    sha256: str


@dataclass(frozen=True)
class TaskStateItem:
    """Host-authored, source-linked task state for the current working set."""

    id: str
    kind: str
    text: str
    sources: tuple[Ref, ...]
    status: str
    inferred: bool
    supersedes: tuple[str, ...]
    created_at: float


@dataclass(frozen=True)
class Handoff:
    id: str
    snapshot: str
    source: Lease
    target: Target
    phase: str
    packet_sha256: str | None
