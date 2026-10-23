"""Current C03 contract: derived searchable spans and explicit coverage."""

import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from contextrail import ContextTools, Lease, Ref, Scope, Selection, Store, Target
from contextrail.errors import AccessDenied, BudgetExceeded, InvalidRequest, NotFound, Unavailable
from contextrail.models import canonical, digest
from contextrail.project import load_project
from contextrail.store import APPLICATION_ID, SCHEMA_V1, SCHEMA_V3, SCHEMA_V4, SCHEMA_V5, SCHEMA_V6


class TaskIndexTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rail-index-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.path = self.workspace / "store.sqlite3"
        self.scope = load_project(self.workspace, overrides={"project_id": "index-project", "task": "index-task"}).scope
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.lease = self.store.create_task(self.scope, Target("owner", "host", "unknown"), "query indexed evidence", allowed_providers=("host",))
        self.tools = ContextTools(self.store, self.scope, "owner")

    def query(self, query, **options):
        return self.tools.call("context.search_indexed", {"query": query, **options})

    def put(self, name, body, *, revision=0, **options):
        return self.store.put(self.scope, self.lease, name, body, expected_revision=revision, **options)

    def test_cli_returns_searchable_span_with_exact_chinese_emoji_crlf_readback(self):
        raw = ("早期无关资料🙂\r\n" * 2500 + "\r\n错误修复 socketretry CR-7429\r\n").encode()
        self.put("observed.log", raw)
        args = [sys.executable, "-m", "contextrail", "search-indexed", "--workspace", str(self.workspace),
                "--store", str(self.path), "--project-id", self.scope.project, "--task", self.scope.task,
                "--session", "owner", "--query", "socketretry"]
        reply = subprocess.run(args, capture_output=True, timeout=20)
        self.assertEqual(reply.returncode, 0, reply.stderr.decode("utf-8", "replace"))
        result = json.loads(reply.stdout)
        self.assertEqual(result["status"], "matched")
        hit = result["hits"][0]
        self.assertGreater(hit["ref"]["start"], 0)
        self.assertLessEqual(hit["ref"]["end"] - hit["ref"]["start"], 8192)
        read = self.tools.call("context.get", hit["ref"])
        self.assertIn("错误修复 socketretry CR-7429\r\n", read["content"])
        self.assertEqual(read["content"].encode(), raw[hit["ref"]["start"]:hit["ref"]["end"]])
        self.assertTrue(result["index"]["complete"])
        self.assertTrue(self.query("错误")["hits"])

    def test_bm25_candidates_are_current_and_scope_bound_even_with_identical_names(self):
        self.put("same", b"retry socket timeout fixed")
        self.put("noise", b"retry " * 500 + b"unrelated filler " * 500)
        other = Scope(self.scope.tenant, self.scope.project, self.scope.branch, "other-task")
        lease = self.store.create_task(other, Target("other", "host", "unknown"), "other", allowed_providers=("host",))
        self.store.put(other, lease, "same", b"retry secret_cross_task_identifier", expected_revision=0)
        body = self.query("retry socket")
        self.assertEqual(body["hits"][0]["ref"]["name"], "same")
        self.assertTrue(all("secret_cross_task_identifier" not in item["snippet"] for item in body["hits"]))
        self.assertEqual(self.query("secret_cross_task_identifier")["hits"], [])
        with self.assertRaises(InvalidRequest):
            self.tools.call("context.search_indexed", {"query": "retry", "scope": asdict(other)})

    def test_new_revision_invalidation_expiry_and_purge_remove_current_candidates(self):
        old = self.put("revised", b"oldneedle original")
        self.put("revised", b"newneedle current", revision=1)
        self.assertEqual(self.query("oldneedle")["hits"], [])
        self.assertEqual(self.query("newneedle")["hits"][0]["ref"]["revision"], 2)
        self.assertEqual(self.store.get(self.scope, old).content, b"oldneedle original")
        self.store.invalidate(self.scope, self.lease, Ref("revised", 2))
        self.assertEqual(self.query("newneedle")["hits"], [])
        moment = [100.0]
        self.store.clock = lambda: moment[0]
        expiring = self.put("expires", b"expiringneedle", expires_at=110)
        self.assertTrue(self.query("expiringneedle")["hits"])
        moment[0] = 111
        self.assertEqual(self.query("expiringneedle")["hits"], [])
        self.store.purge(self.scope)
        with self.assertRaises(Unavailable):
            self.store.get(self.scope, expiring)
        self.assertEqual(self.query("expiringneedle")["hits"], [])

    def test_index_failure_rolls_back_artifacts_version_and_first_index_insert(self):
        before = self.store.task(self.scope)
        self.store.db.execute("CREATE TRIGGER fail_second_index BEFORE INSERT ON evidence_index_state WHEN new.name='second' BEGIN SELECT RAISE(ABORT,'injected second index failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.put_many(self.scope, self.lease, [
                {"name": "first", "content": b"rollbackneedle first", "expected_revision": 0},
                {"name": "second", "content": b"rollbackneedle second", "expected_revision": 0}])
        self.assertEqual(self.store.task(self.scope), before)
        with self.assertRaises(NotFound):
            self.store.get(self.scope, Ref("first", 1))
        self.assertEqual(self.query("rollbackneedle")["hits"], [])

    def test_v6_task_snapshot_survive_upgrade_pending_then_owner_rebuild(self):
        legacy_path = self.workspace / "legacy.sqlite3"
        connection = sqlite3.connect(legacy_path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.executescript(SCHEMA_V1 + SCHEMA_V3 + SCHEMA_V4 + SCHEMA_V5 + SCHEMA_V6)
        connection.execute("PRAGMA application_id=" + str(APPLICATION_ID))
        connection.execute("PRAGMA user_version=6")
        legacy = Store.__new__(Store)
        legacy.db, legacy.clock = connection, time.time
        lease = legacy.create_task(self.scope, Target("owner", "host", "unknown"), "preserved goal", constraints=("literal constraint",), allowed_providers=("host",))
        raw = "legacyneedle 原始约束\r\n".encode()
        connection.execute("INSERT INTO blobs VALUES(?,?,?)", (self.scope.key, digest(raw), raw))
        connection.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,0)", (self.scope.key, "legacy", 1, digest(raw), len(raw), "text/plain", None))
        connection.execute("INSERT INTO heads VALUES(?,?,?)", (self.scope.key, "legacy", 1))
        sid = legacy.snapshot(self.scope, lease, (Selection(Ref("legacy", 1), required=True),))
        before = legacy.task(self.scope)
        legacy.close()
        with Store(legacy_path) as upgraded:
            tools = ContextTools(upgraded, self.scope, "owner")
            pending = tools.call("context.search_indexed", {"query": "legacyneedle"})
            self.assertEqual(pending["status"], "index_pending")
            self.assertEqual(pending["index"]["pending"], 1)
            with self.assertRaises(AccessDenied):
                upgraded.rebuild_index(self.scope, Lease("owner", 99))
            rebuilt = upgraded.rebuild_index(self.scope, lease)
            self.assertTrue(rebuilt["complete"])
            self.assertEqual(upgraded.task(self.scope), before)
            self.assertTrue(tools.call("context.search_indexed", {"query": "legacyneedle"})["hits"])
            snapshot = upgraded.load_snapshot(self.scope, sid, require_current=False)
            self.assertEqual(snapshot["objective"], "preserved goal")
            self.assertEqual(upgraded.get(self.scope, Ref("legacy", 1)).content, raw)

    def test_structural_preambles_and_module_assignments_remain_searchable(self):
        self.put("module.py", b'"""preambleneedle"""\nvalue="assignmentneedle"\n\ndef work():\n    return "functionneedle"\n')
        self.put("guide.md", b'leadingmarkdownneedle\n\n# Section\nsectionneedle\n')
        for needle in ("preambleneedle", "assignmentneedle", "functionneedle", "leadingmarkdownneedle", "sectionneedle"):
            with self.subTest(needle=needle):
                body = self.query(needle)
                self.assertTrue(body["hits"])
                self.assertIn(needle, self.store.get(self.scope, Ref(**body["hits"][0]["ref"])).content.decode())

    def test_binary_and_oversize_sources_expose_exclusion_and_pending_not_false_no_match(self):
        self.put("binary", b"\xffbinaryneedle")
        self.put("large", b"largependingneedle " + b"x" * (16 * 1024 * 1024))
        body = self.query("largependingneedle")
        self.assertEqual(body["status"], "index_pending")
        self.assertEqual(body["index"]["excluded"], 1)
        self.assertEqual(body["index"]["pending"], 1)
        self.assertFalse(body["index"]["complete"])
        self.assertEqual(self.store.get(self.scope, Ref("binary", 1)).content, b"\xffbinaryneedle")

    def test_rebuild_is_task_local_idempotent_and_query_json_is_bounded(self):
        for index in range(8):
            self.put("hit-" + str(index), ("budgetneedle 中文🙂 " * 100).encode())
        before = self.store.task(self.scope)
        other = Scope(self.scope.tenant, self.scope.project, self.scope.branch, "rebuild-other")
        lease = self.store.create_task(other, Target("other", "host", "unknown"), "other task", allowed_providers=("host",))
        self.store.put(other, lease, "same", b"otherrebuildneedle", expected_revision=0)
        other_tools = ContextTools(self.store, other, "other")
        first = self.query("budgetneedle")
        self.store.rebuild_index(self.scope, self.lease)
        self.store.rebuild_index(self.scope, self.lease)
        second = self.query("budgetneedle")
        self.assertEqual([hit["ref"] for hit in first["hits"]], [hit["ref"] for hit in second["hits"]])
        self.assertEqual(self.store.task(self.scope), before)
        self.assertTrue(other_tools.call("context.search_indexed", {"query": "otherrebuildneedle"})["hits"])
        bounded = self.query("budgetneedle", budget=1600)
        self.assertLessEqual(len(canonical(bounded).encode()), 1600)
        self.assertGreater(bounded["omitted"], 0)
        with self.assertRaises(BudgetExceeded):
            self.query("budgetneedle", budget=1)
        with self.assertRaises(InvalidRequest):
            self.query("a" * 1025)
        self.assertIsInstance(self.query('budgetneedle " OR * (')["hits"], list)

    def test_declared_unavailable_backend_preserves_core_bytes_and_pending_state(self):
        # The live SQLite build has FTS5. This capability fixture exercises the
        # adapter's declared unavailable branch; it is not a second native build.
        self.store.fts_available = False
        ref = self.put("without-fts", b"fallbackneedle still preserved")
        self.assertEqual(self.store.get(self.scope, ref).content, b"fallbackneedle still preserved")
        self.assertTrue(self.store.search(self.scope, "fallbackneedle"))
        coverage = self.tools.call("context.index_status", {})
        self.assertEqual(coverage["backend"], "unavailable")
        self.assertEqual(coverage["pending"], 1)
        with self.assertRaises(Unavailable):
            self.query("fallbackneedle")

    def test_real_mcp_search_and_read_preserve_source_revision_and_scope(self):
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            self.skipTest("Requires optional MCP SDK extra.")
        ref = self.put("wire-log", "wireindexedneedle 中文\r\n".encode())
        async def exercise():
            args = ["-m", "contextrail", "mcp", "--workspace", str(self.workspace), "--store", str(self.path),
                    "--project-id", self.scope.project, "--task", self.scope.task, "--session", "owner"]
            async with stdio_client(StdioServerParameters(command=sys.executable, args=args)) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("context.search_indexed", {"query": "wireindexedneedle"})
                    self.assertFalse(result.isError)
                    body = json.loads(result.content[0].text)
                    readback = await session.call_tool("context.get", body["hits"][0]["ref"])
                    self.assertEqual(json.loads(readback.content[0].text)["content"], "wireindexedneedle 中文\r\n")
                    denied = await session.call_tool("context.index_status", {"task": "other"})
                    self.assertTrue(denied.isError)
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
