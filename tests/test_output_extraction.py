"""B03 current consumer contract: saved output -> extraction -> exact source."""

import asyncio
import base64
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from contextrail import ContextTools, Lease, Ref, Scope, Store, Target
from contextrail.errors import BudgetExceeded, InvalidRequest
from contextrail.tool_results import OutputRecorder
from contextrail.project import load_project


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rail-extract-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.path = self.workspace / "state.sqlite3"
        self.scope = load_project(self.workspace, overrides={"project_id": "extract-project", "task": "extract-task"}).scope
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.lease = self.store.create_task(self.scope, Target("owner", "host", "unknown"), "extract saved output", allowed_providers=("host",))
        self.tools = ContextTools(self.store, self.scope, "owner")

    def capture(self, raw, call_id="observed"):
        result = OutputRecorder(self.store, self.scope, self.lease, self.workspace).capture({
            "call_id": call_id, "tool": "shell", "argv": ["already-executed-by-host"], "cwd": str(self.workspace),
            "started_at": 0, "finished_at": 1, "exit_code": 0,
            "stdout_base64": base64.b64encode(raw).decode(), "stderr": ""})
        return result["stdout"]["ref"]

    def extract(self, source, kind, **options):
        return self.tools.call("context.extract", {"source": source, "kind": kind, **options})

    def read_sample(self, item):
        result = self.tools.call("context.get", item["source"]["ref"])
        return result["content"] if result["encoding"] == "utf-8" else base64.b64decode(result["content"])

    def test_cli_log_aggregation_reads_original_chinese_crlf_sample(self):
        line = "ERROR 中文🙂 retry failed\r\n"
        source = self.capture(("INFO ready\r\n" + line + line + "FATAL stopped\r\n").encode())
        result = subprocess.run([sys.executable, "-m", "contextrail", "extract-result", "--workspace", str(self.workspace),
            "--store", str(self.path), "--project-id", self.scope.project, "--tenant", self.scope.tenant,
            "--task", self.scope.task, "--session", "owner", "--source", json.dumps(source), "--kind", "log"],
            capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        body = json.loads(result.stdout)
        self.assertEqual(body["statistics"]["error_lines"], 3)
        self.assertEqual(body["statistics"]["groups"], 2)
        first = body["items"][0]
        self.assertEqual(first["count"], 2)
        self.assertEqual(self.read_sample(first), line)
        self.assertEqual(first["source"]["line_start"], 2)

    def test_json_pointer_filtered_values_have_exact_escaped_source_spans(self):
        raw = '{"rows":[{"keep":true,"a/b":{"~name":"中文\\n🙂"}},{"keep":1,"a/b":{"~name":"excluded"}}]}'.encode()
        source = self.capture(raw)
        body = self.extract(source, "json", options={"path": "/rows", "where": {"path": "/keep", "equals": True}, "select": "/a~1b/~0name"})
        self.assertEqual([item["value"] for item in body["items"]], ["中文\n🙂"])
        self.assertEqual(body["scan"]["records"], 2)
        self.assertEqual(self.read_sample(body["items"][0]), '"中文\\n🙂"')
        self.assertEqual(body["items"][0]["source"]["pointer"], "/rows/0/a~1b/~0name")
        missing = self.extract(source, "json", options={"path": "/rows", "where": {"path": "/keep", "equals": False}})
        self.assertEqual(missing["status"], "no_match")

    def test_csv_statistics_keep_multiline_record_locations_and_invalid_counts(self):
        raw = 'name,amount\r\n"甲\r\n乙",0.1\r\n丙,0.2\r\n丁,no\r\n戊,\r\n'.encode()
        body = self.extract(self.capture(raw), "csv", options={"numeric": ["amount"]})
        self.assertEqual(body["scan"]["records"], 4)
        amount = body["statistics"]["numeric"]["amount"]
        self.assertEqual((amount["sum"], amount["mean"], amount["valid"], amount["invalid"], amount["empty"]), ("0.3", "0.15", 2, 1, 1))
        self.assertEqual(self.read_sample(body["items"][0]), '"甲\r\n乙",0.1\r\n')
        self.assertEqual((body["items"][0]["source"]["line_start"], body["items"][0]["source"]["line_end"]), (2, 3))

    def test_git_z_formats_keep_rename_paths_tabs_newlines_and_binary_counts(self):
        status = b"R  new name\x00old\tname\x00 M line\nname\x00?? untracked\x00"
        body = self.extract(self.capture(status), "git_status")
        self.assertEqual((body["items"][0]["path"], body["items"][0]["original_path"]), ("new name", "old\tname"))
        self.assertEqual(self.read_sample(body["items"][0]), "R  new name\x00old\tname\x00")
        self.assertEqual(body["items"][1]["path"], "line\nname")
        numstat = b"2\t1\t\x00old\tname\x00new\nname\x00-\t-\timage.png\x00"
        numbers = self.extract(self.capture(numstat, "diff"), "git_numstat")
        self.assertEqual((numbers["statistics"]["added"], numbers["statistics"]["deleted"], numbers["statistics"]["binary_files"]), (2, 1, 1))
        self.assertEqual(numbers["items"][0]["original_path"], "old\tname")
        self.assertIsNone(numbers["items"][1]["added"])

    def test_csv_sum_keeps_small_decimal_alongside_maximum_supported_magnitude(self):
        raw = ("amount\n1" + "0" * 999 + "e1000\n1e-1000\n").encode()
        body = self.extract(self.capture(raw), "csv", options={"numeric": ["amount"]}, budget=16000)
        self.assertEqual(body["statistics"]["numeric"]["amount"]["sum"], "1" + "0" * 1999 + "." + "0" * 999 + "1")

    def test_parse_failures_preserve_exact_source_and_task_version(self):
        cases = [("json", b'{"duplicate":1,"duplicate":2}'), ("json", b'{"x":NaN}'),
                 ("csv", b'a,b\r\n"unterminated,b\r\n'), ("csv", b'a,a\n1,2\n'),
                 ("git_status", b"R  new\x00"), ("git_numstat", b"1\t2\tmissing-final-nul"), ("json", b"\xff")]
        for index, (kind, raw) in enumerate(cases):
            with self.subTest(kind=kind, index=index):
                source = self.capture(raw, "bad-" + str(index))
                before = self.store.task(self.scope)
                body = self.extract(source, kind)
                self.assertEqual(body["status"], "parse_failed")
                self.assertFalse(body["scan"]["complete"])
                self.assertEqual(self.store.get(self.scope, Ref(**body["source"])).content, raw)
                self.assertEqual(self.store.task(self.scope), before)

    def test_budget_omits_whole_items_but_preserves_full_scan_and_historical_ref(self):
        raw = ("ERROR " + "中文🙂" * 600 + "\r\n").encode() + b"ERROR short\r\n"
        source = self.capture(raw)
        self.store.put(self.scope, self.lease, source["name"], b"newer revision", expected_revision=1)
        before = self.store.task(self.scope)
        body = self.extract(source, "log", budget=1800, limit=1)
        self.assertLessEqual(len(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()), 1800)
        self.assertEqual(body["statistics"]["error_lines"], 2)
        self.assertEqual(body["omitted"], body["matched"] - len(body["items"]))
        self.assertEqual(body["source"]["revision"], 1)
        self.assertEqual(self.store.task(self.scope), before)
        with self.assertRaises(BudgetExceeded):
            self.extract(source, "log", budget=1)
        with self.assertRaises(InvalidRequest):
            self.extract(source, "json", options={"path": "/bad~2escape"})
        with self.assertRaises(InvalidRequest):
            self.tools.call("context.extract", {"source": source, "kind": "log", "task": "other"})

    def test_real_mcp_extraction_and_sample_read_are_bound_to_same_saved_output(self):
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            self.skipTest("Requires optional MCP SDK extra.")
        source = self.capture(b'{"events":[{"code":"ERR-492"},{"code":"OK"}]}')
        async def exercise():
            args = ["-m", "contextrail", "mcp", "--workspace", str(self.workspace), "--store", str(self.path),
                    "--tenant", self.scope.tenant, "--project-id", self.scope.project, "--task", self.scope.task, "--session", "owner"]
            async with stdio_client(StdioServerParameters(command=sys.executable, args=args)) as (read, write):
                async with ClientSession(read, write) as connection:
                    await connection.initialize()
                    result = await connection.call_tool("context.extract", {"source": source, "kind": "json", "options": {"path": "/events", "where": {"path": "/code", "equals": "ERR-492"}}})
                    self.assertFalse(result.isError)
                    body = json.loads(result.content[0].text)
                    self.assertEqual(body["matched"], 1)
                    sample = await connection.call_tool("context.get", body["items"][0]["source"]["ref"])
                    self.assertEqual(json.loads(sample.content[0].text)["content"], '{"code":"ERR-492"}')
                    denied = await connection.call_tool("context.extract", {"source": source, "kind": "log", "command": "execute history"})
                    self.assertTrue(denied.isError)
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
