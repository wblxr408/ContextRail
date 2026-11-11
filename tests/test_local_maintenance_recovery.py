"""H06/H07 recovery from observed database and file state."""

from contextlib import closing
from dataclasses import asdict, replace
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile

from contextrail import Ref, Selection, Store
from contextrail.project import load_project
from contextrail.models import Target
from contextrail.tool_results import OutputRecorder


class LocalMaintenanceRecovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rail-maintenance-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "任务 workspace"
        self.workspace.mkdir()
        subprocess.run(["git", "-C", str(self.workspace), "init"], check=True, capture_output=True, timeout=10)
        self.config = self.workspace / ".contextrail.toml"
        self.config.write_bytes('[project]\ntask="repair"\n'.encode())
        config = load_project(self.workspace)
        self.scope, self.db = config.scope, config.store
        self.db.parent.mkdir()
        self.content = "## 任务说明\r\n\r\nrecoverable_keyword 结果待核对。\r\n".encode()
        with Store(self.db) as store:
            store.db.execute("PRAGMA journal_mode=WAL")
            self.lease = store.create_task(self.scope, Target("owner", "host", "external"),
                                           "保留原有任务和动作", acceptance=("找回原文和终态",),
                                           allowed_providers=("host",))
            required = store.put(self.scope, self.lease, "requirements", self.content, expected_revision=0)
            store.begin_action(self.scope, self.lease, "completed")
            store.finish_action(self.scope, self.lease, "completed", state="succeeded")
            store.begin_action(self.scope, self.lease, "in-flight")
            self.snapshot = store.snapshot(self.scope, self.lease, (Selection(required, required=True),))

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "contextrail", *map(str, args)], capture_output=True,
                              cwd=Path(__file__).resolve().parents[1], timeout=30)

    def cli(self, *args):
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        return json.loads(result.stdout)

    def verify_recovered(self, path):
        with Store(path) as store:
            self.assertEqual(store.task(self.scope)["acceptance"], ["找回原文和终态"])
            self.assertEqual(store.get(self.scope, Ref("requirements", 1)).content, self.content)
            selected = store.load_snapshot(self.scope, self.snapshot)["evidence"]
            self.assertEqual([(item["ref"]["name"], item["required"]) for item in selected],
                             [("requirements", True)])
            self.assertFalse(store.begin_action(self.scope, self.lease, "completed"))
            self.assertFalse(store.begin_action(self.scope, self.lease, "in-flight"))
            store.finish_action(self.scope, self.lease, "completed", state="succeeded")
            actions = {row["id"]: row["state"] for row in store.db.execute("SELECT id,state FROM actions")}
            self.assertEqual(actions, {"completed": "succeeded", "in-flight": "pending"})

    def test_online_snapshot_restores_task_and_queue_without_replaying_work(self):
        archive = self.root / "备份 archive"
        recovery = self.root / "恢复 review"
        knowledge = self.root / "知识 notes"
        knowledge.mkdir()
        note = "原知识\r\n".encode()
        (knowledge / "中文.md").write_bytes(note)
        (knowledge / "credentials.json").write_bytes(b"excluded private material")
        queue = self.root / "queue.sqlite"
        with closing(sqlite3.connect(queue)) as worker, closing(sqlite3.connect(self.db)) as writer:
            worker.execute("PRAGMA journal_mode=WAL")
            worker.execute("CREATE TABLE jobs(id TEXT PRIMARY KEY, status TEXT)")
            worker.execute("INSERT INTO jobs VALUES('queued','retry')")
            worker.commit()
            worker.execute("INSERT INTO jobs VALUES('uncommitted','processing')")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("UPDATE tasks SET constraints_json=?", (json.dumps(["从 WAL 恢复约束"]),))
            writer.commit()
            writer.execute("UPDATE tasks SET objective='uncommitted'")
            manifest = self.cli("backup-create", "--workspace", self.workspace, "--output", archive,
                                "--knowledge-root", knowledge, "--queue-db", queue)
            self.assertEqual(manifest["scope"], asdict(self.scope))
            self.assertEqual(manifest["roles"]["knowledge"]["files"], 1)
            self.assertEqual(manifest["roles"]["rail"]["state"]["actions_needing_review"], 1)
            worker.rollback()
            writer.rollback()
        (knowledge / "中文.md").write_text("后来变了", encoding="utf-8")
        with Store(self.db) as original:
            original.finish_action(self.scope, self.lease, "in-flight", state="cancelled")
        result = self.cli("backup-restore", "--backup", archive, "--output", recovery)
        self.assertEqual(result["activation"], "manual_review")
        self.assertEqual(result["state"]["selected_task_review"]["pending_actions"], ["in-flight"])
        self.assertEqual((recovery / "knowledge" / "中文.md").read_bytes(), note)
        self.assertEqual((recovery / "project.toml").read_bytes(), self.config.read_bytes())
        with closing(sqlite3.connect(recovery / "queue.sqlite3")) as restored:
            self.assertEqual(restored.execute("SELECT * FROM jobs").fetchall(), [("queued", "retry")])
        with Store(recovery / "rail.sqlite3") as restored:
            self.assertEqual(restored.task(self.scope)["objective"], "保留原有任务和动作")
            self.assertEqual(restored.task(self.scope)["constraints"], ["从 WAL 恢复约束"])
        self.verify_recovered(recovery / "rail.sqlite3")
        conflict = self.run_cli("backup-restore", "--backup", archive, "--output", recovery)
        self.assertEqual(json.loads(conflict.stderr)["error"]["code"], "conflict")

    def test_opt_in_code_archive_captures_committed_code(self):
        (self.workspace / "code.txt").write_bytes(b"committed code\n")
        for args in (("-c", "core.autocrlf=false", "add", "code.txt"),
                     ("-c", "user.name=Backup Test", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture")):
            subprocess.run(["git", "-C", str(self.workspace), *args], check=True, capture_output=True, timeout=10)
        (self.workspace / "code.txt").write_bytes(b"working copy\n")
        archive = self.root / "versioned files"
        manifest = self.cli("backup-create", "--workspace", self.workspace, "--output", archive, "--include-code")
        self.assertEqual(manifest["roles"]["code"]["basis"], "committed_head")
        with zipfile.ZipFile(archive / "source.zip") as source:
            self.assertEqual(source.read("code.txt"), b"committed code\n")

    def test_index_recovery_rebuilds_from_original_bytes_and_keeps_failed_source(self):
        other_scope = replace(self.scope, task="another-task")
        with Store(self.db) as store:
            lease = store.create_task(other_scope, Target("owner", "host", "external"),
                                      "保留同库任务", allowed_providers=("host",))
            store.put(other_scope, lease, "other", b"other_keyword", expected_revision=0)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("DELETE FROM evidence_fts_data")
            conn.commit()
        output = self.root / "修复 copy"
        result = self.cli("index-recover", "--workspace", self.workspace, "--output", output)
        self.assertEqual(result["activation"], "manual_review")
        self.assertEqual(result["state"]["actions_needing_review"], 1)
        self.assertEqual(result["state"]["tasks"], 2)
        self.verify_recovered(output / "rail.sqlite3")
        matched = self.cli("search-indexed", "--workspace", self.workspace, "--store", output / "rail.sqlite3",
                           "--session", "owner", "--query", "recoverable_keyword")
        self.assertEqual(matched["hits"][0]["ref"]["name"], "requirements")
        matched = self.cli("search-indexed", "--workspace", self.workspace, "--store", output / "rail.sqlite3",
                           "--task", "another-task", "--session", "owner", "--query", "other_keyword")
        self.assertEqual(matched["hits"][0]["ref"]["name"], "other")
        original = self.run_cli("search-indexed", "--workspace", self.workspace, "--session", "owner",
                                "--query", "recoverable_keyword")
        if original.returncode == 0:
            self.assertEqual(json.loads(original.stdout)["hits"], [])
        else:
            self.assertEqual(json.loads(original.stderr)["error"]["code"], "storage_error")

    def test_recovery_refuses_an_invalid_action_state_without_clearing_authority(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("UPDATE actions SET state='unverified' WHERE id='in-flight'")
            conn.commit()
        result = self.run_cli("index-recover", "--workspace", self.workspace,
                              "--output", self.root / "refused recovery")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "integrity_error")
        with Store.open_readonly(self.db) as original:
            self.assertEqual(original.get(self.scope, Ref("requirements", 1)).content, self.content)
            self.assertEqual(original.db.execute("SELECT state FROM actions WHERE id='in-flight'").fetchone()[0],
                             "unverified")

    def test_missing_authoritative_table_is_not_replaced_with_an_empty_one(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("DROP TABLE snapshots")
            conn.commit()
        result = self.run_cli("index-recover", "--workspace", self.workspace, "--output", self.root / "invalid")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "integrity_error")

    def test_timeline_is_recovered_from_saved_tool_results(self):
        with Store(self.db) as store:
            OutputRecorder(store, self.scope, self.lease, self.workspace).capture(
                {"call_id": "observed-tool", "tool": "shell", "argv": ["python"], "cwd": str(self.workspace),
                 "started_at": 10, "finished_at": 11, "exit_code": 1, "stdout": "", "stderr": "SyntaxError",
                 "source_paths": ["src/源文件.py"]}, budget=8192)
            store.db.execute("DELETE FROM timeline_entries")
        output = self.root / "timeline review"
        self.cli("index-recover", "--workspace", self.workspace, "--output", output)
        view = self.cli("timeline", "--workspace", self.workspace, "--store", output / "rail.sqlite3",
                        "--session", "owner", "--role", "tool")
        self.assertEqual([(entry["call_id"], entry["status"]) for entry in view["entries"]],
                         [("observed-tool", "failed")])
