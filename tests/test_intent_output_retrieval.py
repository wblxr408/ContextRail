"""Current B04 contract: execute on host, capture, intent/requery, exact source."""

import asyncio
import base64
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile
import time
import unittest

from contextrail import ContextTools, Ref, Store, Target
from contextrail.errors import BudgetExceeded, InvalidRequest, NotFound
from contextrail.models import canonical
from contextrail.project import load_project
from contextrail.tool_results import OutputRecorder


class IntentOutputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rail-intent-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.path = self.workspace / "store.sqlite3"
        self.scope = load_project(self.workspace, overrides={"project_id": "intent", "task": "intent-task"}).scope
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.lease = self.store.create_task(self.scope, Target("owner", "host", "unknown"), "intent within result", allowed_providers=("host",))
        self.tools = ContextTools(self.store, self.scope, "owner")
        self.recorder = OutputRecorder(self.store, self.scope, self.lease, self.workspace)
        script = ("import sys;sys.stdout.buffer.write(('INFO unrelated🙂\\r\\n'*3000+'\\r\\nERROR ERR-8372 中文诊断 switchneedle\\r\\n\\r\\n'+'INFO tail\\r\\n'*3000).encode('utf-8'));"
                  "sys.stderr.buffer.write(b'Traceback stderrneedle\\r\\n');sys.exit(7)")
        argv = [sys.executable, "-c", script, "metadataonlyneedle"]
        start = time.time()
        observed = subprocess.run(argv, capture_output=True, timeout=20)
        self.raw = observed.stdout
        self.data = {"call_id": "intent-call", "tool": "shell", "argv": argv, "cwd": str(self.workspace),
                     "started_at": start, "finished_at": time.time(), "exit_code": observed.returncode,
                     "stdout_base64": base64.b64encode(observed.stdout).decode(), "stderr_base64": base64.b64encode(observed.stderr).decode()}

    def search(self, result_ref, query, **options):
        return self.tools.call("context.result_search", {"result_ref": result_ref, "query": query, **options})

    def cli(self, command, *args, input=None):
        return subprocess.run([sys.executable, "-m", "contextrail", command, "--workspace", str(self.workspace),
            "--store", str(self.path), "--project-id", self.scope.project, "--task", self.scope.task,
            "--session", "owner", *map(str, args)], input=input, capture_output=True, timeout=30)

    def test_cli_intent_prioritizes_middle_diagnostic_and_preserves_command_failure(self):
        reply = self.cli("capture-result", "--epoch", "0", "--result", "-", "--intent", "ERR-8372", input=canonical(self.data).encode())
        self.assertEqual(reply.returncode, 0, reply.stderr.decode("utf-8", "replace"))
        result = json.loads(reply.stdout)
        self.assertLessEqual(len(canonical(result).encode()), 4096)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["intent"]["status"], "matched")
        hit = result["intent"]["hits"][0]
        self.assertIn("ERR-8372", hit["snippet"])
        self.assertIn("ERR-8372", hit["title"])
        original = self.tools.call("context.get", hit["ref"])["content"]
        self.assertIn("ERROR ERR-8372 中文诊断 switchneedle\r\n", original)

    def test_no_match_can_requery_same_result_with_cli_or_exact_read(self):
        first = self.recorder.capture(self.data, intent="absentintentneedle")
        self.assertEqual(first["intent"]["status"], "no_match")
        reply = self.cli("result-search", "--result-ref", canonical(first["result_ref"]), "--query", "switchneedle")
        self.assertEqual(reply.returncode, 0, reply.stderr.decode("utf-8", "replace"))
        changed = json.loads(reply.stdout)
        self.assertEqual(changed["status"], "matched")
        self.assertEqual(changed["result_ref"], first["result_ref"])
        self.assertTrue(changed["searchable_terms"])
        self.assertIn(b"ERR-8372", self.store.get(self.scope, Ref(**first["stdout"]["ref"])).content)

    def test_search_does_not_use_manifest_argv_or_another_calls_output(self):
        first = self.recorder.capture(self.data, intent="metadataonlyneedle")
        self.assertEqual(first["intent"]["status"], "no_match")
        other = {**self.data, "call_id": "other-call", "stdout_base64": base64.b64encode(b"crosscallneedle").decode()}
        self.recorder.capture(other)
        self.assertEqual(self.search(first["result_ref"], "crosscallneedle")["status"], "no_match")
        stderr = self.search(first["result_ref"], "stderrneedle")
        self.assertEqual(stderr["hits"][0]["stream"], "stderr")

    def test_changed_intent_retry_preserves_one_observation_and_manifest(self):
        first = self.recorder.capture(self.data, intent="switchneedle")
        before = self.store.task(self.scope)
        manifest = self.store.get(self.scope, Ref(**first["result_ref"])).content
        second = self.recorder.capture(self.data, intent="stderrneedle")
        self.assertEqual(second["intent"]["hits"][0]["stream"], "stderr")
        self.assertEqual(self.store.task(self.scope), before)
        self.assertEqual(self.store.get(self.scope, Ref(**first["result_ref"])).content, manifest)

    def test_required_intent_budget_rejects_before_saving_and_candidates_are_wholly_omitted(self):
        before = self.store.task(self.scope)
        with self.assertRaises(BudgetExceeded):
            self.recorder.capture(self.data, intent="查询" * 250, budget=1800)
        self.assertEqual(self.store.task(self.scope), before)
        with self.assertRaises(NotFound):
            self.store.get(self.scope, Ref("tool-result/intent-call/result", 1))
        result = self.recorder.capture(self.data, intent="INFO", budget=2200)
        self.assertLessEqual(len(canonical(result).encode()), 2200)
        self.assertGreater(result["intent"]["omitted"], 0)
        self.assertEqual(result["status"], "failed")

    def test_old_stream_ref_is_pending_and_never_replaced_by_new_head(self):
        first = self.recorder.capture(self.data, intent="switchneedle")
        ref = Ref(**first["stdout"]["ref"])
        self.store.put(self.scope, self.lease, ref.name, b"newheadneedle", expected_revision=1)
        result = self.search(first["result_ref"], "newheadneedle")
        self.assertEqual(result["status"], "index_pending")
        self.assertEqual(result["hits"], [])
        self.assertEqual(result["sources"][0]["ref"]["revision"], 1)
        self.assertIn(b"switchneedle", self.store.get(self.scope, ref).content)

    def test_optional_query_failure_keeps_saved_refs_and_malformed_manifest_is_rejected(self):
        result = self.recorder.capture(self.data, intent="?!")
        self.assertEqual(result["intent"]["status"], "unsupported_query")
        self.assertEqual(self.store.get(self.scope, Ref(**result["stdout"]["ref"])).content, self.raw)
        malformed = self.store.put(self.scope, self.lease, "tool-result/fake/result", b'{"schema":"wrong"}', expected_revision=0)
        with self.assertRaises(InvalidRequest):
            self.search(asdict(malformed), "data")

    def test_real_mcp_requeries_and_returns_exact_source_without_write_or_scope_override(self):
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            self.skipTest("Requires optional MCP SDK extra.")
        captured = self.recorder.capture(self.data, intent="absentintentneedle")
        async def exercise():
            args = ["-m", "contextrail", "mcp", "--workspace", str(self.workspace), "--store", str(self.path),
                    "--project-id", self.scope.project, "--task", self.scope.task, "--session", "owner"]
            async with stdio_client(StdioServerParameters(command=sys.executable, args=args)) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("context.result_search", {"result_ref": captured["result_ref"], "query": "switchneedle"})
                    self.assertFalse(result.isError)
                    body = json.loads(result.content[0].text)
                    readback = await session.call_tool("context.get", body["hits"][0]["ref"])
                    self.assertIn("ERR-8372 中文诊断 switchneedle\r\n", json.loads(readback.content[0].text)["content"])
                    denied = await session.call_tool("context.result_search", {"result_ref": captured["result_ref"], "query": "switchneedle", "task": "another"})
                    self.assertTrue(denied.isError)
        asyncio.run(exercise())

    def test_optional_sql_query_failure_does_not_hide_already_saved_observation(self):
        def authorize(action, first, second, database, trigger):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_FUNCTION and second == "bm25" else sqlite3.SQLITE_OK
        self.store.db.set_authorizer(authorize)
        result = self.recorder.capture(self.data, intent="switchneedle")
        self.assertEqual(result["intent"]["status"], "unavailable")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.store.get(self.scope, Ref(**result["stdout"]["ref"])).content, self.raw)

    def test_manifest_with_matching_identity_but_missing_status_is_rejected(self):
        captured = self.recorder.capture(self.data)
        ref = Ref(**captured["result_ref"])
        value = json.loads(self.store.get(self.scope, ref).content)
        del value["status"]
        malformed = self.store.put(self.scope, self.lease, ref.name, canonical(value).encode(), expected_revision=1)
        with self.assertRaises(InvalidRequest):
            self.search(asdict(malformed), "switchneedle")

    def test_expired_stream_does_not_leak_tokens_through_vocabulary_hints(self):
        captured = self.recorder.capture(self.data)
        moment = [100.0]
        self.store.clock = lambda: moment[0]
        original = Ref(**captured["stdout"]["ref"])
        content = b"expirehintneedle"
        revised = self.store.put(self.scope, self.lease, original.name, content, expected_revision=1, expires_at=110)
        manifest_ref = Ref(**captured["result_ref"])
        manifest = json.loads(self.store.get(self.scope, manifest_ref).content)
        manifest["stdout"] = {"ref": asdict(revised), "bytes": len(content)}
        updated = self.store.put(self.scope, self.lease, manifest_ref.name, canonical(manifest).encode(), expected_revision=1)
        self.assertTrue(self.search(asdict(updated), "expirehintneedle")["hits"])
        moment[0] = 111.0
        result = self.search(asdict(updated), "expirehintneedle")
        self.assertEqual(result["hits"], [])
        self.assertNotIn("expirehintneedle", result["searchable_terms"])
        self.assertEqual(result["index"]["unavailable"], 1)


if __name__ == "__main__":
    unittest.main()
