"""Current A03/D02 consumer contract against the actual installed Bridge validator."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from contextrail import Lease, Ref, Scope, Selection, Store, Target


class BridgeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rail-bridge-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / ".contextrail.toml").write_text('[project]\ntask="demo-task"\n', encoding="utf-8")
        self.validator = Path.home() / ".codex/skills/context-bridge/scripts/requirements.py"
        self.bundle = self.root / "bundle"
        (self.bundle / "evidence").mkdir(parents=True)
        self.source = b"old product discussion\r\n" + "我现在完善自用 demo，按 TODO 持续实现。\r\n保留原文，不静默丢要求。\r\n".encode()
        (self.bundle / "evidence/user.md").write_bytes(self.source)
        (self.bundle / "evidence/large.log").write_bytes(b"long optional evidence\r\n" * 5000)
        def row(rid, text, kind="constraint", scope="global", always=True, **extra):
            return {"id": rid, "revision": 1, "status": "active", "kind": kind, "scope": scope,
                    "always_load": always, "statement": text,
                    "source": {"path": "evidence/user.md", "quote": "保留原文，不静默丢要求。", "locator": "latest user turn"},
                    "acceptance": "能回读指定版本并遵循当前要求", "progress": "pending", "evidence": [], **extra}
        self.ledger = {"schema": 1, "task_id": "demo-task", "snapshot_id": "demo-task/revision-1", "requirements": [
            row("R001", "把产品做成销售平台", kind="goal", status="superseded", superseded_by="R003"),
            row("R002", "保留原文，不静默丢要求。"),
            row("R003", "完善自用 demo，按 TODO 持续实现", kind="goal"),
            row("R004", "本轮交付源码", kind="deliverable", scope="implementation", always=False,
                progress="done", evidence=["evidence/large.log"]),
            row("R005", "以后写报告", kind="deliverable", scope="docs", always=False)]}
        self.write_ledger()
        self.initial = self.cli("project-init", "--session", "host", "--objective", "temporary runtime placeholder")

    def write_ledger(self):
        (self.bundle / "requirements.json").write_bytes(json.dumps(self.ledger, ensure_ascii=False, indent=2).encode())

    def run_cli(self, command, *args):
        return subprocess.run([sys.executable, "-m", "contextrail", command, "--workspace", str(self.workspace),
                               *map(str, args)], capture_output=True, timeout=40)

    def cli(self, command, *args):
        result = self.run_cli(command, *args)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        return json.loads(result.stdout)

    def export(self):
        return self.cli("bridge-import", "--bundle", self.bundle, "--scope", "implementation", "--session", "host",
                        "--epoch", "0", "--bridge-validator", self.validator)

    def packet(self, exported, budget=7000):
        return self.cli("bridge-packet", "--session", "host", "--snapshot", exported["snapshot"], "--budget", budget)

    def test_complete_export_preserves_origin_and_pins_active_correction(self):
        original = (self.bundle / "requirements.json").read_bytes()
        exported = self.export()
        packet = self.packet(exported)
        self.assertEqual(packet["bridge_snapshot"], "demo-task/revision-1")
        self.assertEqual(packet["required_ids"], ["R002", "R003"])
        context = packet["context"]
        self.assertEqual(context["task"]["objective"], "完善自用 demo，按 TODO 持续实现")
        included = {entry["ref"]["name"] for entry in context["evidence"]}
        with Store(self.initial["store"]) as store:
            scope = Scope(**self.initial["scope"])
            manifest = json.loads(store.get(scope, Ref(**exported["manifest_ref"])).content)
            self.assertEqual(store.get(scope, Ref(**manifest["ledger_ref"])).content, original)
            self.assertEqual(store.get(scope, Ref(**manifest["sources"]["evidence/user.md"])).content, self.source)
            for row in self.ledger["requirements"]:
                entry = manifest["requirements"][row["id"]]
                self.assertEqual(json.loads(store.get(scope, Ref(**entry["ref"])).content), row)
                if row["id"] in ("R002", "R003"):
                    self.assertIn(entry["ref"]["name"], included)
                if row["status"] == "superseded":
                    self.assertNotIn(entry["ref"]["name"], included)
            version = store.task(scope)["version"]
        retry = self.export()
        self.assertEqual(retry["snapshot"], exported["snapshot"])
        with Store(self.initial["store"]) as store:
            self.assertEqual(store.task(scope)["version"], version)
        self.assertEqual((self.bundle / "requirements.json").read_bytes(), original)
        self.assertEqual((self.bundle / "evidence/user.md").read_bytes(), self.source)

    def test_revision_and_live_source_changes_never_reuse_old_current_packet(self):
        old = self.export()
        self.ledger["snapshot_id"] = "demo-task/revision-2"
        self.ledger["requirements"][1].update(revision=2, statement="最新要求：保留失败证据和原始退出码")
        self.write_ledger()
        stale = self.run_cli("bridge-packet", "--session", "host", "--snapshot", old["snapshot"], "--budget", "7000")
        self.assertEqual(json.loads(stale.stderr)["error"]["code"], "stale_snapshot")
        new = self.export()
        self.assertNotEqual(old["snapshot"], new["snapshot"])
        with Store(self.initial["store"]) as store:
            scope = Scope(**self.initial["scope"])
            historical = json.loads(store.get(scope, Ref(**old["manifest_ref"])).content)
            self.assertEqual(json.loads(store.get(scope, Ref(**historical["requirements"]["R002"]["ref"])).content)["revision"], 1)
        packet = self.packet(new)
        self.assertIn("最新要求", " ".join(packet["context"]["task"]["constraints"]))
        stale = self.run_cli("bridge-packet", "--session", "host", "--snapshot", old["snapshot"])
        self.assertEqual(json.loads(stale.stderr)["error"]["code"], "stale_snapshot")

    def test_invalid_revision_and_changed_checkpoint_fail_without_mutating_task(self):
        self.export()
        scope = Scope(**self.initial["scope"])
        with Store(self.initial["store"]) as store:
            before = store.task(scope)
        self.ledger["requirements"][1]["statement"] = "changed without revision"
        self.write_ledger()
        args = ("--bundle", self.bundle, "--scope", "implementation", "--session", "host", "--epoch", "0",
                "--bridge-validator", self.validator)
        result = self.run_cli("bridge-import", *args)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "conflict")
        self.ledger["snapshot_id"] = "demo-task/revision-2"
        self.write_ledger()
        result = self.run_cli("bridge-import", *args)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "invalid_request")
        with Store(self.initial["store"]) as store:
            self.assertEqual(store.task(scope), before)

    def test_receipt_is_checked_per_id_revision_and_never_auto_attested(self):
        exported = self.export()
        receipt = {"task_id": "demo-task", "snapshot_id": "demo-task/revision-1", "scope": "implementation", "checks": [
            {"id": rid, "revision": 1, "result": "aligned", "interpretation": "按指定原文理解该要求", "next_action": "完成本 scope 源码交付"}
            for rid in ("R002", "R003", "R004")]}
        path = self.root / "receiver.json"
        path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
        args = ("--session", "host", "--snapshot", exported["snapshot"], "--receipt", path, "--bridge-validator", self.validator)
        valid = self.cli("bridge-receipt", *args)
        self.assertEqual(valid["semantic_alignment"], "receiver_attested_not_machine_proven")
        receipt["checks"][0]["revision"] = 2
        path.write_text(json.dumps(receipt), encoding="utf-8")
        invalid = self.run_cli("bridge-receipt", *args)
        self.assertEqual(json.loads(invalid.stderr)["error"]["code"], "invalid_request")
        receipt["checks"] = receipt["checks"][1:]
        path.write_text(json.dumps(receipt), encoding="utf-8")
        invalid = self.run_cli("bridge-receipt", *args)
        self.assertEqual(json.loads(invalid.stderr)["error"]["code"], "invalid_request")

    def test_mandatory_budget_failure_identifies_requirement_ids(self):
        exported = self.export()
        result = self.run_cli("bridge-packet", "--session", "host", "--snapshot", exported["snapshot"], "--budget", "100")
        error = json.loads(result.stderr)["error"]
        self.assertEqual(error["code"], "budget_exceeded")
        self.assertIn("R002", error["message"])
        self.assertIn("R003", error["message"])

    def test_storage_failure_rolls_back_objective_artifacts_and_snapshot(self):
        scope = Scope(**self.initial["scope"])
        with Store(self.initial["store"]) as store:
            before = store.task(scope)
            store.db.execute("CREATE TRIGGER reject_second_artifact BEFORE INSERT ON artifacts "
                             "WHEN (SELECT COUNT(*) FROM artifacts WHERE scope=NEW.scope)>0 "
                             "BEGIN SELECT RAISE(ABORT,'injected storage failure'); END")
        result = self.run_cli("bridge-import", "--bundle", self.bundle, "--scope", "implementation", "--session", "host",
                              "--epoch", "0", "--bridge-validator", self.validator)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "storage_error")
        with Store(self.initial["store"]) as store:
            self.assertEqual(store.task(scope), before)
            self.assertEqual(store.search(scope, "保留原文"), [])
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM snapshots WHERE scope=?", (scope.key,)).fetchone()[0], 0)

    def test_invalid_task_quote_and_escaped_source_never_publish(self):
        baseline = copy.deepcopy(self.ledger)
        cases = [("task", "access_denied"), ("quote", "invalid_request"), ("path", "access_denied")]
        for kind, expected in cases:
            with self.subTest(kind=kind):
                self.ledger = copy.deepcopy(baseline)
                if kind == "task":
                    self.ledger.update(task_id="another-task", snapshot_id="another-task/rev-1")
                elif kind == "quote":
                    self.ledger["requirements"][1]["source"]["quote"] = "not present anywhere"
                else:
                    self.ledger["requirements"][1]["source"]["path"] = "../outside.txt"
                self.write_ledger()
                result = self.run_cli("bridge-import", "--bundle", self.bundle, "--scope", "implementation",
                                      "--session", "host", "--epoch", "0", "--bridge-validator", self.validator)
                self.assertEqual(json.loads(result.stderr)["error"]["code"], expected)
                with Store(self.initial["store"]) as store:
                    self.assertEqual(store.task(Scope(**self.initial["scope"]))["version"], 0)

    def test_independent_runtime_constraints_survive_bridge_updates(self):
        scope = Scope(**self.initial["scope"])
        with Store(self.initial["store"]) as store:
            store.update_task(scope, Lease("host", 0), expected_version=0, objective="local runtime",
                               constraints=("保留宿主明确要求：不能覆盖其他 worktree",), acceptance=("主机验收",))
        first = self.export()
        packet = self.packet(first)
        self.assertIn("保留宿主明确要求：不能覆盖其他 worktree", packet["context"]["task"]["constraints"])
        with Store(self.initial["store"]) as store:
            current = store.task(scope)
            store.update_task(scope, Lease("host", 0), expected_version=current["version"], objective=current["objective"],
                               constraints=tuple(current["constraints"]) + ("宿主后来新增的限制",), acceptance=tuple(current["acceptance"]))
        self.ledger["snapshot_id"] = "demo-task/revision-2"
        self.ledger["requirements"][1].update(revision=2, statement="新的 Bridge 约束")
        self.write_ledger()
        second = self.export()
        packet = self.packet(second)
        constraints = packet["context"]["task"]["constraints"]
        self.assertIn("保留宿主明确要求：不能覆盖其他 worktree", constraints)
        self.assertIn("新的 Bridge 约束", constraints)
        self.assertIn("宿主后来新增的限制", constraints)
        self.assertNotIn("保留原文，不静默丢要求。", constraints)

    def test_changed_or_missing_source_is_stale_while_stored_original_remains_readable(self):
        exported = self.export()
        source = self.bundle / "evidence/user.md"
        source.write_bytes(self.source + b"source changed outside Rail\r\n")
        result = self.run_cli("bridge-packet", "--session", "host", "--snapshot", exported["snapshot"])
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "stale_snapshot")
        source.unlink()
        result = self.run_cli("bridge-packet", "--session", "host", "--snapshot", exported["snapshot"])
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "stale_snapshot")
        with Store(self.initial["store"]) as store:
            scope = Scope(**self.initial["scope"])
            manifest = json.loads(store.get(scope, Ref(**exported["manifest_ref"])).content)
            self.assertEqual(store.get(scope, Ref(**manifest["sources"]["evidence/user.md"])).content, self.source)
        source.write_bytes(self.source)
        self.assertEqual(self.packet(exported)["required_ids"], ["R002", "R003"])

    def test_terminal_navigation_lists_exact_snapshots_and_executes_returned_resume_argv(self):
        first = self.export()
        self.ledger["snapshot_id"] = "demo-task/revision-2"
        self.ledger["requirements"][1].update(revision=2, statement="当前导航要求")
        self.write_ledger()
        second = self.export()
        state = self.cli("status", "--session", "host")
        self.assertEqual(state["scope"]["task"], "demo-task")
        self.assertEqual(state["checkpoints"]["total"], 2)
        newest = self.cli("checkpoints", "--session", "host", "--limit", "1")
        older = self.cli("checkpoints", "--session", "host", "--offset", "1", "--limit", "1")
        self.assertEqual(newest["entries"][0]["snapshot"], second["snapshot"])
        self.assertTrue(newest["entries"][0]["store_current"])
        self.assertEqual(older["entries"][0]["snapshot"], first["snapshot"])
        self.assertFalse(older["entries"][0]["store_current"])
        result = subprocess.run(newest["entries"][0]["resume"]["argv"], capture_output=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        resumed = json.loads(result.stdout)
        self.assertEqual(resumed["snapshot"], second["snapshot"])
        self.assertEqual(resumed["bridge_snapshot"], "demo-task/revision-2")
        self.assertEqual(resumed["context"]["task"]["objective"], "完善自用 demo，按 TODO 持续实现")
        explanation = self.cli("explain", "--session", "host", "--snapshot", second["snapshot"])
        self.assertEqual(explanation["snapshot"], resumed["snapshot"])
        stale = self.run_cli("continue", "--session", "host", "--snapshot", first["snapshot"])
        self.assertEqual(json.loads(stale.stderr)["error"]["code"], "stale_snapshot")
        missing = self.run_cli("continue", "--session", "host")
        self.assertNotEqual(missing.returncode, 0)
        search = self.cli("search", "--session", "host", "--query", "当前导航要求")
        self.assertTrue(search["refs"])

    def test_navigation_is_task_bound_and_does_not_acquire_or_mutate_ownership(self):
        exported = self.export()
        scope = Scope(**self.initial["scope"])
        foreign = Scope(scope.tenant, scope.project, scope.branch, "another-task")
        with Store(self.initial["store"]) as store:
            lease = store.create_task(foreign, Target("host", "host", "external"), "other objective", allowed_providers=("host",))
            ref = store.put(foreign, lease, "foreign-secret", b"FOREIGN-SENSITIVE-9142", expected_revision=0)
            foreign_sid = store.snapshot(foreign, lease, (Selection(ref),))
            before = store.task(scope)
        self.assertEqual(self.cli("search", "--session", "host", "--query", "FOREIGN-SENSITIVE-9142")["refs"], [])
        self.assertEqual([item["snapshot"] for item in self.cli("checkpoints", "--session", "host")["entries"]], [exported["snapshot"]])
        invalid = self.run_cli("continue", "--session", "host", "--snapshot", foreign_sid)
        self.assertEqual(json.loads(invalid.stderr)["error"]["code"], "not_found")
        invalid = self.run_cli("checkpoints", "--session", "host", "--limit", "101")
        self.assertEqual(json.loads(invalid.stderr)["error"]["code"], "invalid_request")
        with Store(self.initial["store"]) as store:
            self.assertEqual(store.task(scope), before)


if __name__ == "__main__":
    unittest.main()
