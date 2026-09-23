from dataclasses import replace

from contextrail import Compiler, Lease, Selection, Store, Target
from contextrail.errors import AccessDenied, Conflict, InvalidRequest, NotFound, StaleSnapshot, Unavailable
from .common import RailTest


class HandoffTests(RailTest):
    def prepared(self):
        ref = self.put()
        sid = self.snapshot(Selection(ref, required=True))
        return self.store.prepare(self.scope, self.lease, sid, self.b)

    def test_full_lifecycle_persisted_and_activation_idempotent(self):
        h = self.prepared()
        packet = self.compiler.for_handoff(self.scope, h.id, budget=10000)
        self.assertEqual(self.store.handoff(self.scope, h.id).phase, "prepared")
        with Store(self.path, clock=lambda: self.now) as resumed:
            resumed.acknowledge(self.scope, h.id, self.b.session, packet.sha256)
            self.assertEqual(resumed.handoff(self.scope, h.id).phase, "hydrated")
            resumed.validate(self.scope, h.id)
            self.assertEqual(resumed.handoff(self.scope, h.id).phase, "validated")
            lease = resumed.activate(self.scope, h.id)
            self.assertEqual(lease, Lease(self.b.session, 1))
            self.assertEqual(resumed.activate(self.scope, h.id), lease)

    def test_stages_cannot_be_skipped(self):
        h = self.prepared()
        with self.assertRaises(Conflict):
            self.store.validate(self.scope, h.id)
        with self.assertRaises(Conflict):
            self.store.activate(self.scope, h.id)
        with self.assertRaises(Conflict):
            self.store.acknowledge(self.scope, h.id, self.b.session, "fabricated")

    def test_write_freeze_and_abort_restore_source(self):
        h = self.prepared()
        with self.assertRaises(Conflict):
            self.put("during-handoff")
        with self.assertRaises(Conflict):
            self.store.begin_action(self.scope, self.lease, "action")
        with self.assertRaises(AccessDenied):
            self.store.abort(self.scope, h.id, Lease(self.b.session, 0))
        self.store.abort(self.scope, h.id, self.lease)
        self.assertEqual(self.store.handoff(self.scope, h.id).phase, "aborted")
        self.put("after-abort")
        with self.assertRaises(Conflict):
            self.store.activate(self.scope, h.id)

    def test_receipt_bound_to_session_and_exact_packet(self):
        h = self.prepared()
        packet = self.compiler.for_handoff(self.scope, h.id, budget=10000)
        with self.assertRaises(AccessDenied):
            self.store.acknowledge(self.scope, h.id, self.a.session, packet.sha256)
        with self.assertRaises(Conflict):
            self.store.acknowledge(self.scope, h.id, self.b.session, "wrong digest")

    def test_rebuilding_context_requires_a_new_delivery_ack(self):
        hot = self.put("hot", b"must read")
        cold = self.put("cold", b"long old result " * 300)
        sid = self.snapshot(Selection(hot, required=True), Selection(cold))
        h = self.store.prepare(self.scope, self.lease, sid, self.b)
        first = self.compiler.for_handoff(self.scope, h.id, budget=2000)
        self.store.acknowledge(self.scope, h.id, self.b.session, first.sha256)
        second = self.compiler.for_handoff(self.scope, h.id, budget=10000, requested=(cold,))
        self.assertNotEqual(first.sha256, second.sha256)
        with self.assertRaises(Conflict):
            self.store.validate(self.scope, h.id)
        with self.assertRaises(Conflict):
            self.store.acknowledge(self.scope, h.id, self.b.session, first.sha256)

    def test_expiry_after_validation_blocks_activation(self):
        ref = self.put(expires_at=self.now + 10)
        sid = self.snapshot(Selection(ref, required=True))
        h = self.store.prepare(self.scope, self.lease, sid, self.b)
        packet = self.compiler.for_handoff(self.scope, h.id, budget=10000)
        self.store.acknowledge(self.scope, h.id, self.b.session, packet.sha256)
        self.store.validate(self.scope, h.id)
        self.now += 11
        with self.assertRaises(Unavailable):
            self.store.activate(self.scope, h.id)
        self.assertEqual(self.store.task(self.scope)["owner"], self.a.session)
        self.store.abort(self.scope, h.id, self.lease)

    def test_a_b_a_fences_old_a_lease_even_when_session_matches(self):
        original_a = self.lease
        first_id = self.transfer(self.b)
        old_b = self.lease
        self.transfer(self.a)
        self.assertEqual(self.lease, Lease(self.a.session, 2))
        for stale in (original_a, old_b):
            with self.assertRaises(AccessDenied):
                self.store.put(self.scope, stale, "forbidden", b"stale write", expected_revision=0)
        with self.assertRaises(Conflict):
            self.store.activate(self.scope, first_id)
        self.put("authorized")

    def test_inflight_action_blocks_handoff_and_repeated_actions_not_reserved(self):
        self.assertTrue(self.store.begin_action(self.scope, self.lease, "call-1"))
        self.assertFalse(self.store.begin_action(self.scope, self.lease, "call-1"))
        sid = self.snapshot()
        with self.assertRaises(Conflict):
            self.store.prepare(self.scope, self.lease, sid, self.b)
        self.store.finish_action(self.scope, self.lease, "call-1", state="succeeded")
        self.store.finish_action(self.scope, self.lease, "call-1", state="succeeded")
        with self.assertRaises(Conflict):
            self.store.finish_action(self.scope, self.lease, "call-1", state="failed")
        with self.assertRaises(StaleSnapshot):
            self.store.prepare(self.scope, self.lease, sid, self.b)
        self.transfer(self.b)
        self.assertFalse(self.store.begin_action(self.scope, self.lease, "call-1"))

    def test_provider_policy_and_session_identity_cannot_be_bypassed(self):
        sid = self.snapshot()
        unknown = Target("external", "unapproved", "model")
        with self.assertRaises(AccessDenied):
            self.store.prepare(self.scope, self.lease, sid, unknown)
        self.assertIsNone(self.store.task(self.scope)["handoff"])
        self.transfer(self.b)
        sid = self.snapshot()
        with self.assertRaises(Conflict):
            self.store.prepare(self.scope, self.lease, sid, replace(self.a, model="another-model"))
        with self.assertRaises(InvalidRequest):
            self.store.prepare(self.scope, self.lease, sid, self.b)

    def test_task_change_and_cross_scope_snapshot_rejected(self):
        sid = self.snapshot()
        self.put()
        with self.assertRaises(StaleSnapshot):
            self.store.prepare(self.scope, self.lease, sid, self.b)
        other_scope = replace(self.scope, task="other")
        other_lease = self.store.create_task(other_scope, self.a, "Other", allowed_providers=("provider-a", "provider-b"))
        with self.assertRaises(NotFound):
            self.store.prepare(other_scope, other_lease, sid, self.b)
