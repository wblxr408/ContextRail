from dataclasses import replace
import json

from contextrail import Compiler, ContextTools, Ref, Selection
from contextrail.errors import AccessDenied, BudgetExceeded, InvalidRequest, StaleSnapshot, Unavailable
from contextrail.models import digest
from .common import RailTest


class ContextTests(RailTest):
    def test_hot_cold_selection_exact_retrieval_and_deterministic_payload(self):
        required = self.put("contract", "原始约束\r\nID-00042".encode())
        cold = self.put("old-log", b"verbose line\n" * 1000)
        sid = self.snapshot(Selection(required, required=True), Selection(cold))
        packet = self.compiler.compile(self.scope, sid, self.a.session, budget=2000)
        self.assertEqual(packet.included, (required,))
        self.assertEqual(packet.omitted, (cold,))
        self.assertEqual(packet.units, len(packet.body.encode("utf-8")))
        self.assertEqual(packet.sha256, digest(packet.body.encode("utf-8")))
        self.assertLessEqual(packet.units, packet.budget)
        state = json.loads(packet.body)
        self.assertEqual(state["evidence"][0]["text"], "原始约束\r\nID-00042")
        self.assertEqual(state["task"]["constraints"], ["Keep original constraints"])
        self.assertEqual(state["task"]["acceptance"], ["Pass exact evidence checks"])
        self.assertEqual(self.compiler.compile(self.scope, sid, self.a.session, budget=2000), packet)
        tools = ContextTools(self.store, self.scope, self.a.session)
        read = tools.call("context.get", {"name": cold.name, "revision": cold.revision})
        self.assertEqual(read["content"].encode(read["encoding"]), b"verbose line\n" * 1000)

    def test_budget_counts_full_envelope_and_never_truncates_mandatory_evidence(self):
        required = self.put(content=b"must remain" * 1000)
        sid = self.snapshot(Selection(required, required=True))
        with self.assertRaises(BudgetExceeded):
            self.compiler.compile(self.scope, sid, self.a.session, budget=500)
        self.assertEqual(self.store.get(self.scope, required).content, b"must remain" * 1000)
        empty = self.snapshot()
        with self.assertRaises(BudgetExceeded):
            self.compiler.compile(self.scope, empty, self.a.session, budget=1)

    def test_requested_cold_page_is_pinned_and_other_session_view_unchanged(self):
        required = self.put("contract", b"required")
        cold = self.put("old", b"long " * 1000)
        sid = self.snapshot(Selection(required, required=True), Selection(cold))
        h = self.store.prepare(self.scope, self.lease, sid, self.b)
        a_view = self.compiler.compile(self.scope, sid, self.a.session, budget=2000)
        b_view = self.compiler.for_handoff(self.scope, h.id, budget=10000, requested=(cold,))
        self.assertIn(cold, b_view.included)
        self.assertEqual(a_view.omitted, (cold,))
        self.assertEqual(self.compiler.compile(self.scope, sid, self.a.session, budget=2000), a_view)
        with self.assertRaises(BudgetExceeded):
            self.compiler.for_handoff(self.scope, h.id, budget=2000, requested=(cold,))

    def test_optional_priority_is_honored_and_whole_pages_kept(self):
        low = self.put("low", b"low" * 500)
        high = self.put("high", b"high" * 500)
        sid = self.snapshot(Selection(low, priority=0), Selection(high, priority=10))
        full = self.compiler.compile(self.scope, sid, self.a.session, budget=10000)
        partial = self.compiler.compile(self.scope, sid, self.a.session, budget=full.units - 1000)
        self.assertEqual(partial.included, (high,))
        self.assertEqual(partial.omitted, (low,))
        self.assertEqual(json.loads(partial.body)["evidence"][0]["text"], (b"high" * 500).decode())

    def test_custom_measurement_is_explicit_and_validated(self):
        sid = self.snapshot()
        compiler = Compiler(self.store, measure=lambda body: len(body), unit="test_characters")
        packet = compiler.compile(self.scope, sid, self.a.session, budget=10000)
        self.assertEqual(packet.unit, "test_characters")
        self.assertEqual(packet.units, len(packet.body))
        with self.assertRaises(InvalidRequest):
            Compiler(self.store, unit="tokens")
        with self.assertRaises(InvalidRequest):
            Compiler(self.store, measure=lambda body: -1).compile(self.scope, sid, self.a.session, budget=10000)

    def test_expired_evidence_fails_instead_of_using_summary_or_stale_packet(self):
        ref = self.put(expires_at=self.now + 1)
        sid = self.snapshot(Selection(ref))
        self.now += 2
        with self.assertRaises(Unavailable):
            self.compiler.compile(self.scope, sid, self.a.session, budget=10000)

    def test_snapshot_changes_detected_before_compile(self):
        ref = self.put()
        sid = self.snapshot(Selection(ref))
        self.put(content=b"updated", expected_revision=1)
        with self.assertRaises(StaleSnapshot):
            self.compiler.compile(self.scope, sid, self.a.session, budget=10000)

    def test_unknown_session_and_requested_page_rejected(self):
        ref = self.put()
        sid = self.snapshot()
        with self.assertRaises(AccessDenied):
            self.compiler.compile(self.scope, sid, "unknown", budget=10000)
        with self.assertRaises(AccessDenied):
            self.compiler.compile(self.scope, sid, self.a.session, budget=10000, requested=(ref,))

    def test_binary_evidence_remains_cold_but_is_recoverable(self):
        ref = self.put(content=b"\xff\xfe\x00\xab", media_type="application/octet-stream")
        sid = self.snapshot(Selection(ref))
        packet = self.compiler.compile(self.scope, sid, self.a.session, budget=10000)
        self.assertEqual(packet.omitted, (ref,))
        with self.assertRaises(InvalidRequest):
            self.compiler.compile(self.scope, sid, self.a.session, budget=10000, requested=(ref,))

    def test_unaligned_unicode_range_never_replaced_with_lossy_text(self):
        ref = self.put(content="中文".encode())
        unaligned = replace(ref, start=1, end=2)
        sid = self.snapshot(Selection(unaligned, required=True))
        with self.assertRaises(InvalidRequest):
            self.compiler.compile(self.scope, sid, self.a.session, budget=10000)

    def test_duplicate_selection_rejected(self):
        ref = self.put()
        with self.assertRaises(InvalidRequest):
            self.snapshot(Selection(ref), Selection(ref, required=True))
