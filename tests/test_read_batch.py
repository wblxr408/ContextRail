"""Current B06 contract: isolated read views, ordered failures, bounded delivery."""

import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

from contextrail import ContextTools, Ref, Store, Target
from contextrail.errors import BudgetExceeded, InvalidRequest
from contextrail.models import canonical
from contextrail.project import load_project


class BatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rail-batch-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.path = self.workspace / "store.sqlite3"
        self.scope = load_project(self.workspace, overrides={"project_id": "batch", "task": "batch-task"}).scope
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.lease = self.store.create_task(self.scope, Target("owner", "host", "unknown"), "read independently", allowed_providers=("host",))
        self.first = self.store.put(self.scope, self.lease, "first", "原始🙂\r\n".encode(), expected_revision=0, expires_at=9999999999)
        self.log = self.store.put(self.scope, self.lease, "log", b"ERROR x\r\nERROR x\r\n", expected_revision=0)
        self.tools = ContextTools(self.store, self.scope, "owner")

    def calls(self):
        return [{"id": "read-1", "tool": "context.get", "arguments": asdict(self.first)},
                {"id": "missing-2", "tool": "context.get", "arguments": {"name": "absent", "revision": 1}},
                {"id": "extract-3", "tool": "context.extract", "arguments": {"source": asdict(self.log), "kind": "log"}}]

    def batch(self, calls=None, **options):
        return self.tools.call("context.batch", {"calls": self.calls() if calls is None else calls, **options})

    def test_cli_returns_ordered_success_error_and_extraction_without_changing_task(self):
        before = self.store.task(self.scope)
        args = [sys.executable, "-m", "contextrail", "batch-read", "--workspace", str(self.workspace), "--store", str(self.path),
                "--project-id", self.scope.project, "--task", self.scope.task, "--session", "owner", "--calls", "-", "--concurrency", "2"]
        result = subprocess.run(args, input=canonical(self.calls()).encode(), capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        body = json.loads(result.stdout)
        self.assertEqual([item["id"] for item in body["results"]], ["read-1", "missing-2", "extract-3"])
        self.assertEqual([item["status"] for item in body["results"]], ["succeeded", "failed", "succeeded"])
        self.assertEqual(body["results"][0]["result"]["content"], "原始🙂\r\n")
        self.assertEqual(body["results"][1]["error"]["code"], "not_found")
        self.assertEqual(body["results"][2]["result"]["statistics"]["error_lines"], 2)
        self.assertEqual(self.store.task(self.scope), before)

    def test_concurrent_reads_overlap_on_independent_readonly_connections(self):
        barrier, lock, active, peak = threading.Barrier(2), threading.Lock(), 0, 0
        def clock():
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                barrier.wait(timeout=4)
            finally:
                with lock:
                    active -= 1
            return 1
        self.store.clock = clock
        calls = [{"id": str(index), "tool": "context.get", "arguments": asdict(self.first)} for index in range(2)]
        result = self.batch(calls, concurrency=2)
        self.assertEqual(peak, 2)
        self.assertEqual([item["result"]["content"] for item in result["results"]], ["原始🙂\r\n"] * 2)
        self.store.clock = lambda: 1
        with Store.open_readonly(self.path) as reader:
            with self.assertRaises(sqlite3.OperationalError):
                reader.put(self.scope, self.lease, "forbidden", b"write", expected_revision=0)

    def test_overall_budget_omits_whole_results_and_keeps_retry_requests_and_failures(self):
        large = self.store.put(self.scope, self.lease, "large", ("中文🙂" * 500).encode(), expected_revision=0)
        calls = [{"id": "large", "tool": "context.get", "arguments": asdict(large)}, self.calls()[1]]
        body = self.batch(calls, budget=1800, per_call_bytes=10000)
        self.assertLessEqual(len(canonical(body).encode()), 1800)
        self.assertEqual(body["results"][0]["status"], "succeeded")
        self.assertEqual(body["results"][0]["delivery"], "omitted")
        self.assertEqual(body["results"][0]["request"], calls[0]["arguments"])
        self.assertEqual(body["results"][1]["error"]["code"], "not_found")
        # Too-small budgets must fail before any read, not lose settled states later.
        self.store.clock = lambda: (_ for _ in ()).throw(AssertionError("must not dispatch"))
        with self.assertRaises(BudgetExceeded):
            self.batch([self.calls()[0]], budget=1)

    def test_invalid_batch_actions_duplicates_and_transactions_are_rejected(self):
        for calls in ([self.calls()[0], self.calls()[0]],
                      [{"id": "write", "tool": "action.reserve", "arguments": {}}],
                      [{**self.calls()[0], "depends_on": ["other"]}]):
            with self.subTest(calls=calls), self.assertRaises(InvalidRequest):
                self.batch(calls)
        with self.assertRaises(InvalidRequest):
            self.batch(concurrency=9)
        with self.store.transaction(), self.assertRaises(InvalidRequest):
            self.batch()

    def test_memory_store_uses_explicit_sequential_mode_without_copying(self):
        with Store(":memory:") as memory:
            lease = memory.create_task(self.scope, Target("owner", "host", "unknown"), "in memory", allowed_providers=("host",))
            ref = memory.put(self.scope, lease, "in-memory", b"live memory bytes", expected_revision=0)
            tools = ContextTools(memory, self.scope, "owner")
            calls = [{"id": "memory", "tool": "context.get", "arguments": asdict(ref)}]
            with self.assertRaises(InvalidRequest):
                tools.call("context.batch", {"calls": calls, "concurrency": 2})
            result = tools.call("context.batch", {"calls": calls, "concurrency": 1})
            self.assertEqual(result["results"][0]["result"]["content"], "live memory bytes")

    def test_excessively_nested_request_is_rejected_before_read_dispatch(self):
        nested = []
        for _ in range(1200):
            nested = [nested]
        calls = [{"id": "deep", "tool": "context.extract", "arguments": {"source": asdict(self.log), "kind": "log", "options": {"invalid": nested}}}]
        with self.assertRaises(InvalidRequest):
            self.batch(calls)

    def test_parse_failure_is_a_failed_item_with_original_source_while_sibling_succeeds(self):
        malformed = self.store.put(self.scope, self.lease, "malformed-json", b'{"x":', expected_revision=0)
        calls = [{"id": "parse", "tool": "context.extract", "arguments": {"source": asdict(malformed), "kind": "json"}}, self.calls()[0]]
        body = self.batch(calls)
        self.assertEqual([entry["status"] for entry in body["results"]], ["failed", "succeeded"])
        self.assertEqual(body["results"][0]["error"]["code"], "invalid_format")
        self.assertEqual(body["results"][0]["result"]["source"], asdict(malformed))

    def test_real_mcp_batch_failure_does_not_hide_source_for_subsequent_read(self):
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            self.skipTest("Requires optional MCP SDK extra.")
        async def exercise():
            args = ["-m", "contextrail", "mcp", "--workspace", str(self.workspace), "--store", str(self.path),
                    "--project-id", self.scope.project, "--task", self.scope.task, "--session", "owner"]
            async with stdio_client(StdioServerParameters(command=sys.executable, args=args)) as (read, write):
                async with ClientSession(read, write) as connection:
                    await connection.initialize()
                    reply = await connection.call_tool("context.batch", {"calls": self.calls(), "concurrency": 2})
                    self.assertFalse(reply.isError)
                    result = json.loads(reply.content[0].text)
                    self.assertEqual(result["results"][1]["error"]["code"], "not_found")
                    source = result["results"][2]["result"]["items"][0]["source"]["ref"]
                    original = await connection.call_tool("context.get", source)
                    self.assertEqual(json.loads(original.content[0].text)["content"], "ERROR x\r\n")
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
