"""Offline recovery through the existing Rail store and memory queue."""

from contextlib import closing
from dataclasses import asdict, replace
import base64
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from contextrail import Selection, Store, Target
from contextrail.ingest_delivery import SCHEMA as QUEUE_SCHEMA
from contextrail.project import load_project
from tests import test_native_client_context as native


ROOT = Path(__file__).resolve().parents[1]


class OfflineTaskNavigationStage(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="rail-offline-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / ".contextrail.toml").write_text('[project]\ntask="one"\n', encoding="utf-8")
        config = load_project(self.root)
        config.store.parent.mkdir()
        self.store = Store(config.store)
        self.addCleanup(self.store.close)
        self.scope = config.scope
        self.lease = self.store.create_task(self.scope, Target("owner", "host", "offline"),
                                           "保留末尾纠正", constraints=("最后要求使用圆锥曲线",),
                                           acceptance=("保留 CRLF 原文",), allowed_providers=("host",))
        self.ref = self.store.put(self.scope, self.lease, "requirements", "圆锥曲线原文\r\n".encode(), expected_revision=0)
        self.store.begin_action(self.scope, self.lease, "unknown-action")
        self.snapshot = self.store.snapshot(self.scope, self.lease, (Selection(self.ref, required=True),))

    def cli(self, command, *args, session="owner", ok=True):
        result = subprocess.run([sys.executable, "-m", "contextrail", command, "--workspace", str(self.root),
                                 "--session", session, *map(str, args)], cwd=ROOT, capture_output=True, timeout=25)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        return json.loads(result.stderr)["error"]["code"]

    def binding(self, home):
        path = self.root / "memory-binding.json"
        path.write_text(json.dumps({"schema": 1, "task": "one", "workspace": str(self.root),
                                    "memory_home": str(home), "memory_mode": "hybrid"}), encoding="utf-8")
        return path

    def test_offline_brief_saves_exact_requirements_and_observes_original_pending_queue(self):
        home = self.root / "existing-memory"
        home.mkdir()
        retry = time.time() + 3600
        queue = home / "state.sqlite"
        with closing(sqlite3.connect(queue)) as db:
            db.execute("CREATE TABLE jobs(id TEXT PRIMARY KEY,source TEXT,payload TEXT,state TEXT,"
                       "attempts INTEGER,created REAL,available REAL)")
            db.executescript(QUEUE_SCHEMA)
            for index, identity, workspace, state in (
                (1, "quota-job", self.root, "retry"), (2, "index-job", self.root, "awaiting_index"),
                (3, "saved-job", self.root, "saved"), (4, "other-job", self.root / "other", "queued"),
            ):
                db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?)",
                           (identity, "native", json.dumps({"cwd": str(workspace)}), state, 2, index, retry))
            db.execute("INSERT INTO job_control(job_id,reason,next_retry,stage,updated) VALUES(?,?,?,?,?)",
                       ("quota-job", "quota_exhausted", retry, "waiting", time.time()))
            db.commit()
        state = self.root / "STATE 离线.md"
        options = ("--offline", "--snapshot", self.snapshot, "--query-binding", self.binding(home), "--queue-limit", 1)
        output = self.cli("brief", *options, "--state-output", state)
        self.assertEqual(output["task"]["objective"], "保留末尾纠正")
        self.assertEqual(output["task"]["constraints"], ["最后要求使用圆锥曲线"])
        self.assertEqual(output["required"][0]["text"], "圆锥曲线原文\r\n")
        self.assertIn("snapshot.created", {item["kind"] for item in output["explanation"]["steps"]["entries"]})
        self.assertEqual(output["explanation"]["actions"]["entries"], [{"id": "unknown-action", "state": "pending"}])
        learning = output["offline"]["learning"]
        self.assertEqual((learning["pending"], learning["next_offset"]), (2, 1))
        self.assertEqual(learning["entries"][0]["id"], "quota-job")
        self.assertEqual(learning["entries"][0]["next_retry"], retry)
        self.assertEqual(learning["entries"][0]["reason"], "quota_exhausted")
        self.assertEqual(output["offline"]["model_calls"], 0)
        exported = json.loads(state.read_text(encoding="utf-8").split("\n```json\n", 1)[1].rsplit("\n```", 1)[0])
        self.assertEqual(exported["snapshot"], self.snapshot)
        self.assertEqual(exported["offline"]["learning"]["entries"], learning["entries"])
        page = self.cli("brief", *options, "--queue-offset", 1)
        self.assertEqual([entry["id"] for entry in page["offline"]["learning"]["entries"]], ["index-job"])
        request = self.root / "read-ref.json"
        request.write_text(json.dumps([{"id": "original", "tool": "context.get",
                                        "arguments": output["required"][0]["ref"]}]), encoding="utf-8")
        read = self.cli("batch-read", "--calls", request)
        self.assertEqual(read["results"][0]["result"]["content"], "圆锥曲线原文\r\n")
        with closing(sqlite3.connect(queue)) as db:
            self.assertEqual(db.execute("SELECT state,attempts FROM jobs WHERE id='quota-job'").fetchone(), ("retry", 2))

    def test_offline_brief_reads_task_when_existing_memory_is_unavailable(self):
        output = self.cli("brief", "--offline", "--snapshot", self.snapshot,
                          "--query-binding", self.binding(self.root / "disconnected"))
        self.assertEqual(output["required"][0]["ref"], asdict(self.ref))
        self.assertEqual(output["offline"]["learning"]["state"], "unavailable")
        self.assertIsNone(output["offline"]["learning"]["pending"])

    def test_offline_bridge_keeps_exact_source_checks_for_brief_and_bookmark(self):
        fixture = native.NativeContextTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        snapshot = fixture.exported["snapshot"]
        brief = fixture.cli("brief", "--session", "owner", "--offline", "--snapshot", snapshot, "--budget", "20000")
        self.assertEqual(brief["task"]["objective"], fixture.statement)
        self.assertEqual(brief["bridge"]["required_ids"], ["R001"])
        fixture.cli("bookmark-set", "--session", "owner", "--name", "source", "--snapshot", snapshot, "--epoch", 0)
        (fixture.bundle / "user.md").write_text("修改后的外部来源", encoding="utf-8")
        for result in (fixture.run_cli("brief", "--session", "owner", "--offline", "--snapshot", snapshot, "--budget", "20000"),
                       fixture.run_cli("bookmark-open", "--session", "owner", "--name", "source")):
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stderr)["error"]["code"], "stale_snapshot")
        self.assertIsNone(fixture.cli("bookmark-list", "--session", "owner")["entries"][0]["last_opened_at"])

    def test_model_free_record_uses_the_existing_memory_owner_and_keeps_task_readable(self):
        home = self.root / "offline-memory"
        app = home / "app"
        app.mkdir(parents=True)
        vault = self.root / "notes"
        vault.mkdir()
        installed = Path.home() / ".codex/services/engineering-memory/app"
        try:
            for name in ("canonical.py", "capture_sanitizer.py", "checkpoint_lifecycle.py"):
                shutil.copyfile(installed / name, app / name)
        except FileNotFoundError:
            self.skipTest("Requires the existing engineering-memory runtime dependencies.")
        shutil.copyfile(ROOT / "integrations/engineering_memory/memory_runtime.py", app / "memory_runtime.py")
        (home / "config.json").write_text(json.dumps({"vault": str(vault)}), encoding="utf-8")
        request = self.root / "explicit-record.json"
        text = "离线保存本轮用户说明。"
        request.write_text(json.dumps({"event_id": "offline-record", "title": "本轮说明", "text": text}),
                           encoding="utf-8")
        saved = subprocess.run([sys.executable, "-m", "contextrail", "memory-save", "--workspace", str(self.root),
                                "--home", str(home), "--input", str(request)],
                               cwd=ROOT, capture_output=True, timeout=30)
        self.assertEqual(saved.returncode, 0, saved.stderr.decode("utf-8", "replace"))
        record = json.loads(saved.stdout)
        self.assertEqual(record["model_calls"], 0)
        self.assertIn(text, Path(record["source"]).read_text(encoding="utf-8"))
        brief = self.cli("brief", "--offline", "--snapshot", self.snapshot, "--query-binding", self.binding(home))
        self.assertEqual(brief["task"]["objective"], "保留末尾纠正")
        self.assertEqual(brief["offline"]["model_calls"], 0)

    def save_bookmark(self, name="圆锥", *, scope=None, snapshot=None, query=None, session="owner"):
        args = ["--name", name, "--task", (scope or self.scope).task, "--snapshot", snapshot or self.snapshot, "--epoch", 0]
        if query is not None:
            args += ["--query", query]
        return self.cli("bookmark-set", *args, session=session)

    def test_bookmark_opens_pinned_task_search_and_rejects_stale_snapshot(self):
        self.save_bookmark(query="曲线")
        other = replace(self.scope, task="two")
        lease = self.store.create_task(other, Target("owner", "host", "offline"), "other",
                                       allowed_providers=("host",))
        other_snapshot = self.store.snapshot(other, lease, ())
        self.save_bookmark("other", scope=other, snapshot=other_snapshot)
        opened = self.cli("bookmark-open", "--name", "圆锥", "--task", "two")
        self.assertEqual(opened["bookmark"]["scope"], asdict(self.scope))
        self.assertEqual(opened["packet"]["snapshot"], self.snapshot)
        self.assertEqual(opened["search"]["hits"][0]["ref"]["name"], self.ref.name)
        resume = subprocess.run(opened["packet"]["resume"]["argv"], cwd=ROOT, capture_output=True, timeout=20)
        self.assertEqual(resume.returncode, 0, resume.stderr)
        self.assertEqual(json.loads(resume.stdout)["snapshot"], self.snapshot)
        listing = self.cli("bookmark-list", "--task", "two")
        self.assertEqual([entry["name"] for entry in listing["entries"]], ["圆锥", "other"])
        current = self.store.task(self.scope)["version"]
        self.store.update_task(self.scope, self.lease, expected_version=current, objective="后续纠正",
                               constraints=(), acceptance=())
        self.store.snapshot(self.scope, self.lease, (Selection(self.ref, required=True),))
        self.assertEqual(self.cli("bookmark-open", "--name", "圆锥", "--task", "two", ok=False), "stale_snapshot")
        self.assertEqual(self.cli("bookmark-list", "--prefix", "圆")["entries"][0]["state"], "stale")

    def test_bookmark_listing_and_mutations_enforce_the_pinned_task_membership(self):
        self.save_bookmark()
        other = replace(self.scope, task="other")
        other_lease = self.store.create_task(other, Target("different-owner", "host", "offline"),
                                             "different owner", allowed_providers=("host",))
        other_snapshot = self.store.snapshot(other, other_lease, ())
        self.save_bookmark("other", scope=other, snapshot=other_snapshot, session="different-owner")
        listing = self.cli("bookmark-list", "--task", "other", session="different-owner")
        self.assertEqual([entry["name"] for entry in listing["entries"]], ["other"])
        self.assertEqual(self.cli("bookmark-open", "--task", "other", "--name", "圆锥",
                                  session="different-owner", ok=False), "not_found")
        self.assertEqual(self.cli("bookmark-remove", "--task", "other", "--name", "圆锥", "--epoch", 0,
                                  session="different-owner", ok=False), "not_found")
        self.cli("bookmark-remove", "--name", "圆锥", "--epoch", 0)
        self.assertEqual(self.cli("bookmark-open", "--name", "圆锥", ok=False), "not_found")

    def test_saved_single_letter_search_has_a_literal_fallback(self):
        formula = self.store.put(self.scope, self.lease, "formula", b"x=1", expected_revision=0)
        snapshot = self.store.snapshot(self.scope, self.lease, (Selection(formula, required=True),))
        self.save_bookmark("formula", snapshot=snapshot, query="x")
        result = self.cli("bookmark-open", "--name", "formula")
        self.assertEqual(result["packet"]["snapshot"], snapshot)
        self.assertEqual(result["search"]["refs"], [asdict(formula)])

    @unittest.skipUnless(os.name == "nt", "Real Windows PowerShell command completion")
    def test_powershell_completes_visible_unicode_bookmark(self):
        self.save_bookmark()
        self.save_bookmark("other")
        command = self.root / "contextrail.cmd"
        command.write_bytes(b"@exit /b 0\r\n")
        script = r"""
        $ErrorActionPreference = 'Stop'
        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
        & $env:RAIL_COMPLETION -Workspace $env:RAIL_PROJECT -Session owner -Python $env:RAIL_PYTHON
        $line = 'contextrail bookmark-open --name ' + $env:RAIL_PREFIX
        $completed = (TabExpansion2 -InputScript $line -CursorColumn $line.Length).CompletionMatches
        ConvertTo-Json -InputObject @($completed | ForEach-Object CompletionText) -Compress
        """
        encoded = base64.b64encode(script.encode("utf-16-le")).decode()
        pwsh = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        env = {**os.environ, "PATH": str(self.root) + os.pathsep + os.environ["PATH"],
               "RAIL_COMPLETION": str(ROOT / "Enable-ContextRailCompletion.ps1"), "RAIL_PROJECT": str(self.root),
               "RAIL_PYTHON": sys.executable, "RAIL_PREFIX": "圆"}
        result = subprocess.run([str(pwsh), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                cwd=ROOT, env=env, capture_output=True, timeout=20,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["圆锥"])
