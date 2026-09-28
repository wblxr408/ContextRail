"""Deterministic tests for the optional semantic planning layer (P0 + local P1).

These cover the P0 protocol invariants (byte-accurate structure, cycle-safe
hard closures, atomic group loading, recovery-closure lifecycle, schema
fencing) and the local, model-free P1 analyzer/ranker.  No network or model is
involved, so every assertion is a mechanism check, not a task-quality claim.
"""

from dataclasses import replace

from contextrail import (Compiler, EvidenceGroup, LexicalCohesionRanker, Ref, Selection, SelectionGroup,
                         SemanticPlanner, SourceDocument, StructuralSemanticAnalyzer, build_index,
                         node_texts, validate_index)
from contextrail.errors import BudgetExceeded, IntegrityError, InvalidRequest
from contextrail.models import digest
from contextrail.semantic import (EvidenceRelation, SemanticIndexDraft, SemanticNode)
from .common import RailTest


def _doc(name: str, content: bytes, revision: int = 1, media_type: str = "text/plain") -> SourceDocument:
    return SourceDocument(Ref(name, revision), content, digest(content), media_type)


class SemanticIndexTests(RailTest):
    def test_structural_analyzer_binds_exact_spans_and_hard_import_dependency(self):
        source = b"import math\n\ndef calc(v):\n    return math.ceil(v)\n\ndef other():\n    return 0\n"
        index = build_index("scope-key", (_doc("calc.py", source, media_type="text/x-python"),))
        labels = {node.label: node for node in index.nodes}
        self.assertIn("calc", labels)
        self.assertIn("other", labels)
        # The function node references the exact function bytes, verbatim.
        calc = labels["calc"]
        self.assertEqual(source[calc.ref.start:calc.ref.end], b"def calc(v):\n    return math.ceil(v)\n")
        # calc requires the import span; the relation is hard because a real
        # AST parse confirmed the dependency, not a model guess.
        requires = [r for r in index.relations if r.source_id == calc.id and r.kind == "requires"]
        self.assertEqual(len(requires), 1)
        self.assertEqual(requires[0].enforcement, "hard")
        self.assertEqual(requires[0].provenance, "deterministic_parse")

    def test_validate_index_drops_out_of_range_nodes_and_orphaned_relations(self):
        content = b"hello world"
        doc = _doc("a.txt", content)
        good = SemanticNode("n_good", Ref("a.txt", 1, 0, 5), doc.source_sha256, "paragraph", "greeting")
        # A node past the artifact end, and one with a wrong hash: both dropped.
        past_end = SemanticNode("n_bad", Ref("a.txt", 1, 0, 999), doc.source_sha256, "paragraph", "toolong")
        wrong_hash = SemanticNode("n_hash", Ref("a.txt", 1, 0, 5), "0" * 64, "paragraph", "badhash")
        draft = SemanticIndexDraft(
            (good, past_end, wrong_hash),
            (EvidenceRelation("n_good", "n_bad", "elaboration", "advisory"),),  # endpoint dropped -> relation dropped
        )
        index = validate_index("scope-key", draft, (doc,))
        self.assertEqual([n.id for n in index.nodes], ["n_good"])
        self.assertEqual(index.relations, ())

    def test_uncovered_spans_are_reported_for_recall_fallback(self):
        # A leading module docstring is not emitted as its own chunk, so it must
        # surface as an uncovered span rather than becoming invisible.
        source = b'"""module doc"""\nimport os\n\ndef f():\n    return os.getpid()\n'
        index = build_index("scope-key", (_doc("m.py", source, media_type="text/x-python"),))
        self.assertTrue(index.uncovered_spans)
        # Every uncovered span is a real byte range inside the artifact.
        for name, revision, start, end in index.uncovered_spans:
            self.assertEqual((name, revision), ("m.py", 1))
            self.assertLess(start, end)
            self.assertLessEqual(end, len(source))

    def test_index_digest_is_deterministic_across_reanalysis(self):
        source = b"import math\n\ndef calc(v):\n    return math.ceil(v)\n"
        doc = _doc("calc.py", source, media_type="text/x-python")
        first = build_index("scope-key", (doc,))
        second = build_index("scope-key", (doc,))
        self.assertEqual(first.digest, second.digest)


