from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
import sqlite3
import threading

from contextrail import Lease, Ref, Selection, Store, Target
from contextrail.errors import AccessDenied, Conflict, IntegrityError, InvalidRequest, NotFound, StaleSnapshot, Unavailable
from .common import RailTest


class StoreTests(RailTest):
    def test_original_bytes_ranges_and_revisions_survive_reopen(self):
        data = "\ufeff精确标识符CR-00042\r\n\"x\"\\\x00".encode()
        first = self.put(content=data)
        second = self.put(content=b"new data", expected_revision=1)
        with Store(self.path, clock=lambda: self.now) as reopened:
            self.assertEqual(reopened.get(self.scope, first).content, data)
            self.assertEqual(reopened.get(self.scope, second).content, b"new data")
            self.assertEqual(reopened.get(self.scope, replace(first, start=3, end=9)).content, data[3:9])
            self.assertEqual(reopened.get(self.scope, replace(first, start=len(data))).content, b"")

    def test_compare_and_swap_preserves_previous_work(self):
        ref = self.put()
        with self.assertRaises(Conflict):
            self.put(content=b"overwrite")
        self.assertEqual(self.store.get(self.scope, ref).content, b"exact original evidence")
        self.assertEqual(self.store.task(self.scope)["version"], 1)

    def test_competing_writers_only_one_commits(self):
        ref = self.put()
        barrier = threading.Barrier(2)

        def writer(value):
            with Store(self.path, clock=lambda: self.now) as other:
                barrier.wait(timeout=10)
                try:
                    other.put(self.scope, self.lease, ref.name, value, expected_revision=1)
                    return "committed"
                except Conflict:
                    return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(writer, (b"first writer", b"second writer")))
        self.assertCountEqual(results, ["committed", "conflict"])
        self.assertEqual(self.store.task(self.scope)["version"], 2)
        self.assertIn(self.store.get(self.scope, Ref(ref.name, 2)).content, (b"first writer", b"second writer"))

    def test_each_scope_dimension_isolated_in_reads_search_and_snapshots(self):
        ref = self.put(content=b"classified marker")
        sid = self.snapshot(Selection(ref))
        for dimension in ("tenant", "project", "branch", "task"):
            with self.subTest(dimension=dimension):
                other = replace(self.scope, **{dimension: "other"})
                self.store.create_task(other, self.a, "Other task", allowed_providers=("provider-a",))
                with self.assertRaises(NotFound):
                    self.store.get(other, ref)
                with self.assertRaises(NotFound):
                    self.store.load_snapshot(other, sid)
                self.assertEqual(self.store.search(other, "marker"), [])

    def test_dedup_is_scope_local(self):
        self.put("one", b"shared bytes")
        self.put("two", b"shared bytes")
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM blobs").fetchone()[0], 1)
        other = replace(self.scope, task="another")
        lease = self.store.create_task(other, self.a, "Another", allowed_providers=("provider-a",))
        self.store.put(other, lease, "three", b"shared bytes", expected_revision=0)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM blobs").fetchone()[0], 2)

    def test_expired_payload_unavailable_and_purged_without_resurrection(self):
        ref = self.put(expires_at=self.now + 1)
        self.now += 2
        with self.assertRaises(Unavailable):
            self.store.get(self.scope, ref)
        self.assertEqual(self.store.search(self.scope, "evidence"), [])
        self.assertEqual(self.store.purge(self.scope), 1)
        with self.assertRaises(Unavailable):
            self.store.get(self.scope, ref)
        updated = self.put(expected_revision=1)
        self.assertEqual(updated.revision, 2)
        with self.assertRaises(Unavailable):
            self.store.get(self.scope, ref)

    def test_purge_keeps_blob_with_a_live_reference(self):
        old = self.put("old", b"same", expires_at=self.now + 1)
        live = self.put("live", b"same")
        self.now += 2
        self.assertEqual(self.store.purge(self.scope), 0)
        self.assertEqual(self.store.get(self.scope, live).content, b"same")
        self.store.invalidate(self.scope, self.lease, live)
        self.assertEqual(self.store.purge(self.scope), 1)
        for ref in (old, live):
            with self.assertRaises(Unavailable):
                self.store.get(self.scope, ref)

    def test_search_current_literal_only_and_limits(self):
        self.put("old-file", b"removed search word")
        self.put("old-file", b"updated", expected_revision=1)
        percent = self.put("literal%", b"one")
        self.put("ordinary", b"two")
        self.assertEqual(self.store.search(self.scope, "search word"), [])
        self.assertEqual(self.store.search(self.scope, "%"), [percent])
        self.assertEqual(self.store.search(self.scope, "' OR 1=1 --"), [])
        for limit in (True, 0, 101):
            with self.assertRaises(InvalidRequest):
                self.store.search(self.scope, "one", limit=limit)

    def test_corrupt_payload_and_snapshot_rejected(self):
        ref = self.put()
        sid = self.snapshot(Selection(ref))
        self.store.db.execute("UPDATE blobs SET content=?", (b"tampered",))
        with self.assertRaises(IntegrityError):
            self.store.get(self.scope, ref)
        with self.assertRaises(IntegrityError):
            self.store.search(self.scope, "tampered")
        self.store.db.execute("UPDATE snapshots SET body='{}'")
        with self.assertRaises(IntegrityError):
            self.store.load_snapshot(self.scope, sid)

    def test_snapshot_cannot_silently_use_outdated_head(self):
        old = self.put()
        self.put(expected_revision=1, content=b"new")
        with self.assertRaises(StaleSnapshot):
            self.snapshot(Selection(old))
        historical = self.snapshot(Selection(old, current=False))
        self.assertEqual(self.store.load_snapshot(self.scope, historical)["evidence"][0]["ref"]["revision"], 1)

    def test_task_revision_invalidates_snapshot_and_uses_cas(self):
        sid = self.snapshot()
        version = self.store.update_task(self.scope, self.lease, expected_version=0,
                                         objective="Changed goal", constraints=("new constraint",), acceptance=("new gate",))
        self.assertEqual(version, 1)
        with self.assertRaises(StaleSnapshot):
            self.store.load_snapshot(self.scope, sid)
        with self.assertRaises(Conflict):
            self.store.update_task(self.scope, self.lease, expected_version=0, objective="Lost update", constraints=(), acceptance=())
        self.assertEqual(self.store.task(self.scope)["objective"], "Changed goal")

    def test_audit_does_not_include_payload_name_or_objective(self):
        self.put("private/path.py", b"secret_marker")
        self.snapshot()
        audit = json.dumps(self.store.events(self.scope))
        for original in ("private/path.py", "secret_marker", "Exact evidence handoff", "Keep original constraints"):
            self.assertNotIn(original, audit)

    def test_input_validation_and_invalid_ranges(self):
        ref = self.put()
        for kwargs in ({"start": -1}, {"revision": True}, {"end": 1, "start": 2}, {"name": "\n"}):
            with self.assertRaises(InvalidRequest):
                replace(ref, **kwargs)
        with self.assertRaises(InvalidRequest):
            self.store.get(self.scope, replace(ref, end=1000000))
        for expires in (float("nan"), float("inf"), self.now, True):
            with self.assertRaises(InvalidRequest):
                self.put("bad-expiry", expires_at=expires)
        with self.assertRaises(InvalidRequest):
            self.put("bad-content", content="not bytes")

    def test_unapproved_provider_and_fake_lease_rejected(self):
        other = replace(self.scope, task="private")
        with self.assertRaises(AccessDenied):
            self.store.create_task(other, self.b, "goal", allowed_providers=("provider-a",))
        with self.assertRaises(NotFound):
            self.store.task(other)
        with self.assertRaises(AccessDenied):
            self.store.put(self.scope, Lease("fake", 0), "name", b"data", expected_revision=0)

    def test_foreign_database_and_unknown_schema_are_preserved(self):
        other = self.path.parent / "foreign.sqlite3"
        with closing(sqlite3.connect(other)) as db:
            db.execute("CREATE TABLE precious(value TEXT)")
            db.execute("INSERT INTO precious VALUES('keep')")
            db.commit()
        with self.assertRaises(Conflict):
            Store(other)
        with closing(sqlite3.connect(other)) as db:
            self.assertEqual(db.execute("SELECT value FROM precious").fetchone()[0], "keep")
            self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0], 1)
            db.execute("PRAGMA user_version=1")
        with self.assertRaises(Conflict):
            Store(other)
        self.store.db.execute("PRAGMA user_version=99")
        with self.assertRaises(Conflict):
            Store(self.path)

    def test_missing_payload_is_not_silently_hidden_by_search(self):
        self.put(content=b"important")
        self.store.db.execute("DELETE FROM blobs")
        with self.assertRaises(IntegrityError):
            self.store.search(self.scope, "important")
