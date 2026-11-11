"""Consumer behavior for lexical retrieval and scoped web observations."""

from dataclasses import asdict, replace
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from contextrail import Store, Target
from contextrail.host import ContextTools
from contextrail.project import load_project


class RetrievalWebEvidenceStage(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="rail-source-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / ".contextrail.toml").write_text('[project]\ntask="sources"\n', encoding="utf-8")
        config = load_project(self.root)
        config.store.parent.mkdir()
        self.store = Store(config.store)
        self.addCleanup(self.store.close)
        self.scope = config.scope
        self.lease = self.store.create_task(self.scope, Target("owner", "host", "external"),
                                           "find source evidence", allowed_providers=("host",))

    def cli(self, command, *args, ok=True):
        result = subprocess.run([sys.executable, "-m", "contextrail", command, "--workspace", str(self.root),
                                 "--session", "owner", *map(str, args)],
                                capture_output=True, timeout=25)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        return json.loads(result.stderr)["error"]["code"]

    def add(self, name, text, *, scope=None, lease=None):
        return self.store.put(scope or self.scope, lease or self.lease, name, text.encode(), expected_revision=0)

    def test_exact_han_and_path_hits_return_original_byte_anchors(self):
        source = "路径 src\\图形\\api_client.py\r\n圆锥曲线整理。曲线半径。"
        ref = self.add("notes", source)
        for query, expected in (("src/图形/api_client.py", "src\\图形\\api_client.py"), ("锥", "锥"), ("曲线", "曲线")):
            page = self.cli("search-indexed", "--query", query)
            self.assertEqual(page["hits"][0]["ref"]["name"], ref.name)
            anchor = page["hits"][0]["windows"][0]["matches"][0]["ref"]
            raw = self.store.get(self.scope, type(ref)(**anchor))
            self.assertEqual(raw.content.decode(), expected)
        self.add("unrelated", "圆形、锥体、半曲和线段。")
        page = self.cli("search-indexed", "--query", "圆锥曲线")
        self.assertEqual({hit["ref"]["name"] for hit in page["hits"]}, {ref.name})

    def test_identifier_prefix_and_typo_are_visible_candidates_only(self):
        ref = self.add("react", "Call useEffect after rendering.")
        for query in ("useEff", "useEfect"):
            page = self.cli("search-indexed", "--query", query)
            self.assertEqual(page["hits"], [])
            item = page["suggestions"][0]
            self.assertEqual(item["text"], "useEffect")
            self.assertEqual(self.store.get(self.scope, type(ref)(**item["ref"])).content, b"useEffect")
        exact = self.cli("search-indexed", "--query", "useEffect")
        self.assertEqual(exact["hits"][0]["ref"]["name"], ref.name)
        self.assertEqual(exact["suggestions"], [])

    def test_path_version_and_error_id_are_not_silently_changed(self):
        self.add("release", "Release v2.3.5 failed with ERR-4107 in src/setup.py (ENOENT).")
        for query in ("Release v2.3.6", "failed ERR-4108", "src/setpu.py", "ENOENR"):
            page = self.cli("search-indexed", "--query", query)
            self.assertEqual((page["hits"], page["suggestions"]), ([], []))

    def test_legacy_lexical_hits_stay_readable_until_explicit_rebuild(self):
        ref = self.add("legacy", "radius 晶体半径")
        prior_terms = ["radius", "晶体半径", "晶体", "体半", "半径"]
        encoded = " ".join("t" + word.encode().hex() for word in prior_terms)
        self.store.db.execute("UPDATE evidence_fts SET terms=? WHERE scope=? AND name=?", (encoded, self.scope.key, ref.name))
        self.store.db.execute("UPDATE evidence_index_state SET indexer='lexical-spans-v2' WHERE scope=?", (self.scope.key,))
        self.assertEqual(self.cli("search-indexed", "--query", "radius")["hits"][0]["ref"]["name"], ref.name)
        before = self.cli("search-indexed", "--query", "半")
        self.assertEqual((before["status"], before["index"]["reasons"]), ("index_pending", {"lexical_upgrade_pending": 1}))
        reader = ContextTools(self.store, self.scope, "owner")
        self.assertEqual(reader.call("context.query", {"queries": ["半"]})["queries"][0]["hits"], [])
        self.cli("index-rebuild", "--epoch", 0)
        self.assertEqual(self.cli("search-indexed", "--query", "半")["hits"][0]["ref"]["name"], ref.name)
        after = reader.call("context.query", {"queries": ["半"]})["queries"][0]
        self.assertEqual(after["hits"][0]["ref"]["name"], ref.name)
        self.assertFalse(after["sources"]["task"]["cache_hit"])

    def put_page(self, body, **options):
        path = self.root / "observed-response.json"
        path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return self.cli("web-cache-store", "--epoch", 0, "--input", path, **options)

    def test_web_cache_separates_sources_urls_tasks_and_explicit_refresh(self):
        ref = self.add("browser-result", "原始页面 v1\r\n")
        observation = {"source": "docs", "url": "https://EXAMPLE.invalid/page?b=2&a=1", "status": 200,
                       "fetched_at": time.time(), "ttl_seconds": 600, "http_version": "2",
                       "etag": '"v1"', "ref": asdict(ref), "expected_revision": 0, "fetch_id": "fetch-1"}
        first = self.put_page(observation)
        self.assertEqual(self.put_page(observation)["revision"], first["revision"])
        self.put_page({**observation, "source": "another-source", "etag": '"other"'})
        self.put_page({**observation, "url": "https://example.invalid/elsewhere", "etag": '"another-url"'})
        tools = ContextTools(self.store, self.scope, "owner")
        lookup = {"source": "docs", "url": "https://example.invalid/page?b=2&a=1"}
        hit = tools.call("context.web_cache", lookup)
        self.assertEqual((hit["state"], hit["etag"], hit["http_version"]), ("fresh", '"v1"', "2"))
        self.assertEqual(tools.call("context.get", hit["read_ref"])["content"], "原始页面 v1\r\n")
        self.assertEqual(tools.call("context.web_cache", {**lookup, "source": "another-source"})["etag"], '"other"')
        self.assertEqual(tools.call("context.web_cache", {**lookup, "url": "https://example.invalid/elsewhere"})["etag"],
                         '"another-url"')
        forced = tools.call("context.web_cache", {**lookup, "refresh": True})
        self.assertEqual(forced["state"], "refresh_required")
        self.assertNotIn("read_ref", forced)
        other = replace(self.scope, task="independent")
        self.store.create_task(other, Target("owner", "host", "external"), "other", allowed_providers=("host",))
        self.assertEqual(ContextTools(self.store, other, "owner").call("context.web_cache", lookup)["state"], "miss")

    def test_expired_web_source_requires_refresh_and_304_binds_existing_validator(self):
        ref = self.add("old-body", "网页原文")
        current = time.time()
        body = {"source": "browser", "url": "https://example.invalid/docs", "status": 200,
                "fetched_at": current - 600, "ttl_seconds": 30, "ref": asdict(ref), "etag": '"known"',
                "expected_revision": 0, "fetch_id": "old"}
        expired = self.put_page(body)
        self.assertEqual(expired["state"], "stale")
        self.assertNotIn("read_ref", expired)
        self.assertEqual(expired["historical_ref"], asdict(ref))
        refresh = {key: value for key, value in body.items() if key != "ref"}
        refresh.update(status=304, expected_revision=1, fetched_at=current, fetch_id="checked",
                       validator={"if_none_match": '"different"'})
        self.assertEqual(self.put_page(refresh, ok=False), "conflict")
        refresh["validator"] = {"if_none_match": '"known"'}
        renewed = self.put_page(refresh)
        self.assertEqual((renewed["state"], renewed["revision"], renewed["read_ref"]), ("fresh", 2, asdict(ref)))
        self.assertEqual(self.put_page({**refresh, "fetch_id": "late"}, ok=False), "conflict")
        replacement = self.add("updated-body", "网页原文 v2")
        newer = self.put_page({**body, "expected_revision": 2, "fetched_at": current + 0.01, "fetch_id": "new",
                               "ref": asdict(replacement), "etag": '"new"'})
        self.assertEqual((newer["revision"], newer["read_ref"]), (3, asdict(replacement)))
        self.store.invalidate(self.scope, self.lease, replacement)
        invalidated = self.cli("web-cache-get", "--source", "browser", "--url", body["url"])
        self.assertEqual(invalidated["state"], "unavailable")
        self.assertNotIn("read_ref", invalidated)

    def test_observed_http_response_roundtrip_through_the_normal_capture_entry(self):
        raw = "浏览器原文🙂\r\n".encode()
        etag = '"loopback-v1"'
        seen = []

        class Server(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                validator = self.headers.get("If-None-Match")
                seen.append(validator)
                unchanged = validator == etag
                self.send_response(304 if unchanged else 200)
                self.send_header("ETag", etag)
                if not unchanged:
                    self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                if not unchanged:
                    self.wfile.write(raw)

        with ThreadingHTTPServer(("127.0.0.1", 0), Server) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                url = "http://127.0.0.1:" + str(server.server_port) + "/docs"
                with urlopen(url, timeout=5) as response:
                    captured = response.read()
                    observed = {"source": "existing-fetcher", "url": url, "status": response.status,
                                "http_version": "1.1", "etag": response.headers["ETag"],
                                "fetched_at": time.time(), "ttl_seconds": 600, "expected_revision": 0, "fetch_id": "network-1"}
                path = self.root / "browser-result.json"
                path.write_text(json.dumps({
                    "call_id": "network-1", "tool": "browser", "argv": ["fetch", url], "cwd": str(self.root),
                    "started_at": 1, "finished_at": 2, "exit_code": 0,
                    "stdout_base64": base64.b64encode(captured).decode(), "stderr": "",
                }), encoding="utf-8")
                saved = self.cli("capture-result", "--epoch", 0, "--result", path)
                self.put_page({**observed, "ref": saved["stdout"]["ref"]})
                refresh = self.cli("web-cache-get", "--source", observed["source"], "--url", url, "--refresh")
                request = Request(url, headers={"If-None-Match": refresh["conditional"]["if_none_match"]})
                try:
                    with urlopen(request, timeout=5) as response:
                        self.fail(f"Expected HTTP 304, received {response.status}")
                except HTTPError as response:
                    with response:
                        self.assertEqual(response.code, 304)
                        renewed = self.put_page({**observed, "status": response.code, "fetched_at": time.time(),
                                                 "expected_revision": refresh["revision"], "fetch_id": "network-2",
                                                 "validator": refresh["conditional"]})
                self.assertEqual(seen, [None, etag])
                result = ContextTools(self.store, self.scope, "owner").call("context.get", renewed["read_ref"])
                self.assertEqual(result["content"].encode(), raw)
            finally:
                server.shutdown()
                worker.join()