class SemanticPlannerTests(RailTest):
    def _payment_index(self):
        # A -> B -> C hard chain plus an unrelated node D, exercising transitive
        # closure and cold-ref separation without touching the store.
        content = (b"A retry allowed after failure.\n\n"
                   b"B payment must query transaction status first.\n\n"
                   b"C only resubmit when explicitly not charged.\n\n"
                   b"D unrelated note.\n")
        doc = _doc("policy.txt", content)
        a = SemanticNode("A", Ref("policy.txt", 1, 0, 31), doc.source_sha256, "paragraph", "retry")
        b = SemanticNode("B", Ref("policy.txt", 1, 33, 80), doc.source_sha256, "paragraph", "query-first")
        c = SemanticNode("C", Ref("policy.txt", 1, 82, 127), doc.source_sha256, "paragraph", "no-charge")
        d = SemanticNode("D", Ref("policy.txt", 1, 129, len(content)), doc.source_sha256, "paragraph", "unrelated")
        relations = (EvidenceRelation("A", "B", "requires", "hard", "host"),
                     EvidenceRelation("B", "C", "requires", "hard", "host"))
        index = validate_index("scope-key", SemanticIndexDraft((a, b, c, d), relations), (doc,))
        return index, doc

    def test_must_read_root_pulls_full_transitive_hard_closure_into_required_group(self):
        index, doc = self._payment_index()
        texts = node_texts(index, (doc,))
        ranking = LexicalCohesionRanker().rank("payment timeout retry", index, texts)
        plan = SemanticPlanner().plan(scope_key="scope-key", task_version=1, index=index,
                                      query="payment timeout retry", ranking=ranking, must_include=("A",))
        required = plan.required_groups
        self.assertEqual(len(required), 1)
        # A's required closure is exactly {A, B, C}; D stays cold.
        member_labels = {index.node(node_id).label for member in required[0].member_refs
                         for node_id in [next(n.id for n in index.nodes if n.ref == member)]}
        self.assertEqual(member_labels, {"retry", "query-first", "no-charge"})
        self.assertIn(Ref("policy.txt", 1, 129, len(doc.content)), plan.cold_refs)

    def test_cycle_in_hard_relations_terminates(self):
        content = b"X first.\n\nY second.\n"
        doc = _doc("cyc.txt", content)
        x = SemanticNode("X", Ref("cyc.txt", 1, 0, 8), doc.source_sha256, "paragraph", "x")
        y = SemanticNode("Y", Ref("cyc.txt", 1, 10, len(content)), doc.source_sha256, "paragraph", "y")
        relations = (EvidenceRelation("X", "Y", "requires", "hard", "host"),
                     EvidenceRelation("Y", "X", "requires", "hard", "host"))
        index = validate_index("scope-key", SemanticIndexDraft((x, y), relations), (doc,))
        ranking = LexicalCohesionRanker().rank("first", index, node_texts(index, (doc,)))
        plan = SemanticPlanner().plan(scope_key="scope-key", task_version=1, index=index,
                                      query="first", ranking=ranking, must_include=("X",))
        # The closure includes both nodes exactly once despite the cycle.
        self.assertEqual(len(plan.required_groups[0].member_refs), 2)

    def test_optional_root_closure_forms_one_atomic_group(self):
        # An optional (non-must-read) root that hard-requires another node emits
        # a single group carrying the whole closure, so the planner never hands
        # the compiler a root without its dependency.
        content = b"P needs Q.\n\nQ is the dependency.\n\nR is unrelated.\n"
        doc = _doc("dep.txt", content)
        p = SemanticNode("P", Ref("dep.txt", 1, 0, 10), doc.source_sha256, "paragraph", "p")
        q = SemanticNode("Q", Ref("dep.txt", 1, 12, 33), doc.source_sha256, "paragraph", "q")
        r = SemanticNode("R", Ref("dep.txt", 1, 35, len(content)), doc.source_sha256, "paragraph", "r")
        relations = (EvidenceRelation("P", "Q", "requires", "hard", "host"),)
        index = validate_index("scope-key", SemanticIndexDraft((p, q, r), relations), (doc,))
        ranking = LexicalCohesionRanker().rank("needs", index, node_texts(index, (doc,)))
        plan = SemanticPlanner().plan(scope_key="scope-key", task_version=1, index=index, query="needs",
                                      ranking=ranking)
        p_groups = [g for g in plan.groups if "P" in g.root_ids]
        self.assertEqual(len(p_groups), 1)
        self.assertEqual(set(p_groups[0].member_refs), {p.ref, q.ref})

    def test_index_invariant_rejects_relation_to_absent_node(self):
        # The SemanticIndex constructor refuses a relation whose endpoint is not
        # a node in the index, so a deceptively complete root can never be
        # constructed directly.
        content = b"P has a dep.\n\nQ standalone.\n"
        doc = _doc("dep.txt", content)
        p = SemanticNode("P", Ref("dep.txt", 1, 0, 12), doc.source_sha256, "paragraph", "p")
        q = SemanticNode("Q", Ref("dep.txt", 1, 14, len(content)), doc.source_sha256, "paragraph", "q")
        good = validate_index("scope-key", SemanticIndexDraft((p, q), ()), (doc,))
        with self.assertRaises(InvalidRequest):
            replace(good, relations=(EvidenceRelation("P", "absent", "requires", "hard", "host"),))
        # validate_index instead drops a relation whose endpoint node was itself
        # dropped, so the surviving index is always internally consistent.
        past_end = SemanticNode("Z", Ref("dep.txt", 1, 0, 9999), doc.source_sha256, "paragraph", "z")
        dropped = validate_index("scope-key", SemanticIndexDraft(
            (p, q, past_end), (EvidenceRelation("P", "Z", "requires", "hard", "host"),)), (doc,))
        self.assertNotIn("Z", {n.id for n in dropped.nodes})
        self.assertEqual(dropped.relations, ())


