"""Deterministic C0/C1/C2 ablation for the semantic planning layer (design §13).

This measures the *mechanism* deltas the design proposes, with no network or
model call, so every number is reproducible from source bytes alone:

* **C0** — current structural chunking + lexical selection (the engineering
  baseline already shipped in ``retrieval.py``).
* **C1** — same candidate coverage, semantic (cohesion) ranking.  Isolates the
  *ranking* delta: does a related-but-not-lexically-matching span rank into the
  budget?
* **C2** — C1 plus dependency-closed group loading and recovery.  Isolates the
  *completeness* delta: is a declared closure loaded atomically (or refused),
  instead of a root arriving without its condition?

The tasks are built around the design's own worked example (§12): a retry rule
whose safety depends on two further conditions.  A lexical query hits the retry
rule but not the conditions, so C0 can load the root while dropping the
condition under budget pressure — exactly the silent-omission failure the group
mechanism exists to prevent.  This is a deterministic construction check, not a
claim about any model's task quality.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import csv
import json
from pathlib import Path

from ..chunking import StructuralChunker
from ..context import Compiler
from ..errors import BudgetExceeded
from ..models import Ref, Scope, Selection, SelectionGroup, Target, canonical
from ..retrieval import ChunkedEvidenceSelector
from ..semantic import (LexicalCohesionRanker, SemanticPlanner, SourceDocument, StructuralSemanticAnalyzer,
                        build_index, node_texts)
from ..store import Store


@dataclass(frozen=True)
class AblationDoc:
    name: str
    text: str
    media_type: str = "text/plain"


@dataclass(frozen=True)
class AblationTask:
    """One ablation scenario with an oracle the selectors never see.

    ``query`` is what a host would search.  ``root_symbol`` is the span the
    query lexically hits.  ``required_closure`` is the set of node labels that
    must accompany the root for a *correct* answer — the oracle.  ``relations``
    declares the host-confirmed hard dependencies used by C2 only.
    """

    id: str
    title: str
    docs: tuple[AblationDoc, ...]
    query: str
    root_label: str
    required_closure: tuple[str, ...]
    relations: tuple[tuple[str, str], ...] = ()  # (source_label, target_label) hard edges


@dataclass(frozen=True)
class StrategyOutcome:
    strategy: str
    packet_bytes: int
    included_labels: tuple[str, ...]
    root_present: bool
    closure_complete: bool
    missing_conditions: tuple[str, ...]
    budget_exceeded: bool


@dataclass(frozen=True)
class TaskAblation:
    task_id: str
    title: str
    budget: int
    outcomes: dict = field(default_factory=dict)


def _condition_doc(anchor: str) -> str:
    # A retry rule (the lexical hit) whose safety depends on two further
    # conditions that share NO query terms with it, plus unrelated padding that
    # competes for the budget.  This is the design's §12 example made concrete.
    return (
        f"Retrying {anchor} requests is allowed after a transient failure.\n\n"
        "Before any resubmission the client must first query the current transaction status.\n\n"
        "Resubmission is permitted only when the status explicitly confirms the charge did not go through.\n\n"
        + ("Unrelated operational diagnostic note about log rotation and disk usage. " * 12).strip() + "\n"
    )


ABLATION_TASKS: tuple[AblationTask, ...] = (
    AblationTask(
        "S01", "重试条件不可拆散",
        (AblationDoc("payment-policy", _condition_doc("payment")),),
        query="retry payment after failure",
        root_label="paragraph:1",
        required_closure=("paragraph:1", "paragraph:2", "paragraph:3"),
        relations=(("paragraph:1", "paragraph:2"), ("paragraph:2", "paragraph:3")),
    ),
    AblationTask(
        "S02", "跨段例外条件",
        (AblationDoc("refund-policy", _condition_doc("refund")),),
        query="refund resubmission allowed",
        root_label="paragraph:1",
        required_closure=("paragraph:1", "paragraph:2", "paragraph:3"),
        relations=(("paragraph:1", "paragraph:2"), ("paragraph:2", "paragraph:3")),
    ),
    AblationTask(
        "S03", "Python 导入硬依赖",
        (AblationDoc("service.py",
                     "import hashlib\n\n"
                     "def sign(payload):\n    return hashlib.sha256(payload).hexdigest()\n\n"
                     "def unrelated():\n    return 0\n",
                     media_type="text/x-python"),),
        query="sign",
        root_label="sign",
        required_closure=("sign", "<module.imports>"),
        relations=(),  # discovered deterministically from the AST, not host-declared
    ),
)


class SemanticAblation:
    """Run C0/C1/C2 for one task inside a disposable store; measure deltas."""

    def __init__(self, *, budget: int = 10_000):
        # A generous budget is deliberate: it isolates the *recall/completeness*
        # variable from budget pressure.  C0/C1 miss a condition because a
        # lexical ranker never recalls a non-matching span, not because it ran
        # out of room; C2 loads the declared closure atomically.  A tight budget
        # would confound "was it selected" with "did it fit".
        self.budget = budget

    def run(self, task: AblationTask) -> TaskAblation:
        import tempfile
        with tempfile.TemporaryDirectory(prefix=f"contextrail-ablation-{task.id.lower()}-") as folder:
            return self._run(task, Path(folder) / "state.sqlite3")

    def _run(self, task: AblationTask, path: Path) -> TaskAblation:
        scope = Scope("evaluation", "semantic-ablation", "main", task.id.lower())
        target = Target("ablation-session", "api", "ablation-model")
        with Store(path) as store:
            lease = store.create_task(scope, target, f"Resolve {task.title}",
                                      allowed_providers=("api",))
            refs: dict[str, Ref] = {}
            docs: list[SourceDocument] = []
            for doc in task.docs:
                content = doc.text.encode("utf-8")
                ref = store.put(scope, lease, doc.name, content, expected_revision=0, media_type=doc.media_type)
                refs[doc.name] = ref
                artifact = store.get(scope, ref)
                docs.append(SourceDocument(ref, artifact.content, artifact.sha256, doc.media_type))
            compiler = Compiler(store)
            index = build_index(scope.key, tuple(docs))
            texts = node_texts(index, tuple(docs))
            ranking = LexicalCohesionRanker().rank(task.query, index, texts)
            label_to_node = {node.label: node for node in index.nodes}
            outcomes = {
                "C0": self._c0(store, scope, lease, compiler, task, index),
                "C1": self._c1(store, scope, lease, compiler, task, index, ranking, label_to_node),
                "C2": self._c2(store, scope, lease, compiler, task, index, ranking, label_to_node),
            }
            return TaskAblation(task.id, task.title, self.budget,
                                {k: asdict(v) for k, v in outcomes.items()})

    def _labels_for_included(self, index, included) -> set[str]:
        by_ref = {node.ref: node.label for node in index.nodes}
        # Included refs are whole-artifact or chunk refs; map any that coincide
        # with a node span to that node's label for closure checking.
        labels: set[str] = set()
        for ref in included:
            if ref in by_ref:
                labels.add(by_ref[ref])
        return labels

    def _outcome(self, strategy: str, index, task: AblationTask, packet, budget_exceeded: bool) -> StrategyOutcome:
        labels = self._labels_for_included(index, packet.included) if packet is not None else set()
        missing = tuple(label for label in task.required_closure if label not in labels)
        return StrategyOutcome(
            strategy=strategy,
            packet_bytes=0 if packet is None else packet.units,
            included_labels=tuple(sorted(labels)),
            root_present=task.root_label in labels,
            closure_complete=not missing,
            missing_conditions=missing,
            budget_exceeded=budget_exceeded,
        )

    def _c0(self, store, scope, lease, compiler, task, index) -> StrategyOutcome:
        # C0: the shipped structural-chunk + lexical selector.  It ranks by
        # lexical match, so a condition that shares no query terms is never
        # recalled — the baseline gap the group mechanism addresses.
        plan = ChunkedEvidenceSelector(store).select(scope, task.query, max_optional=20)
        sid = store.snapshot(scope, lease, plan.selections)
        packet = compiler.compile(scope, sid, "ablation-session", budget=self.budget)
        return self._outcome("C0", index, task, packet, False)

    def _c1(self, store, scope, lease, compiler, task, index, ranking, label_to_node) -> StrategyOutcome:
        # C1: same candidate coverage, semantic (cohesion) ranking, with the
        # top-ranked root forced as a must-read.  This secures the root but,
        # like any ranker, still cannot recall a non-matching condition.
        selections = []
        seen = set()
        root_node = label_to_node.get(task.root_label)
        if root_node is not None:
            selections.append(Selection(root_node.ref, required=True))
            seen.add(root_node.ref)
        for candidate in ranking.ranked:
            node = index.node(candidate.node_id)
            if node.ref in seen:
                continue
            selections.append(Selection(node.ref, priority=max(1, round(candidate.score * 1000))))
            seen.add(node.ref)
        sid = store.snapshot(scope, lease, tuple(selections))
        packet = compiler.compile(scope, sid, "ablation-session", budget=self.budget)
        return self._outcome("C1", index, task, packet, False)

    def _c2(self, store, scope, lease, compiler, task, index, ranking, label_to_node) -> StrategyOutcome:
        # C2: C1 plus dependency-closed group loading.  The planner expands the
        # root's hard closure (host-declared prose conditions, or AST-confirmed
        # imports) into a required atomic group, so the whole closure loads or
        # the compile refuses — a condition can never be silently dropped.
        index = self._with_declared_relations(index, task, label_to_node)
        root_node = label_to_node.get(task.root_label)
        must_include = (root_node.id,) if root_node is not None else ()
        plan = SemanticPlanner().plan(scope_key=scope.key, task_version=store.task(scope)["version"],
                                      index=index, query=task.query, ranking=ranking, must_include=must_include)
        # Every node is individually selectable; groups bind the closures.
        selections = tuple(Selection(node.ref) for node in index.nodes)
        groups = tuple(SelectionGroup(group.id, group.member_refs, required=group.required,
                                      priority=group.priority)
                       for group in plan.groups)
        sid = store.snapshot(scope, lease, selections, groups=groups)
        try:
            packet = compiler.compile(scope, sid, "ablation-session", budget=self.budget)
            return self._outcome("C2", index, task, packet, False)
        except BudgetExceeded:
            return self._outcome("C2", index, task, None, True)

    @staticmethod
    def _with_declared_relations(index, task: AblationTask, label_to_node):
        """Attach the task's host-declared hard relations to the analyzed index.

        Structural (Python import) dependencies are already hard in the index
        from the AST parse; prose-condition dependencies are host knowledge, so
        the ablation declares them here exactly as a host would confirm them.
        """
        if not task.relations:
            return index
        from dataclasses import replace as _replace
        from ..semantic import EvidenceRelation
        extra = tuple(EvidenceRelation(label_to_node[s].id, label_to_node[t].id, "requires", "hard", "host")
                      for s, t in task.relations if s in label_to_node and t in label_to_node)
        existing = {(r.source_id, r.target_id, r.kind) for r in index.relations}
        merged = index.relations + tuple(r for r in extra if (r.source_id, r.target_id, r.kind) not in existing)
        return _replace(index, relations=merged)


def run_ablation_suite(output: Path, *, budget: int = 10_000, tasks: tuple[AblationTask, ...] = ABLATION_TASKS) -> list[TaskAblation]:
    """Run every C0/C1/C2 ablation task and write CSV + JSON + a Markdown report."""
    output.mkdir(parents=True, exist_ok=True)
    runner = SemanticAblation(budget=budget)
    results = [runner.run(task) for task in tasks]
    (output / "ablation.json").write_text(
        canonical({"schema": "contextrail.semantic-ablation/v1", "budget": budget,
                   "tasks": [asdict(result) for result in results]}) + "\n", encoding="utf-8")
    # Flat CSV: one row per (task, strategy).
    fields = ["task_id", "title", "strategy", "packet_bytes", "root_present",
              "closure_complete", "missing_conditions", "budget_exceeded", "included_labels"]
    with (output / "ablation-metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results:
            for strategy in ("C0", "C1", "C2"):
                outcome = result.outcomes[strategy]
                writer.writerow({
                    "task_id": result.task_id, "title": result.title, "strategy": strategy,
                    "packet_bytes": outcome["packet_bytes"], "root_present": outcome["root_present"],
                    "closure_complete": outcome["closure_complete"],
                    "missing_conditions": ";".join(outcome["missing_conditions"]),
                    "budget_exceeded": outcome["budget_exceeded"],
                    "included_labels": ";".join(outcome["included_labels"]),
                })
    _write_ablation_report(results, output)
    return results


def _write_ablation_report(results: list[TaskAblation], output: Path) -> None:
    total = len(results)
    complete = {s: sum(1 for r in results if r.outcomes[s]["closure_complete"]) for s in ("C0", "C1", "C2")}
    root = {s: sum(1 for r in results if r.outcomes[s]["root_present"]) for s in ("C0", "C1", "C2")}
    lines = [
        "# 语义规划层 C0/C1/C2 消融（确定性机制验证）",
        "",
        "所有数字仅从原文字节确定性算出，无模型、无网络。它验证机制差异，不是模型任务质量断言。",
        "",
        "- **C0**：现有结构分块 + 词法选择（工程基线）。",
        "- **C1**：同候选覆盖 + 语义（内聚）排序，置顶根设为必读。",
        "- **C2**：C1 + 依赖成组原子装入与恢复（本设计新增）。",
        "",
        "## 汇总",
        "",
        "| 策略 | 根命中 | 依赖闭包完整 |",
        "|---|---:|---:|",
    ]
    for strategy in ("C0", "C1", "C2"):
        lines.append(f"| {strategy} | {root[strategy]}/{total} | {complete[strategy]}/{total} |")
    lines += ["", "## 逐任务", "",
              "| 任务 | 策略 | 根命中 | 闭包完整 | 缺失条件 | Packet 字节 |",
              "|---|---|---:|---:|---|---:|"]
    for result in results:
        for strategy in ("C0", "C1", "C2"):
            outcome = result.outcomes[strategy]
            missing = "、".join(outcome["missing_conditions"]) or "—"
            lines.append(
                f"| {result.task_id} {result.title} | {strategy} | "
                f"{'✓' if outcome['root_present'] else '✗'} | "
                f"{'✓' if outcome['closure_complete'] else '✗'} | {missing} | {outcome['packet_bytes']} |")
    lines += [
        "",
        "## 读法",
        "",
        "C0/C1 命中重试规则（根），却漏掉与查询无共同词的安全条件——这正是设计 §12 的静默遗漏。",
        "C2 把根的硬依赖闭包整体装入，缺失条件降为零；预算不足时整组失败而非拆散（见 `budget_exceeded`）。",
        "引用有效只证来源，不证语义完整：闭包完整仅指“声明的依赖全部装入”，真实依赖是否被发现另需 oracle 召回评测。",
    ]
    (output / "ablation-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
