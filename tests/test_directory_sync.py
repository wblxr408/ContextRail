"""C01/C02 consumer contracts: bounded directory observations and explicit queueing."""

import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from contextrail import ContextTools, Lease, Ref, Scope, Store, Target
from contextrail.errors import AccessDenied, Conflict, InvalidRequest, NotFound, Unavailable
from contextrail.models import canonical
from contextrail.project import load_project


class DirectorySyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rail-directory-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "中文 project"
        self.root.mkdir()
        self.config_file = self.root / ".contextrail.toml"
        self.config_file.write_text('[project]\nid="directory-demo"\ntask="sync"\n[context]\nresponse_bytes=8192\n', encoding="utf-8")
        self.src = self.root / "src"
        self.src.mkdir()
        self.cli("project-init", "--session", "owner", "--objective", "read selected source")

    def cli(self, command, *args, ok=True):
        process = subprocess.run([sys.executable, "-m", "contextrail", command, "--workspace", str(self.root),
                                  "--session", "owner", *map(str, args)], capture_output=True, timeout=40)
        self.assertEqual(process.returncode, 0 if ok else 1, process.stderr.decode("utf-8", "replace"))
        return json.loads(process.stdout if ok else process.stderr)

    def write(self, path, content):
        file = self.src / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content if isinstance(content, bytes) else content.encode())
        return file

    def sync(self, **kwargs):
        from contextrail.directory_sync import DirectorySync
        config = load_project(self.root)
        with Store(config.store) as store:
            return DirectorySync(store, config, Lease("owner", 0)).sync(**kwargs)

    def entries(self, run):
        return self.cli("directory-status", "--run", run, "--limit", 100)["entries"]

    def test_cli_gitignore_selection_and_exact_utf8_bytes(self):
        raw = "源码needle 中文🙂\r\nnext\r\n".encode()
        self.write("main.py", raw)
        self.write("drop.tmp", "excluded")
        self.write("nested/.gitignore", "*.py\n!keep.py\n")
        self.write("nested/drop.py", "ignoredneedle")
        self.write("nested/keep.py", "nestedneedle")
        self.write("node_modules/dependency.py", "dependencyneedle")
        self.write("private/secret.py", "privateneedle")
        self.config_file.write_text(self.config_file.read_text() + '[directory]\ninclude=["**/*.py"]\nexclude=["private/**"]\n', encoding="utf-8")
        body = self.cli("directory-sync", "--root", "src", "--epoch", 0)
        self.assertEqual(body["status"], "complete")
        captured = {entry["path"]: entry for entry in self.entries(body["run"]) if entry["outcome"] == "captured"}
        self.assertEqual(set(captured), {"src/main.py", "src/nested/keep.py"})
        config = load_project(self.root)
        with Store(config.store) as store:
            self.assertEqual(store.get(config.scope, Ref(**captured["src/main.py"]["ref"])).content, raw)
            tools = ContextTools(store, config.scope, "owner")
            hit = tools.call("context.search_indexed", {"query": "main.py"})["hits"][0]
            self.assertEqual(hit["source_path"], "src/main.py")

    def test_cli_resume_exact_cursor_after_process_exit_and_reader_pagination(self):
        for name in ("a.py", "b.py", "c.py"):
            self.write(name, "pageneedle " + name)
        first = self.cli("directory-sync", "--root", "src", "--epoch", 0, "--max-files", 1)
        self.assertEqual(first["status"], "partial")
        coverage = self.cli("search-indexed", "--query", "notobservedyet")["index"]
        self.assertFalse(coverage["complete"])
        self.assertEqual(coverage["directory_pending"], 1)
        rejected = self.cli("directory-sync", "--resume", first["run"], "--step", 0, "--epoch", 0, ok=False)
        self.assertEqual(rejected["error"]["code"], "conflict")
        current = first
        while current["status"] == "partial":
            current = self.cli("directory-sync", "--resume", current["run"], "--step", current["step"], "--epoch", 0, "--max-files", 1)
        self.assertEqual(current["counts"]["captured"], 3)
        config = load_project(self.root)
        with Store.open_readonly(config.store) as store:
            tools = ContextTools(store, config.scope, "owner")
            one = tools.call("context.directory_status", {"run": first["run"], "limit": 1, "budget": 1800})
            two = tools.call("context.directory_status", {"run": first["run"], "offset": one["next_offset"], "limit": 1})
            self.assertNotEqual(one["entries"][0]["path"], two["entries"][0]["path"])
            ref = one["entries"][0]["ref"]
            self.assertIn("pageneedle", tools.call("context.get", ref)["content"])
            self.assertLessEqual(len(canonical(one).encode()), 1800)
            with self.assertRaises(InvalidRequest):
                tools.call("context.directory_status", {"run": first["run"], "root": str(self.root)})

    def test_edit_preserved_mtime_identical_touch_delete_and_reappear(self):
        file = self.write("note.md", "firstneedle\r\n")
        first = self.sync(root="src")
        old = next(e["ref"] for e in self.entries(first["run"]) if e["outcome"] == "captured")
        before_stat = file.stat()
        file.write_bytes(b"secondneedle\r\n")
        os.utime(file, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))
        second = self.sync(root="src")
        new = next(e["ref"] for e in self.entries(second["run"]) if e["outcome"] == "captured")
        self.assertEqual(new["revision"], 2)
        os.utime(file, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns + 1000000000))
        third = self.sync(root="src")
        self.assertEqual(third["counts"]["unchanged"], 1)
        self.assertEqual(self.entries(first["run"])[0]["availability"], "historical")
        config = load_project(self.root)
        with Store(config.store) as store:
            self.assertEqual(store.get(config.scope, Ref(**old)).content, b"firstneedle\r\n")
        file.unlink()
        deleted = self.sync(root="src")
        self.assertEqual(deleted["counts"]["deleted"], 1)
        self.assertEqual(self.entries(second["run"])[0]["availability"], "unavailable")
        with Store(config.store) as store:
            with self.assertRaises(Unavailable):
                store.get(config.scope, Ref(**new))
            self.assertEqual(store.search_indexed(config.scope, "secondneedle")["hits"], [])
        file.write_bytes(b"thirdneedle")
        reappeared = self.sync(root="src")
        self.assertEqual(next(e["ref"]["revision"] for e in self.entries(reappeared["run"]) if e["outcome"] == "captured"), 3)

    def test_partial_scan_does_not_delete_unseen_and_budgets_can_increase(self):
        self.write("a.txt", "a")
        b = self.write("deep/b.txt", "b" * 80)
        self.sync(root="src")
        b.unlink()
        self.write("deep/c.txt", "c" * 80)
        partial = self.sync(root="src", max_depth=0, max_file_bytes=8)
        self.assertEqual(partial["status"], "partial")
        config = load_project(self.root)
        with Store(config.store) as store:
            self.assertTrue(store.search_indexed(config.scope, "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")["hits"])
        complete = self.sync(resume=partial["run"], step=partial["step"], max_depth=3, max_file_bytes=100)
        self.assertEqual(complete["status"], "complete")
        self.assertEqual(complete["counts"]["deleted"], 1)

    def test_superseded_wrong_scope_owner_and_policy_rejected(self):
        self.write("a.py", "a")
        self.write("b.py", "b")
        first = self.sync(root="src", max_files=1)
        self.sync(root="src")
        with self.assertRaises(Conflict):
            self.sync(resume=first["run"], step=first["step"])
        partial = self.sync(root="src", max_files=1)
        self.config_file.write_text(self.config_file.read_text() + '[directory]\nexclude=["b.py"]\n', encoding="utf-8")
        with self.assertRaises(Conflict):
            self.sync(resume=partial["run"], step=partial["step"])
        from contextrail.directory_sync import DirectorySync, directory_status
        config = load_project(self.root)
        with Store(config.store) as store:
            with self.assertRaises(AccessDenied):
                DirectorySync(store, config, Lease("owner", 999)).sync(root="src")
            other = Scope(config.scope.tenant, config.scope.project, config.scope.branch, "other")
            store.create_task(other, Target("other", "host", "unknown"), "other", allowed_providers=("host",))
            with self.assertRaises(NotFound):
                directory_status(store, other, "other", partial["run"])

    def test_failed_page_rolls_back_evidence_and_cursor_then_retries(self):
        self.write("a.py", "atomicneedle a")
        self.write("b.py", "atomicneedle b")
        config = load_project(self.root)
        from contextrail.directory_sync import DirectorySync, directory_status
        with Store(config.store) as store:
            # Trigger failure at the second real published artifact/index, after the first insertion.
            store.db.execute("CREATE TRIGGER reject_second BEFORE INSERT ON evidence_index_state WHEN (SELECT COUNT(*) FROM evidence_index_state WHERE scope=new.scope)>=1 BEGIN SELECT RAISE(ABORT,'reject second'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                DirectorySync(store, config, Lease("owner", 0)).sync(root="src")
            run = store.db.execute("SELECT id FROM directory_runs WHERE scope=?", (config.scope.key,)).fetchone()[0]
            state = directory_status(store, config.scope, "owner", run)
            self.assertEqual(state["step"], 0)
            self.assertEqual(state["entries"], [])
            self.assertEqual(store.search_indexed(config.scope, "atomicneedle")["hits"], [])
            store.db.execute("DROP TRIGGER reject_second")
            retried = DirectorySync(store, config, Lease("owner", 0)).sync(resume=run, step=0)
            self.assertEqual(retried["counts"]["captured"], 2)

    def test_outside_root_and_native_junction_are_not_read(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("outsideneedle", encoding="utf-8")
        self.write("good.txt", "insideneedle")
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(self.src / "external"), str(outside)], capture_output=True, check=True)
        else:
            (self.src / "external").symlink_to(outside, target_is_directory=True)
        try:
            result = self.sync(root="src")
            entries = self.entries(result["run"])
            self.assertTrue(any(e["reason"] == "link_or_reparse" for e in entries))
            self.assertFalse(any("secret" in e["path"] for e in entries))
            with self.assertRaises(InvalidRequest):
                self.sync(root=str(outside))
        finally:
            # Remove only the link itself; never recurse across the junction.
            if os.name == "nt":
                (self.src / "external").rmdir()
            else:
                (self.src / "external").unlink()

    def test_explicit_existing_memory_queue_uses_frozen_revision_and_retry_effect(self):
        self.write("note.md", "originalqueue 中\r\n\r\nsecond")
        self.write("module.py", "not a native document")
        run = self.sync(root="src")
        self.write("note.md", "changed after capture")
        memory_home = Path(self.temp.name) / "existing-memory"
        app = Path.home() / ".codex/services/engineering-memory/app"
        setup = subprocess.run([sys.executable, "-c", "import sys;sys.path.insert(0,sys.argv[1]);from listener import Store;store=Store(sys.argv[2]);store.db.close()", str(app), str(memory_home)], capture_output=True, timeout=30)
        self.assertEqual(setup.returncode, 0, setup.stderr.decode("utf-8", "replace"))
        first = self.cli("directory-memory-enqueue", "--run", run["run"], "--epoch", 0, "--memory-home", memory_home)
        second = self.cli("directory-memory-enqueue", "--run", run["run"], "--epoch", 0, "--memory-home", memory_home)
        self.assertEqual(first["queued"], 1, first)
        self.assertEqual(second["queued"], 0)
        self.assertEqual(first["status"], "queued_not_yet_classified")
        self.assertTrue(any(e["status"] == "unsupported" for e in first["entries"]))
        db = sqlite3.connect(memory_home / "state.sqlite")
        try:
            rows = db.execute("SELECT payload,state FROM jobs").fetchall()
        finally:
            db.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0][0])["final"], "originalqueue 中\n\nsecond")
        self.assertEqual(rows[0][1], "queued")

    def test_directory_fanout_limit_is_visible_and_resumable(self):
        for name in ("one.txt", "two.txt", "three.txt"):
            self.write(name, "fanoutneedle")
        limited = self.sync(root="src", max_directory_entries=2)
        self.assertEqual(limited["status"], "partial")
        self.assertTrue(any("max_directory_entries" in e["reason"] for e in self.entries(limited["run"])))
        done = self.sync(resume=limited["run"], step=limited["step"], max_directory_entries=3)
        self.assertEqual(done["status"], "complete")
        self.assertEqual(done["counts"]["captured"], 3)

    def test_complete_delete_reconciliation_pages_without_touching_other_source(self):
        other = self.root / "other"
        other.mkdir()
        (other / "keep.txt").write_bytes(b"isolatedneedle")
        self.sync(root="other")
        for n in range(3):
            self.write(str(n) + ".txt", "deletepageneedle")
        first = self.sync(root="src")
        for file in self.src.iterdir():
            file.unlink()
        page = self.sync(root="src", max_files=1)
        self.assertEqual(page["status"], "partial")
        self.assertEqual(page["counts"]["deleted"], 1)
        while page["status"] == "partial":
            page = self.sync(resume=page["run"], step=page["step"], max_files=1)
        self.assertEqual(page["counts"]["deleted"], 3)
        config = load_project(self.root)
        with Store(config.store) as store:
            self.assertTrue(store.search_indexed(config.scope, "isolatedneedle")["hits"])

    def test_frozen_task_and_wrong_owner_cannot_advance_directory(self):
        self.write("a.txt", "frozenneedle")
        config = load_project(self.root)
        from contextrail.directory_sync import DirectorySync
        with Store(config.store) as store:
            owner = Lease("owner", 0)
            sid = store.snapshot(config.scope, owner, ())
            store.prepare(config.scope, owner, sid, Target("new-owner", "host", "unknown"))
            with self.assertRaises(Conflict):
                DirectorySync(store, config, owner).sync(root="src")

    def test_schema7_upgrade_preserves_task_evidence_and_snapshot_for_directory_use(self):
        from contextrail.store import SCHEMA_V1, SCHEMA_V3, SCHEMA_V4, SCHEMA_V5, SCHEMA_V6, SCHEMA_V7, APPLICATION_ID
        from contextrail.models import Selection, digest
        path = self.root / "legacy.sqlite3"
        db = sqlite3.connect(path, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.executescript(SCHEMA_V1 + SCHEMA_V3 + SCHEMA_V4 + SCHEMA_V5 + SCHEMA_V6 + SCHEMA_V7)
        db.execute("PRAGMA application_id=" + str(APPLICATION_ID))
        db.execute("PRAGMA user_version=7")
        config = load_project(self.root)
        legacy = Store.__new__(Store)
        legacy.db, legacy.clock = db, time.time
        owner = legacy.create_task(config.scope, Target("owner", "host", "unknown"), "preserve legacy", allowed_providers=("host",))
        raw = b"legacy exact\r\n"
        db.execute("INSERT INTO blobs VALUES(?,?,?)", (config.scope.key, digest(raw), raw))
        db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,0)", (config.scope.key, "legacy", 1, digest(raw), len(raw), "text/plain", None))
        db.execute("INSERT INTO heads VALUES(?,?,?)", (config.scope.key, "legacy", 1))
        sid = legacy.snapshot(config.scope, owner, (Selection(Ref("legacy", 1)),))
        before = legacy.task(config.scope)
        legacy.close()
        self.write("migrated.py", "migrationdirectoryneedle")
        from contextrail.directory_sync import DirectorySync
        with Store(path) as store:
            self.assertEqual(store.task(config.scope), before)
            self.assertEqual(store.get(config.scope, Ref("legacy", 1)).content, raw)
            self.assertEqual(store.load_snapshot(config.scope, sid)["objective"], "preserve legacy")
            result = DirectorySync(store, config, owner).sync(root="src")
            self.assertEqual(result["counts"]["captured"], 1)
            self.assertTrue(store.search_indexed(config.scope, "migrationdirectoryneedle")["hits"])

    def test_source_head_changed_while_reading_rejects_staged_page(self):
        self.write("race.txt", "firstsource")
        first = self.sync(root="src")
        saved = next(e["ref"] for e in self.entries(first["run"]) if e["outcome"] == "captured")
        self.write("race.txt", "diskupdated")
        config = load_project(self.root)
        from contextrail.directory_sync import DirectorySync, _read_file
        with Store(config.store) as store:
            def changed_head(*args):
                result = _read_file(*args)
                store.put(config.scope, Lease("owner", 0), saved["name"], b"concurrentowner", expected_revision=1)
                return result
            with patch("contextrail.directory_sync._read_file", side_effect=changed_head):
                with self.assertRaises(Conflict):
                    DirectorySync(store, config, Lease("owner", 0)).sync(root="src")
            self.assertEqual(store.get(config.scope, Ref(saved["name"], 2)).content, b"concurrentowner")

    def test_real_mcp_directory_status_and_exact_read(self):
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            self.skipTest("Requires optional MCP SDK extra.")
        self.write("wire.txt", "directorywire 中文\r\n")
        run = self.sync(root="src")
        async def exercise():
            params = StdioServerParameters(command=sys.executable, args=["-m", "contextrail", "mcp", "--workspace", str(self.root), "--session", "owner"])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("context.directory_status", {"run": run["run"], "limit": 1})
                    self.assertFalse(result.isError)
                    body = json.loads(result.content[0].text)
                    ref = body["entries"][0]["ref"]
                    evidence = await session.call_tool("context.get", ref)
                    self.assertEqual(json.loads(evidence.content[0].text)["content"], "directorywire 中文\r\n")
                    denied = await session.call_tool("context.directory_status", {"run": run["run"], "root": str(self.root)})
                    self.assertTrue(denied.isError)
                    self.assertEqual(json.loads(denied.content[0].text)["error"]["code"], "invalid_request")
        asyncio.run(exercise())

    def test_oversize_file_defers_then_resumes_and_binary_remains_exact(self):
        self.write("large.txt", "sizeboundneedle " * 8)
        self.write("raw.bin", b"\xff\x00\r\n")
        partial = self.sync(root="src", max_file_bytes=8)
        self.assertEqual(partial["status"], "partial")
        self.assertTrue(any(e["reason"] == "max_file_bytes" for e in self.entries(partial["run"])))
        done = self.sync(resume=partial["run"], step=partial["step"], max_file_bytes=256)
        self.assertEqual(done["status"], "complete")
        config = load_project(self.root)
        with Store(config.store) as store:
            raw = next(e["ref"] for e in self.entries(done["run"]) if e["path"] == "src/raw.bin")
            self.assertEqual(store.get(config.scope, Ref(**raw)).content, b"\xff\x00\r\n")
            self.assertEqual(store.index_status(config.scope)["excluded"], 1)

    def test_gitignored_tracked_file_is_excluded_and_explicit_disable_is_supported(self):
        self.write("tracked.py", "trackedignoredneedle")
        self.write("keep.py", "trackedkeepneedle")
        (self.root / ".gitignore").write_text("src/tracked.py\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "init", "--quiet"], capture_output=True, check=True)
        subprocess.run(["git", "-C", str(self.root), "add", "--force", "src/tracked.py"], capture_output=True, check=True)
        result = self.sync(root="src")
        self.assertTrue(any(e["path"] == "src/tracked.py" and e["reason"] == "gitignored" for e in self.entries(result["run"])))
        self.config_file.write_text(self.config_file.read_text() + '[directory]\nrespect_gitignore=false\n', encoding="utf-8")
        explicit = self.sync(root="src")
        self.assertTrue(any(e["path"] == "src/tracked.py" and e["outcome"] == "captured" for e in self.entries(explicit["run"])))

    def test_filename_query_returns_source_text_instead_of_blank_structural_gaps(self):
        self.write("structured.py", '"""A source module."""\n\nimport os\n\n\ndef work():\n    return "value"\n\n')
        self.sync(root="src")
        config = load_project(self.root)
        with Store(config.store) as store:
            hits = store.search_indexed(config.scope, "structured.py")["hits"]
            self.assertTrue(hits)
            self.assertTrue(all(hit["snippet"].strip() for hit in hits), hits)

    def test_page_byte_budget_limits_actual_reads_and_defers_next_file(self):
        self.write("a.txt", "123456")
        self.write("b.txt", "abcdef")
        from contextrail.directory_sync import _read_file
        read_bytes = []
        def observed_read(*args):
            result = _read_file(*args)
            read_bytes.append(len(result[0]))
            return result
        with patch("contextrail.directory_sync.MAX_PAGE_BYTES", 10), patch("contextrail.directory_sync._read_file", side_effect=observed_read):
            first = self.sync(root="src")
            self.assertEqual(first["status"], "partial")
            self.assertEqual(first["counts"]["captured"], 1)
            self.assertLessEqual(sum(read_bytes), 10)
            second = self.sync(resume=first["run"], step=first["step"])
            self.assertEqual(second["status"], "complete")
            self.assertEqual(second["counts"]["captured"], 2)


if __name__ == "__main__":
    unittest.main()