class GroupedCompilerTests(RailTest):
    def _put_chain(self):
        a = self.put("a", b"AAAA retry allowed")
        b = self.put("b", b"BBBB query status first")
        c = self.put("c", b"CCCC only when not charged")
        return a, b, c

    def test_required_group_loads_whole_or_fails_never_partial(self):
        a, b, c = self._put_chain()
        group = SelectionGroup("g1", (a, b, c), required=True)
        sid = self.store.snapshot(self.scope, self.lease,
                                  (Selection(a), Selection(b), Selection(c)), groups=(group,))
        packet = self.compiler.compile(self.scope, sid, self.a.session, budget=10_000)
        self.assertEqual(set(packet.included), {a, b, c})
        # A budget that fits the framing but not the whole group must fail, not
        # load a subset of the group.
        with self.assertRaises(BudgetExceeded):
            self.compiler.compile(self.scope, sid, self.a.session, budget=len(packet.body.encode("utf-8")) - 5)

    def test_optional_group_is_atomic_all_in_or_all_cold(self):
        a, b, c = self._put_chain()
        pad = self.put("pad", b"x" * 400)
        group = SelectionGroup("g_opt", (a, b, c), required=False, priority=10)
        sid = self.store.snapshot(self.scope, self.lease,
                                  (Selection(a), Selection(b), Selection(c), Selection(pad, required=True)),
                                  groups=(group,))
        full = self.compiler.compile(self.scope, sid, self.a.session, budget=10_000)
        self.assertEqual(set(full.included), {a, b, c, pad})
        # Tighten the budget so the optional group cannot fit as a whole: it
        # must go entirely cold, never leaving one or two members behind.
        tight = self.compiler.compile(self.scope, sid, self.a.session,
                                      budget=len(full.body.encode("utf-8")) - 40)
        included = set(tight.included)
        group_present = {a, b, c} & included
        self.assertIn(group_present, ({a, b, c}, set()))
        self.assertIn(pad, included)

    def test_requested_ref_pins_whole_containing_group(self):
        a, b, c = self._put_chain()
        group = SelectionGroup("g_opt", (a, b, c), required=False, priority=1)
        sid = self.store.snapshot(self.scope, self.lease,
                                  (Selection(a), Selection(b), Selection(c)), groups=(group,))
        # Requesting only member `b` must bring the whole group into the packet.
        packet = self.compiler.compile(self.scope, sid, self.a.session, budget=10_000, requested=(b,))
        self.assertEqual(set(packet.included), {a, b, c})

    def test_shared_dependency_is_charged_once_across_two_groups(self):
        shared = self.put("shared", b"SHARED dependency bytes")
        root1 = self.put("r1", b"ROOT1 body")
        root2 = self.put("r2", b"ROOT2 body")
        g1 = SelectionGroup("g1", (root1, shared), required=True)
        g2 = SelectionGroup("g2", (root2, shared), required=True)
        sid = self.store.snapshot(self.scope, self.lease,
                                  (Selection(root1), Selection(root2), Selection(shared)), groups=(g1, g2))
        packet = self.compiler.compile(self.scope, sid, self.a.session, budget=10_000)
        # The shared dependency appears exactly once in the included set.
        self.assertEqual(packet.included.count(shared) if isinstance(packet.included, list)
                         else sum(1 for r in packet.included if r == shared), 1)
        self.assertEqual(set(packet.included), {root1, root2, shared})

    def test_group_member_outside_snapshot_is_rejected_at_snapshot_time(self):
        a, b, _ = self._put_chain()
        outside = self.put("outside", b"not selected")
        group = SelectionGroup("g", (a, outside), required=True)
        with self.assertRaises(InvalidRequest):
            self.store.snapshot(self.scope, self.lease, (Selection(a), Selection(b)), groups=(group,))

    def test_v1_snapshot_still_compiles_unchanged(self):
        a, b, _ = self._put_chain()
        sid = self.store.snapshot(self.scope, self.lease, (Selection(a, required=True), Selection(b)))
        packet = self.compiler.compile(self.scope, sid, self.a.session, budget=10_000)
        self.assertIn(a, packet.included)

    def test_unknown_snapshot_schema_is_refused(self):
        a, _, _ = self._put_chain()
        sid = self.store.snapshot(self.scope, self.lease, (Selection(a, required=True),))
        # Corrupt the stored schema tag to an unknown version; load must refuse
        # rather than silently ignore fields and degrade group atomicity.
        row = self.store.db.execute("SELECT body FROM snapshots WHERE scope=? AND id=?",
                                    (self.scope.key, sid)).fetchone()
        tampered = row["body"].replace("contextrail.snapshot/v1", "contextrail.snapshot/v9")
        self.store.db.execute("UPDATE snapshots SET body=?, sha256=? WHERE scope=? AND id=?",
                              (tampered, digest(tampered.encode("utf-8")), self.scope.key, sid))
        with self.assertRaises(IntegrityError):
            self.store.load_snapshot(self.scope, sid)


