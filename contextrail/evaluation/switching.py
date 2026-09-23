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
from ..models import CacheLayout, IndexPolicy, Scope, Selection, Target, canonical
from ..policy import UsageLedger
from ..store import Store
from .agent import TraceEvent, facts_present
from .models import ModelClient, ModelUsage
from .tasks import StressTask


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
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    latency_ms: int
    handoffs: int
    receipt_verified: bool
    epoch_verified: bool
    duplicate_action_blocked: bool
    trace: tuple[TraceEvent, ...]


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
        targets = {profile_id: Target(f"switch-{profile_id.lower()}", "api", profile.model)
                   for profile_id, profile in self.profiles.items()}
        trace: list[TraceEvent] = []
        usage = ModelUsage(0, 0, 0)
        latency = 0
        receipt_verified = True
        epoch_verified = True
        with Store(path) as store:
            lease = store.create_task(scope, targets["A"], task.objective,
                                      constraints=("Preserve exact evidence through profile handoff.",),
                                      acceptance=("Return every authoritative sentinel fact.",),
                                      allowed_providers=("api",))
            refs, selections = self._put_evidence(store, scope, lease, task)
            snapshot = store.snapshot(scope, lease, selections)
            compiler = Compiler(store)
            current = "A"
            if trajectory != "A->B-failover":
                answer, step_usage, step_latency = self._call(store, scope, snapshot, compiler, task, current, trace)
                usage, latency = self._sum_usage(usage, step_usage), latency + step_latency
            else:
                answer = ""
                trace.append(self._event("source.failure.simulated", profile="A", reason="router failover checkpoint"))
            lease, packet_sha = self._handoff(store, scope, lease, snapshot, compiler, targets["B"], trace)
            current = "B"
            receipt_verified = receipt_verified and bool(packet_sha)
            epoch_verified = epoch_verified and lease.epoch == 1
            answer, step_usage, step_latency = self._call(store, scope, snapshot, compiler, task, current, trace)
            usage, latency = self._sum_usage(usage, step_usage), latency + step_latency
            handoffs = 1
            if trajectory in {"A->B->A", "A->B->C"}:
                destination = "A" if trajectory == "A->B->A" else "C"
                if destination not in targets:
                    raise ValueError("Trajectory A->B->C requires profile C.")
                lease, packet_sha = self._handoff(store, scope, lease, snapshot, compiler, targets[destination], trace)
                current = destination
                receipt_verified = receipt_verified and bool(packet_sha)
                epoch_verified = epoch_verified and lease.epoch == 2
                answer, step_usage, step_latency = self._call(store, scope, snapshot, compiler, task, current, trace)
                usage, latency = self._sum_usage(usage, step_usage), latency + step_latency
                handoffs = 2
            elapsed = max(round((time.perf_counter() - started) * 1000), latency)
            final_packet = compiler.compile(scope, snapshot, lease.session, budget=self.context_budget,
                                            layout=CacheLayout("switch-stable-prefix", output_reserve=0, tool_reserve=0),
                                            index=IndexPolicy(max_entries=20))
            UsageLedger(store).record(scope, final_packet, unit="provider_tokens", input_units=usage.input_tokens or 0,
                                      cached_input_units=usage.cached_input_tokens or 0,
                                      output_units=usage.output_tokens or 0, latency_ms=elapsed)
            first_action = store.begin_action(scope, lease, "switch-evaluation-once")
            store.finish_action(scope, lease, "switch-evaluation-once", state="succeeded")
            duplicate_action_blocked = first_action and not store.begin_action(scope, lease, "switch-evaluation-once")
            trace.append(self._event("action.ledger.checked", duplicate_blocked=duplicate_action_blocked))
            success = facts_present(answer, task.expected_facts) and receipt_verified and epoch_verified and duplicate_action_blocked
            trace.append(self._event("switch.scored", success=success, final_profile=current, handoffs=handoffs))
            return SwitchRunResult(task.id, trajectory, success, current, answer, usage.input_tokens or 0,
                                   usage.output_tokens or 0, usage.cached_input_tokens or 0, elapsed, handoffs,
                                   receipt_verified, epoch_verified, duplicate_action_blocked, tuple(trace))

    def _handoff(self, store: Store, scope: Scope, lease, snapshot: str, compiler: Compiler, target: Target,
                 trace: list[TraceEvent]):
        handoff = store.prepare(scope, lease, snapshot, target)
        packet = compiler.for_handoff(scope, handoff.id, budget=self.context_budget,
                                      layout=CacheLayout("switch-stable-prefix", output_reserve=0, tool_reserve=0),
                                      index=IndexPolicy(max_entries=20))
        store.acknowledge(scope, handoff.id, target.session, packet.sha256)
        store.validate(scope, handoff.id)
        new_lease = store.activate(scope, handoff.id)
        trace.append(self._event("handoff.activated", from_session=handoff.source.session, to_session=target.session,
                                 epoch=new_lease.epoch, snapshot=snapshot, packet_sha256=packet.sha256,
                                 receipt_sha256=packet.sha256))
        return new_lease, packet.sha256

    def _call(self, store: Store, scope: Scope, snapshot: str, compiler: Compiler, task: StressTask, profile_id: str,
              trace: list[TraceEvent]) -> tuple[str, ModelUsage, int]:
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
        total = ModelUsage(0, 0, 0)
        latency = 0
        answer = ""
        for turn in range(self.max_turns):
            reply = self.profiles[profile_id].client.complete(messages, definitions)
            total = self._sum_usage(total, reply.usage)
            latency += reply.latency_ms
            trace.append(self._event("model.reply", profile=profile_id, turn=turn, request_id=reply.request_id,
                                     packet_sha256=packet.sha256, tool_calls=len(reply.tool_calls)))
            if not reply.tool_calls:
                return reply.content, total, latency
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
    def _put_evidence(store: Store, scope: Scope, lease, task: StressTask):
        refs, selections = {}, []
        for evidence in task.evidence:
            ref = store.put(scope, lease, evidence.name, evidence.text.encode("utf-8"), expected_revision=0)
            refs[evidence.name] = ref
            selections.append(Selection(ref, required=evidence.required, priority=evidence.priority,
                                        cache_stable=evidence.cache_stable))
        return refs, tuple(selections)

    @staticmethod
    def _sum_usage(left: ModelUsage, right: ModelUsage) -> ModelUsage:
        return ModelUsage((left.input_tokens or 0) + (right.input_tokens or 0),
                          (left.output_tokens or 0) + (right.output_tokens or 0),
                          (left.cached_input_tokens or 0) + (right.cached_input_tokens or 0))

    @staticmethod
    def _event(event: str, **details: object) -> TraceEvent:
        return TraceEvent(event, round(time.time() * 1000), details)


def run_switch_suite(agent: SwitchingApiAgent, tasks: Iterable[StressTask], output: Path,
                     trajectories: Iterable[str] = TRAJECTORIES) -> list[SwitchRunResult]:
    output.mkdir(parents=True, exist_ok=True)
    results = [agent.run(task, trajectory) for trajectory in trajectories for task in tasks]
    for result in results:
        folder = output / "switches" / result.trajectory.replace("->", "-") / result.task_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "trace.json").write_text(canonical({"task_id": result.task_id, "trajectory": result.trajectory,
                                                        "events": [asdict(event) for event in result.trace]}), encoding="utf-8")
    fields = [key for key in asdict(results[0]) if key != "trace"] if results else []
    with (output / "switch-metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: value for key, value in asdict(result).items() if key != "trace"} for result in results)
    lines = ["# ContextRail multi-profile switch report", "", "| Trajectory | Runs | Success | Handoffs |", "|---|---:|---:|---:|"]
    for trajectory in trajectories:
        rows = [row for row in results if row.trajectory == trajectory]
        lines.append(f"| {trajectory} | {len(rows)} | {sum(row.success for row in rows)}/{len(rows)} | {sum(row.handoffs for row in rows)} |")
    (output / "switch-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return results
