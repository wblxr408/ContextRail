import json

from contextrail import (CacheLayout, CacheLayoutPolicy, CompactionEstimate, CompactionLifecycle, ContextTools, IndexPolicy,
                         ChunkedEvidenceSelector, LexicalEvidenceSelector, ModelPolicyProvider, RoutingDecision,
                         RoutingRequest, Selection, StructuralChunker, Target, UsageLedger, ContextController, Ref,
                         RequestBudget)
from contextrail.errors import AccessDenied, BudgetExceeded, InvalidRequest, StaleSnapshot
from .common import RailTest


class AdvancedContextTests(RailTest):
    def test_lexical_selector_ranks_current_evidence_and_preserves_explicit_must_reads(self):
        contract = self.put("deploy-contract", b"Deploy endpoint requires admin approval and region=cn.")
        guide = self.put("release-guide", b"Release checklist and ordinary deployment notes.")
        private = self.put("safety-note", b"Always retain this safety constraint even when not queried.")
        selector = LexicalEvidenceSelector(self.store)
        plan = selector.select(self.scope, "deploy endpoint admin approval", must_include=(private,),
                               cache_stable=(private,), max_optional=1)
        self.assertEqual(plan.selections[0], Selection(private, required=True, cache_stable=True))
        self.assertEqual(plan.selections[1].ref, contract)
        self.assertTrue(plan.ranked[0].score > plan.ranked[1].score)
        self.assertEqual(plan.unmatched_terms, ())
        self.assertEqual(self.store.current_refs(self.scope), [contract, guide, private])

    def test_lexical_selector_rejects_noncurrent_required_refs_and_exposes_unmatched_terms(self):
        old = self.put("contract", b"old compatibility rule")
        self.put("contract", b"new compatibility rule", expected_revision=1)
        selector = LexicalEvidenceSelector(self.store)
        with self.assertRaises(AccessDenied):
            selector.select(self.scope, "compatibility", must_include=(old,))
        plan = selector.select(self.scope, "unrepresented-needle", max_optional=2)
        self.assertEqual(plan.selections, ())
        self.assertEqual(plan.unmatched_terms, ("unrepresented-needle",))

    def test_lexical_selector_uses_chinese_bigrams_and_reports_candidate_truncation(self):
        contract = self.put("api-contract", "鉴权接口必须保留兼容性。".encode("utf-8"))
        selector = LexicalEvidenceSelector(self.store, max_artifacts=1)
        plan = selector.select(self.scope, "保留鉴权", max_optional=1)
        self.assertEqual(plan.ranked[0].ref, contract)
        self.assertFalse(plan.candidate_truncated)

    def test_structural_chunker_preserves_exact_python_byte_spans_and_import_dependency(self):
        source = b"import math\n\ndef calculate(value):\n    return math.ceil(value)\n\nclass Runner:\n    pass\n"
        ref = self.put("calculator.py", source)
        artifact = self.store.get(self.scope, ref)
        chunks = StructuralChunker().chunk(ref, source, source_sha256=artifact.sha256)
        function = next(chunk for chunk in chunks if chunk.path == "calculate")
        self.assertEqual(self.store.get(self.scope, function.ref).content, b"def calculate(value):\n    return math.ceil(value)\n")
        self.assertEqual(self.store.get(self.scope, function.dependencies[0]).content, b"import math\n")

    def test_chunked_selector_returns_compiler_ready_function_span(self):
        source = self.put("calculator.py", b"import math\n\ndef calculate(value):\n    return math.ceil(value)\n\ndef unrelated():\n    return 0\n")
        plan = ChunkedEvidenceSelector(self.store).select(self.scope, "calculate", max_optional=1)
        self.assertEqual(plan.ranked[0].chunk.path, "calculate")
        self.assertTrue(any(selection.ref.start > 0 for selection in plan.selections))
        self.assertEqual(plan.recovery_dependencies[0][0].name, "calculator.py")
        self.assertEqual(plan.recovery_dependencies[0][1][0].start, 0)
        sid = self.snapshot(*plan.selections)
        packet = self.compiler.compile(self.scope, sid, self.a.session, budget=10_000)
        self.assertIn("def calculate", packet.body)
        self.assertNotIn("def unrelated", packet.body)

    def test_task_state_is_source_linked_supersedable_and_snapshot_bound(self):
        source = self.put("contract", b"API v2 remains compatible")
        first = self.store.add_task_state(self.scope, self.lease, kind="decision", text="Use API v2.",
                                          sources=(source,))
        second = self.store.add_task_state(self.scope, self.lease, kind="decision", text="Use API v2 with fallback.",
                                           sources=(source,), supersedes=(first.id,))
        self.assertEqual(self.store.task_state(self.scope), (second,))
        sid = self.snapshot(Selection(source, required=True))
        packet = self.compiler.compile(self.scope, sid, self.a.session, budget=10_000)
        self.assertIn("Use API v2 with fallback.", packet.body)
        self.assertNotIn('"text":"Use API v2."', packet.body)
        with self.assertRaises(InvalidRequest):
            self.store.add_task_state(self.scope, self.lease, kind="decision", text="Unsupported fact")

    def test_context_controller_recompiles_recovered_pages_against_final_request_budget(self):
        required = self.put("contract", b"current contract")
        full_cold = self.put("diagnostic", b"needed diagnostic\n" + b"x" * 8_000)
        cold = Ref(full_cold.name, full_cold.revision, 0, len(b"needed diagnostic\n"))
        sid = self.snapshot(Selection(required, required=True), Selection(cold))
        controller = ContextController(self.compiler, self.scope, sid, self.a.session,
                                       budget=RequestBudget(10_000, system_units=200, protocol_units=300,
                                                            output_reserve=400, tool_reserve=100, safety_reserve=100))
        first = controller.compile("locate")
        self.assertEqual(first.requested, ())
        self.assertTrue(controller.observe_tool_result("context.get", {"ref": {"name": cold.name, "revision": cold.revision,
                                                                          "start": cold.start, "end": cold.end}}))
        self.assertFalse(controller.request(cold))
        second = controller.compile("modify")
        self.assertIn(cold, second.packet.included)
        self.assertEqual(second.recovery_count, 1)
        self.assertLessEqual(second.packet.units + 200 + 300 + 400 + 100 + 100, 10_000)
        constrained = ContextController(self.compiler, self.scope, sid, self.a.session,
                                        budget=RequestBudget(500, output_reserve=100))
        with self.assertRaises(BudgetExceeded):
            constrained.compile("locate")

    def test_context_controller_recovers_declared_dependencies_and_rejects_outside_snapshot(self):
        contract = self.put("contract", b"interface signature")
        implementation = self.put("implementation", b"implementation depends on interface")
        outside = self.put("outside", b"not selected")
        sid = self.snapshot(Selection(contract), Selection(implementation))
        controller = ContextController(self.compiler, self.scope, sid, self.a.session,
                                       budget=RequestBudget(10_000),
                                       dependencies={implementation: (contract,)})
        self.assertTrue(controller.request(implementation))
        turn = controller.compile("modify")
        self.assertEqual(set(turn.requested), {contract, implementation})
        self.assertTrue({contract, implementation}.issubset(set(turn.packet.included)))
        with self.assertRaises(InvalidRequest):
            controller.request(outside)

    def test_cache_layout_reserves_budget_and_bounds_cold_index(self):
        stable = self.put("contract", b"stable contract")
        required = self.put("objective", b"dynamic requirement")
        first = self.put("old-1", b"x" * 10_000)
        second = self.put("old-2", b"y" * 10_000)
        sid = self.snapshot(Selection(stable, required=True, cache_stable=True),
                            Selection(required, required=True), Selection(first), Selection(second))
        policy = CacheLayoutPolicy(CacheLayout("provider-cache-key", output_reserve=100, tool_reserve=50),
                                   index=IndexPolicy(1))
        packet = policy.compile(self.compiler, self.scope, sid, self.a.session, budget=3_000)
        body = json.loads(packet.body)
        self.assertEqual(body["schema"], "contextrail.context/v2")
        self.assertEqual(body["stable_evidence"][0]["ref"]["name"], "contract")
        self.assertEqual(body["available_total"], 2)
        self.assertEqual(len(body["available"]), 1)
        self.assertTrue(body["available_truncated"])
        self.assertEqual(packet.reserved, 150)
        self.assertLessEqual(packet.units + packet.reserved, packet.budget)
        self.assertEqual(packet.stable_digest,
                         policy.compile(self.compiler, self.scope, sid, self.a.session, budget=3_000).stable_digest)
        with self.assertRaises(InvalidRequest):
            Selection(first, cache_stable=True)

    def test_bounded_cold_index_pages_only_optional_snapshot_entries(self):
        hot = self.put("hot", b"must stay")
        first = self.put("cold-1", b"first")
        second = self.put("cold-2", b"second")
        sid = self.snapshot(Selection(hot, required=True), Selection(first), Selection(second))
        tools = ContextTools(self.store, self.scope, self.a.session)
        page = tools.call("context.list", {"snapshot": sid, "offset": 0, "limit": 1})
        later = tools.call("context.list", {"snapshot": sid, "offset": 1, "limit": 1})
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["entries"][0]["ref"]["name"], "cold-1")
        self.assertEqual(later["entries"][0]["ref"]["name"], "cold-2")
        self.assertEqual(tools.call("context.get", {"name": second.name, "revision": second.revision})["content"], "second")

    def test_usage_ledger_records_only_a_verified_packet_and_host_reported_units(self):
        sid = self.snapshot()
        packet = self.compiler.compile(self.scope, sid, self.a.session, budget=10_000)
        ledger = UsageLedger(self.store)
        record = ledger.record(self.scope, packet, unit="provider_tokens", input_units=500,
                               cached_input_units=320, cache_write_units=60, output_units=90, latency_ms=250)
        self.assertEqual((record.provider, record.model), (self.a.provider, self.a.model))
        self.assertEqual(ledger.totals(self.scope), {"requests": 1, "input_units": 500,
                                                      "cached_input_units": 320, "cache_write_units": 60,
                                                      "output_units": 90, "latency_ms": 250,
                                                      "aggregate_requests": 1, "request_ledger_requests": 0,
                                                      "request_ledger_usage_known": 0})
        with self.assertRaises(InvalidRequest):
            ledger.record(self.scope, packet, unit="provider_tokens", input_units=10, cached_input_units=11)

    def test_request_usage_preserves_unknown_values_and_deduplicates_provider_request_ids(self):
        sid = self.snapshot()
        packet = self.compiler.compile(self.scope, sid, self.a.session, budget=10_000)
        ledger = UsageLedger(self.store)
        record = ledger.record_request(self.scope, packet, unit="provider_tokens", input_units=None,
                                       output_units=None, request_id="provider-request-1", run_id="run-1",
                                       request_digest=packet.sha256)
        self.assertFalse(record.usage_known)
        self.assertIsNone(record.input_units)
        row = self.store.db.execute("SELECT input_units,output_units,usage_known,request_digest FROM request_usage WHERE id=?",
                                    (record.id,)).fetchone()
        self.assertEqual(tuple(row), (None, None, 0, packet.sha256))
        with self.assertRaises(Exception):
            ledger.record_request(self.scope, packet, unit="provider_tokens", input_units=1, output_units=1,
                                  request_id="provider-request-1", run_id="run-1")

    def test_request_ledger_usage_is_included_in_totals(self):
        # Regression: totals() must aggregate the request-level ledger, not only
        # the legacy usage_records table, or a host on the newer path reads zero.
        sid = self.snapshot()
        packet = self.compiler.compile(self.scope, sid, self.a.session, budget=10_000)
        ledger = UsageLedger(self.store)
        ledger.record_request(self.scope, packet, unit="provider_tokens", input_units=400,
                              cached_input_units=100, output_units=50, latency_ms=30,
                              request_id="req-agg-1", run_id="run-1")
        totals = ledger.totals(self.scope)
        self.assertEqual(totals["input_units"], 400)
        self.assertEqual(totals["output_units"], 50)
        self.assertEqual(totals["requests"], 1)
        self.assertEqual(totals["request_ledger_requests"], 1)
        self.assertEqual(totals["request_ledger_usage_known"], 1)
        self.assertEqual(totals["aggregate_requests"], 0)

    def test_request_usage_records_after_task_version_advances(self):
        # Regression: a request already happened; a later write bumps task version.
        # Recording spent usage must not fail with StaleSnapshot and lose cost.
        sid = self.snapshot()
        packet = self.compiler.compile(self.scope, sid, self.a.session, budget=10_000)
        self.put("late-write", b"advances the task version after the request")
        ledger = UsageLedger(self.store)
        record = ledger.record_request(self.scope, packet, unit="provider_tokens", input_units=120,
                                       output_units=20, request_id="req-late-1", run_id="run-1")
        self.assertTrue(record.usage_known)
        self.assertEqual(ledger.totals(self.scope)["input_units"], 120)

    def test_cache_stable_digest_excludes_dynamic_task_state(self):
        stable = self.put("contract", b"stable contract")
        sid = self.snapshot(Selection(stable, required=True, cache_stable=True))
        layout = CacheLayout("provider-cache-key")
        first = self.compiler.compile(self.scope, sid, self.a.session, budget=10_000, layout=layout)
        state = self.store.task(self.scope)
        self.store.update_task(self.scope, self.lease, objective="New dynamic task state",
                               constraints=tuple(state["constraints"]), acceptance=tuple(state["acceptance"]),
                               expected_version=state["version"])
        sid = self.snapshot(Selection(stable, required=True, cache_stable=True))
        second = self.compiler.compile(self.scope, sid, self.a.session, budget=10_000, layout=layout)
        self.assertEqual(first.stable_digest, second.stable_digest)

    def test_model_policy_validates_candidate_and_leaves_handoff_to_host(self):
        request = RoutingRequest("subagent", (self.a, self.b), estimated_input_units=1_200,
                                 cache_key="provider-cache-key")
        policy = ModelPolicyProvider(lambda _: RoutingDecision(self.b, "lower latency for independent review"))
        decision = policy.decide(self.store, self.scope, request)
        self.assertEqual(decision.target, self.b)
        self.assertEqual(self.store.task(self.scope)["owner"], self.a.session)
        self.assertTrue(any(event["kind"] == "routing.decided" for event in self.store.events(self.scope)))
        bad = Target("other", "unapproved-provider", "other-model")
        with self.assertRaises(AccessDenied):
            ModelPolicyProvider(lambda _: bad).decide(
                self.store, self.scope, RoutingRequest("main", (bad,), estimated_input_units=1))

    def test_compaction_lifecycle_persists_summary_with_exact_spans_and_tool_drilldown(self):
        source = self.put("design", b"The original evidence says: preserve revision and hash.")
        sid = self.snapshot(Selection(source, required=True))
        observed = []
        lifecycle = CompactionLifecycle(self.store, before=lambda item: observed.append(("before", item.phase)),
                                        after=lambda item: observed.append(("after", item.id)))
        prepared = lifecycle.begin(self.scope, self.lease, sid, reason="context window threshold")
        summary = lifecycle.complete(self.scope, self.lease, prepared.id, title="Version invariant",
                                     text="Preserve revision and hash before a handoff.", spans=(source,))
        self.assertEqual(observed, [("before", "prepared"), ("after", summary.id)])
        tools = ContextTools(self.store, self.scope, self.a.session)
        found = tools.call("context.summary_search", {"query": "revision"})
        self.assertEqual(found["summaries"][0]["id"], summary.id)
        fetched = tools.call("context.summary_get", {"id": summary.id})
        self.assertEqual(fetched["spans"][0]["name"], source.name)
        self.assertTrue(fetched["snapshot_current"])
        raw = tools.call("context.get", {"name": source.name, "revision": source.revision})
        self.assertIn("original evidence", raw["content"])

    def test_compaction_rejects_a_stale_snapshot_and_out_of_snapshot_span(self):
        source = self.put("design", b"original")
        unrelated = self.put("other", b"outside")
        sid = self.snapshot(Selection(source, required=True))
        lifecycle = CompactionLifecycle(self.store)
        prepared = lifecycle.begin(self.scope, self.lease, sid, reason="threshold")
        with self.assertRaises(AccessDenied):
            lifecycle.complete(self.scope, self.lease, prepared.id, title="Bad span", text="Not allowed", spans=(unrelated,))
        # The failed validation does not consume the prepared lifecycle. A task
        # revision made afterwards makes its original snapshot stale at commit.
        self.put("other", b"new", expected_revision=1)
        with self.assertRaises(StaleSnapshot):
            lifecycle.complete(self.scope, self.lease, prepared.id, title="Now stale", text="Still anchored", spans=(source,))

    def test_compaction_validator_runs_before_commit_and_keeps_rejectable_draft_prepared(self):
        source = self.put("design", b"Keep exact error E409.")
        sid = self.snapshot(Selection(source, required=True))
        lifecycle = CompactionLifecycle(self.store, validate=lambda _: (_ for _ in ()).throw(InvalidRequest("missing exact error")))
        prepared = lifecycle.begin(self.scope, self.lease, sid, reason="threshold")
        with self.assertRaisesRegex(InvalidRequest, "missing exact error"):
            lifecycle.complete(self.scope, self.lease, prepared.id, title="Bad", text="Omitted details", spans=(source,))
        self.assertEqual(self.store.compaction(self.scope, prepared.id).phase, "prepared")

    def test_compaction_starts_only_when_expected_net_savings_are_positive(self):
        self.assertFalse(CompactionLifecycle.should_begin(CompactionEstimate(100, 80, 10, 10)))
        self.assertTrue(CompactionLifecycle.should_begin(CompactionEstimate(101, 80, 10, 10)))
