"""Multi-profile ContextRail handoff experiments for API models."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import csv
import json
from pathlib import Path
import tempfile
import time
from typing import Iterable

from ..context import Compiler
from ..host import ContextTools
from ..models import CacheLayout, IndexPolicy, Scope, Target, canonical, digest
from ..policy import UsageLedger
from ..store import Store
from .agent import MinimalApiAgent, TraceEvent, facts_present
from .models import ModelClient, ModelRequestError, ModelUsage
from .tasks import StressTask
from .telemetry import distribution


TRAJECTORIES = ("A->B", "A->B->A", "A->B->C", "A->B-failover")


@dataclass(frozen=True)
class ModelProfile:
    id: str
    model: str
    client: ModelClient


@dataclass(frozen=True)
class SwitchRunResult:
    task_id: str
    trajectory: str
    success: bool
    final_profile: str
    answer: str
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    latency_ms: int
    handoffs: int
    receipt_verified: bool
    epoch_verified: bool
    duplicate_action_blocked: bool
    trace: tuple[TraceEvent, ...]
    cache_write_tokens: int | None = None


class SwitchingApiAgent:
    """Runs API calls across profiles while ContextRail owns every handoff."""

    def __init__(self, profiles: dict[str, ModelProfile], *, context_budget: int = 4_000, max_turns: int = 4):
        if not {"A", "B"}.issubset(profiles):
            raise ValueError("Switch experiments require at least profiles A and B.")
        self.profiles = profiles
        self.context_budget = context_budget
        self.max_turns = max_turns

    def run(self, task: StressTask, trajectory: str) -> SwitchRunResult:
        if trajectory not in TRAJECTORIES:
            raise ValueError(f"Unknown trajectory: {trajectory}")
        with tempfile.TemporaryDirectory(prefix=f"contextrail-switch-{task.id.lower()}-") as folder:
            return self._run(task, trajectory, Path(folder) / "state.sqlite3")

    def _run(self, task: StressTask, trajectory: str, path: Path) -> SwitchRunResult:
        started = time.perf_counter()
        scope = Scope("evaluation", "switch-suite", "main", f"{task.id.lower()}-{trajectory.lower().replace('->', '-')}")
        targets = {profile_id: Target(f"switch-{profile_id.lower()}", getattr(profile.client, "provider", "api"), profile.model)
                   for profile_id, profile in self.profiles.items()}
        trace: list[TraceEvent] = []
        observations: list[ModelUsage] = []
        receipt_verified = True
        epoch_verified = True
        with Store(path) as store:
            lease = store.create_task(scope, targets["A"], task.objective,
                                      constraints=("Preserve exact evidence through profile handoff.",),
                                      acceptance=("Return every authoritative sentinel fact.",),
                                      allowed_providers=tuple(dict.fromkeys(t.provider for t in targets.values())))
            _, selections = MinimalApiAgent._put_evidence(store, scope, lease, task)
            snapshot = store.snapshot(scope, lease, selections)
            compiler = Compiler(store)
            current = "A"
            if trajectory != "A->B-failover":
                answer, step_usage = self._call(store, scope, snapshot, compiler, task, current, trace)
                observations.extend(step_usage)
            else:
                answer = ""
                trace.append(self._event("source.failure.simulated", profile="A", reason="router failover checkpoint"))
            lease, packet_sha = self._handoff(store, scope, lease, snapshot, compiler, targets["B"], trace)
            current = "B"
            receipt_verified = receipt_verified and bool(packet_sha)
            epoch_verified = epoch_verified and lease.epoch == 1
            answer, step_usage = self._call(store, scope, snapshot, compiler, task, current, trace)
            observations.extend(step_usage)
            handoffs = 1
            if trajectory in {"A->B->A", "A->B->C"}:
                destination = "A" if trajectory == "A->B->A" else "C"
                if destination not in targets:
                    raise ValueError("Trajectory A->B->C requires profile C.")
                lease, packet_sha = self._handoff(store, scope, lease, snapshot, compiler, targets[destination], trace)
                current = destination
                receipt_verified = receipt_verified and bool(packet_sha)
                epoch_verified = epoch_verified and lease.epoch == 2
                answer, step_usage = self._call(store, scope, snapshot, compiler, task, current, trace)
                observations.extend(step_usage)
                handoffs = 2
            elapsed = round((time.perf_counter() - started) * 1000)
            usage = ModelUsage.total(observations)
            first_action = store.begin_action(scope, lease, "switch-evaluation-once")
            store.finish_action(scope, lease, "switch-evaluation-once", state="succeeded")
            duplicate_action_blocked = first_action and not store.begin_action(scope, lease, "switch-evaluation-once")
            trace.append(self._event("action.ledger.checked", duplicate_blocked=duplicate_action_blocked))
            success = facts_present(answer, task.expected_facts) and receipt_verified and epoch_verified and duplicate_action_blocked
            trace.append(self._event("switch.scored", success=success, final_profile=current, handoffs=handoffs))
            return SwitchRunResult(task.id, trajectory, success, current, answer, usage.input_tokens,
                                   usage.output_tokens, usage.cached_input_tokens, elapsed, handoffs,
                                   receipt_verified, epoch_verified, duplicate_action_blocked, tuple(trace),
                                   usage.cache_write_tokens)

    def _handoff(self, store: Store, scope: Scope, lease, snapshot: str, compiler: Compiler, target: Target,
                 trace: list[TraceEvent]):
        started = time.perf_counter()
        handoff = store.prepare(scope, lease, snapshot, target)
        packet = compiler.for_handoff(scope, handoff.id, budget=self.context_budget,
                                      layout=CacheLayout("switch-stable-prefix", output_reserve=0, tool_reserve=0),
                                      index=IndexPolicy(max_entries=20))
        store.acknowledge(scope, handoff.id, target.session, packet.sha256)
        store.validate(scope, handoff.id)
        new_lease = store.activate(scope, handoff.id)
        trace.append(self._event("handoff.activated", from_session=handoff.source.session, to_session=target.session,
                                 epoch=new_lease.epoch, snapshot=snapshot, packet_sha256=packet.sha256,
                                 receipt_sha256=packet.sha256, latency_ms=round((time.perf_counter() - started) * 1000)))
        return new_lease, packet.sha256

    def _call(self, store: Store, scope: Scope, snapshot: str, compiler: Compiler, task: StressTask, profile_id: str,
              trace: list[TraceEvent]) -> tuple[str, list[ModelUsage]]:
        target = store.session(scope, f"switch-{profile_id.lower()}")
        packet = compiler.compile(scope, snapshot, target.session, budget=self.context_budget,
                                  layout=CacheLayout("switch-stable-prefix", output_reserve=0, tool_reserve=0),
                                  index=IndexPolicy(max_entries=20))
        tools = ContextTools(store, scope, target.session)
        definitions = [{"type": "function", "function": {"name": item["name"], "description": item["description"],
                       "parameters": item["input_schema"]}} for item in ContextTools.definitions()]
        messages = [{"role": "system", "content": "Use exact evidence and tools for cold pages. Return authoritative facts only."},
                    {"role": "user", "content": canonical({"task_id": task.id, "strategy": "C", "profile": profile_id,
                                                               "objective": task.objective, "context": packet.body})}]
        observations = []
        client = self.profiles[profile_id].client
        ledger = UsageLedger(store)
        for turn in range(self.max_turns):
            timestamp = time.time()
            error = None
            try:
                reply = client.complete(messages, definitions)
            except ModelRequestError as exc:
                reply, error = exc.observation, exc.code
            observations.append(reply.usage)
            unit = "fixture_estimated_tokens" if target.provider == "deterministic-fixture" else "provider_tokens"
            record = ledger.record_request(
                scope, packet, unit=unit, input_units=reply.usage.input_tokens,
                output_units=reply.usage.output_tokens, cached_input_units=reply.usage.cached_input_tokens,
                cache_write_units=reply.usage.cache_write_tokens, latency_ms=reply.latency_ms,
                request_id=reply.request_id, run_id=scope.task, strategy="switch",
                model_revision=getattr(client, "model_revision", None),
                timestamp=reply.timestamp if reply.timestamp is not None else timestamp,
                ttft_ms=reply.ttft_ms, tool_calls=len(reply.tool_calls) if error is None else None,
                retries=reply.retries, error_code=error,
                request_digest=reply.request_digest or digest(canonical({"messages": messages, "tools": definitions}).encode()),
            )
            trace.append(self._event("model.reply", profile=profile_id, turn=turn, **asdict(record)))
            if error is not None:
                return "", observations
            if not reply.tool_calls:
                return reply.content, observations
            messages.append({"role": "assistant", "content": reply.content, "tool_calls": [
                {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": canonical(call.arguments)}}
                for call in reply.tool_calls]})
            for call in reply.tool_calls:
                if call.name != "context.get":
                    raise RuntimeError(f"Unsupported switch tool {call.name}.")
                result = tools.call(call.name, call.arguments)
                trace.append(self._event("tool.result", profile=profile_id, ref=result["ref"]))
                messages.append({"role": "tool", "tool_call_id": call.id, "content": canonical(result)})
        raise RuntimeError("Profile exceeded switch experiment turn limit.")

    @staticmethod
    def _event(event: str, **details: object) -> TraceEvent:
        return TraceEvent(event, round(time.time() * 1000), details)


def run_switch_suite(agent: SwitchingApiAgent, tasks: Iterable[StressTask], output: Path,
                     trajectories: Iterable[str] = TRAJECTORIES) -> list[SwitchRunResult]:
    output.mkdir(parents=True, exist_ok=True)
    tasks, trajectories = tuple(tasks), tuple(trajectories)
    results = [agent.run(task, trajectory) for trajectory in trajectories for task in tasks]
    for result in results:
        folder = output / "switches" / result.trajectory.replace("->", "-") / result.task_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "trace.json").write_text(canonical({"task_id": result.task_id, "trajectory": result.trajectory,
                                                        "events": [asdict(event) for event in result.trace]}), encoding="utf-8")
        requests = (dict(event.details, task_id=result.task_id) for event in result.trace if event.event == "model.reply")
        (folder / "usage.jsonl").write_text("".join(canonical(record) + "\n" for record in requests), encoding="utf-8")
    fields = [key for key in asdict(results[0]) if key != "trace"] if results else []
    with (output / "switch-metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: value for key, value in asdict(result).items() if key != "trace"} for result in results)
    lines = ["# ContextRail multi-profile switch report", "", "| Trajectory | Runs | Success | Handoffs |", "|---|---:|---:|---:|"]
    latency = {}
    for trajectory in trajectories:
        rows = [row for row in results if row.trajectory == trajectory]
        lines.append(f"| {trajectory} | {len(rows)} | {sum(row.success for row in rows)}/{len(rows)} | {sum(row.handoffs for row in rows)} |")
        handoffs = [event.details["latency_ms"] for row in rows for event in row.trace if event.event == "handoff.activated"]
        requests = [event.details for row in rows for event in row.trace if event.event == "model.reply"]
        latency[trajectory] = {
            "run_ms": distribution(row.latency_ms for row in rows),
            "handoff_ms": distribution(handoffs),
            "requests_by_cache": {
                state: {field: distribution(r[field] for r in requests
                                            if ("unknown" if r["cached_input_units"] is None else
                                                "hot" if r["cached_input_units"] else "cold") == state)
                        for field in ("ttft_ms", "latency_ms")}
                for state in ("cold", "hot", "unknown")
            },
        }
    (output / "switch-latency.json").write_text(canonical(latency) + "\n", encoding="utf-8")
    (output / "switch-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return results
