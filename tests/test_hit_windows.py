"""C05 consumer behavior: exact source windows, positions and bounded read surfaces."""
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from contextrail import ContextTools, Lease, Ref, Store, Target
from contextrail.errors import InvalidRequest
from contextrail.models import canonical
from contextrail.project import load_project


class HitWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rail-windows-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".contextrail.toml").write_text('[project]\nid="windows"\ntask="hit"\n[context]\nresponse_bytes=8192\n', encoding="utf-8")
        self.config = load_project(self.root)
        self.config.store.parent.mkdir(parents=True)
        self.store = Store(self.config.store)
        self.addCleanup(self.store.close)
        self.owner = self.store.create_task(self.config.scope, Target("owner", "host", "unknown"), "read exact hit", allowed_providers=("host",))
        self.tools = ContextTools(self.store, self.config.scope, "owner")

    def put(self, name, content, media="text/plain"):
        return self.store.put(self.config.scope, self.owner, name, content.encode() if isinstance(content, str) else content, expected_revision=0, media_type=media)

    def search(self, query, **kwargs):
        return self.tools.call("context.search_indexed", {"query": query, **kwargs})

    def assert_exact(self, hit):
        for window in hit["windows"]:
            read = self.tools.call("context.get", window["ref"])
            self.assertEqual(read["content"], window["text"])
            self.assertLessEqual(len(window["text"].encode()), 2048)
            for match in window["matches"]:
                self.assertGreaterEqual(match["ref"]["start"], window["ref"]["start"])
                self.assertLessEqual(match["ref"]["end"], window["ref"]["end"])
                self.assertEqual(self.tools.call("context.get", match["ref"])["content"].casefold(), match["term"])
        self.assertEqual(self.tools.call("context.get", hit["snippet_ref"])["content"], hit["snippet"])

    def test_middle_chinese_hit_has_exact_utf8_window_and_global_lines(self):
        text = '# 原文标题\r\n' + 'prefix 中文🙂 ' * 70 + '\r\n' + '前缀 ' * 100 + '错误记录' + ' 后文' * 100 + '\r\n'
        self.put("source.md", text, "text/markdown")
        hit = self.search("错误记录")["hits"][0]
        self.assertIn("错误", hit["snippet"])
        self.assertEqual(hit["title"], "原文标题")
        self.assertEqual(hit["match_origin"], "content")
        self.assertTrue(all(match["line"] == 3 for win in hit["windows"] for match in win["matches"]))
        self.assert_exact(hit)

    def test_nearby_matches_merge_far_matches_remain_distinct(self):
        self.put("paragraph", "firstneedle gap secondneedle " + "filler " * 120 + "firstneedle end")
        hit = self.search("firstneedle secondneedle")["hits"][0]
        self.assertEqual(hit["windows_total"], 2)
        self.assertEqual(len(hit["windows"]), 2)
        self.assertEqual({m["term"] for m in hit["windows"][0]["matches"]}, {"firstneedle", "secondneedle"})
        self.assert_exact(hit)

    def test_same_heading_different_source_spans_are_not_deduplicated(self):
        self.put("repeat.md", "# 重复标题\r\nfirstneedle one\r\n# 重复标题\r\nfirstneedle two\r\n", "text/markdown")
        hits = self.search("firstneedle")["hits"]
        self.assertEqual(len(hits), 2)
        self.assertEqual({hit["title"] for hit in hits}, {"重复标题"})
        self.assertEqual(len({(h["ref"]["start"], h["ref"]["end"]) for h in hits}), 2)
        self.assertEqual({m["line"] for h in hits for w in h["windows"] for m in w["matches"]}, {2, 4})
        for hit in hits:
            self.assert_exact(hit)

    def test_cr_only_crlf_and_casefold_preserve_global_positions(self):
        self.put("mixed", "第一🙂\r\nbefore\rKEYnEeDLE\r\n")
        hit = self.search("keyneedle", context_chars=0)["hits"][0]
        window = hit["windows"][0]
        self.assertEqual(window["text"], "KEYnEeDLE")
        self.assertEqual(window["line_start"], 3)
        self.assertEqual(window["line_end"], 3)
        self.assert_exact(hit)

    def test_name_match_does_not_claim_a_body_occurrence(self):
        self.put("labeled.py", "ordinary labeled.py_extra source\r\n")
        hit = self.search("labeled.py")["hits"][0]
        self.assertEqual(hit["match_origin"], "source_label")
        self.assertEqual(hit["matches_total"], 0)
        self.assertEqual(hit["windows"][0]["matches"], [])
        self.assert_exact(hit)

    def test_window_and_anchor_limits_declare_omissions(self):
        self.put("many", "spamneedle " * 100)
        crowded = self.search("spamneedle")["hits"][0]
        self.assertEqual(crowded["matches_total"], 100)
        self.assertEqual(len(crowded["windows"][0]["matches"]), 32)
        self.assertEqual(crowded["matches_omitted"], 68)
        self.put("distant", ("distanceneedle " + "padding " * 80) * 6)
        limited = self.search("distanceneedle", window_limit=2, context_chars=8)["hits"][0]
        self.assertEqual(limited["windows_total"], 6)
        self.assertEqual(limited["windows_omitted"], 4)
        self.assertEqual(len(limited["windows"]), 2)
        self.assert_exact(limited)

    def test_narrow_json_budget_and_invalid_window_parameters(self):
        for n in range(4):
            self.put("budget-" + str(n), "中文🙂 " * 30 + "budgetneedle\r\n")
        limited = self.search("budgetneedle", budget=1800)
        self.assertLessEqual(len(canonical(limited).encode()), 1800)
        self.assertGreater(limited["omitted"], 0)
        full = self.search("budgetneedle", budget=16384)
        self.assertTrue(full["hits"])
        for hit in full["hits"]:
            self.assert_exact(hit)
        for values in ({"window_limit": 0}, {"window_limit": 9}, {"context_chars": -1}, {"context_chars": 257}, {"context_chars": True}):
            with self.assertRaises(InvalidRequest):
                self.search("budgetneedle", **values)

    def test_original_failed_stderr_uses_same_window_projection(self):
        from contextrail.tool_results import OutputRecorder
        code = "import sys;sys.stderr.reconfigure(newline='');sys.stderr.write('before\\r\\n'+'padding '*120+'failurewindow 中文\\r\\n');sys.exit(2)"
        process = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=20, env={**__import__('os').environ, 'PYTHONIOENCODING':'utf-8'})
        self.assertEqual(process.returncode, 2)
        data = {"call_id": "window-command", "tool": "shell", "argv": [sys.executable, "-c", code], "cwd": str(self.root),
                "started_at": 1000.0, "finished_at": 1001.0, "exit_code": process.returncode,
                "stdout": process.stdout.decode(), "stderr": process.stderr.decode()}
        saved = OutputRecorder(self.store, self.config.scope, self.owner, self.root).capture(data, budget=8192)
        result = self.tools.call("context.result_search", {"result_ref": saved["result_ref"], "query": "failurewindow", "context_chars": 12})
        self.assertEqual(result["execution_status"], "failed")
        hit = result["hits"][0]
        self.assertEqual(hit["stream"], "stderr")
        self.assertIn("failurewindow", hit["snippet"])
        self.assertEqual(hit["windows"][0]["matches"][0]["line"], 2)
        self.assert_exact(hit)

    def test_cli_and_real_mcp_windows_are_readable_exactly(self):
        self.put("wire.md", "# Wire\r\n" + "padding " * 120 + "wirewindow 中文🙂\r\n", "text/markdown")
        command = [sys.executable, "-m", "contextrail", "search-indexed", "--workspace", str(self.root), "--session", "owner",
                   "--query", "wirewindow", "--context-chars", "12", "--window-limit", "1"]
        result = subprocess.run(command, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        body = json.loads(result.stdout)
        self.assert_exact(body["hits"][0])
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        async def exercise():
            params = StdioServerParameters(command=sys.executable, args=["-m", "contextrail", "mcp", "--workspace", str(self.root), "--session", "owner"])
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    hit = await session.call_tool("context.search_indexed", {"query": "wirewindow", "context_chars": 12, "window_limit": 1})
                    self.assertFalse(hit.isError)
                    window = json.loads(hit.content[0].text)["hits"][0]["windows"][0]
                    read = await session.call_tool("context.get", window["ref"])
                    self.assertEqual(json.loads(read.content[0].text)["content"], window["text"])
                    denied = await session.call_tool("context.search_indexed", {"query": "wirewindow", "context_chars": 999})
                    self.assertTrue(denied.isError)
        asyncio.run(exercise())

    def test_unicode_window_byte_cap_and_nonfirst_chunk_global_line(self):
        self.put("emoji", "🙂" * 300 + " meganeedle " + "🙂" * 300)
        hit = self.search("meganeedle", context_chars=256)["hits"][0]
        self.assertLessEqual(len(hit["snippet"].encode()), 2048)
        self.assertIn("meganeedle", hit["snippet"])
        self.assert_exact(hit)
        self.put("split-log", "header\r\n" + "padding " * 1300 + "\r\nlastchunkneedle\r\n")
        later = self.search("lastchunkneedle")["hits"][0]
        self.assertGreater(later["ref"]["start"], 0)
        self.assertEqual(later["windows"][0]["matches"][0]["line"], 3)
        self.assert_exact(later)

    def test_long_unicode_casefold_token_is_not_cut_by_window_budget(self):
        original = "K" * 500
        self.put("long-word", "🙂" * 100 + " " + original + " " + "🙂" * 100)
        hit = self.search("k" * 500, context_chars=256)["hits"][0]
        self.assertIn(original, hit["windows"][0]["text"])
        self.assertEqual(hit["windows"][0]["matches"][0]["ref"]["end"] - hit["windows"][0]["matches"][0]["ref"]["start"], 1500)
        self.assert_exact(hit)


if __name__ == "__main__":
    unittest.main()