class SemanticAblationTests(RailTest):
    def test_c2_grouping_closes_a_gap_c0_and_c1_leave_open(self):
        # The deterministic ablation must show the mechanism it exists to show:
        # on the retry-condition task, C0/C1 keep the root but drop a condition,
        # while C2's required group loads the whole closure.
        from contextrail.evaluation.semantic_ablation import ABLATION_TASKS, SemanticAblation
        runner = SemanticAblation(budget=10_000)
        by_id = {task.id: runner.run(task) for task in ABLATION_TASKS}
        retry = by_id["S01"].outcomes
        self.assertTrue(retry["C0"]["root_present"])
        self.assertFalse(retry["C0"]["closure_complete"])  # baseline gap
        self.assertFalse(retry["C1"]["closure_complete"])  # ranking alone does not close it
        self.assertTrue(retry["C2"]["closure_complete"])   # grouping closes it
        self.assertEqual(tuple(retry["C2"]["missing_conditions"]), ())
        # The Python-import task's hard dependency is AST-discovered, so C2
        # closes it even without a host-declared relation.
        py = by_id["S03"].outcomes
        self.assertTrue(py["C2"]["closure_complete"])

    def test_ablation_suite_writes_reproducible_json_and_csv(self):
        from pathlib import Path
        import tempfile
        from contextrail.evaluation.semantic_ablation import run_ablation_suite
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            run_ablation_suite(out, budget=10_000)
            self.assertTrue((out / "ablation.json").exists())
            self.assertTrue((out / "ablation-metrics.csv").exists())
            self.assertTrue((out / "ablation-report.md").exists())
