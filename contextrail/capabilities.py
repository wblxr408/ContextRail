"""Capabilities require trusted host evidence, never model-provided client labels."""

from dataclasses import dataclass, field

from .errors import InvalidRequest
from .models import identifier


CAPABILITIES = ("read", "event_capture", "startup_injection", "compaction_hook", "block",
                "argument_rewrite", "final_request_control")


@dataclass(frozen=True)
class CapabilityEvidence:
    state: str
    source: str
    reason: str

    def __post_init__(self):
        if self.state not in {"supported", "unsupported", "unknown"}:
            raise InvalidRequest("Unknown capability state.")
        identifier(self.source)
        identifier(self.reason)


@dataclass(frozen=True)
class HostCapabilities:
    evidence: dict = field(default_factory=dict)

    def describe(self):
        from dataclasses import asdict
        if self.evidence.keys() - set(CAPABILITIES) or any(not isinstance(v, CapabilityEvidence) for v in self.evidence.values()):
            raise InvalidRequest("Capabilities require explicit trusted host evidence.")
        states = {key: asdict(self.evidence.get(key, CapabilityEvidence("unknown", "not_provided", "No trusted host evidence.")))
                  for key in CAPABILITIES}
        return {"capabilities": states, "history_control": "host_control_available" if
                states["final_request_control"]["state"] == "supported" else "not_controlled",
                "history_replaced": False,
                "degradation": [key for key, value in states.items() if value["state"] != "supported"]}

    @classmethod
    def read_only(cls):
        return cls({"read": CapabilityEvidence("supported", "contextrail-mcp-adapter", "Bound read-only context tools are mounted.")})
