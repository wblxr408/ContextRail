"""I03 transfers one task publication and restores exact evidence in a new workspace."""

from dataclasses import asdict
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from contextrail import Lease, Ref, Store, Target
from contextrail.bridge import BridgeEvidence
from contextrail.project import load_project
from examples import native_client_acceptance


class PortableTaskBundleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="rail-portable-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data, self.expected = native_client_acceptance.fixture(self.root / "原机器")
        self.source = load_project(self.data["workspace"])
        work_file = self.source.workspace / "src/下一步.py"
        work_file.parent.mkdir()
        work_file.write_text("continue_here = True\n", encoding="utf-8")
        self.receiver_path = self.root / "另一机器的工作区"
        self.receiver_path.mkdir()
        self.receiver = load_project(self.receiver_path, overrides={"task": self.source.scope.task})
        self.receiver.store.parent.mkdir()
        with Store(self.receiver.store) as store:
            store.create_task(self.receiver.scope, Target("receiver-owner", "host", "external"), "receiver draft",
                              allowed_providers=("host",))
        self.archive = self.root / "任务包.zip"
        self.evidence = "额外证据🙂\r\n".encode("utf-8")
        with Store(self.source.store) as store:
            lease = Lease("owner", 0)
            self.ref = store.put(self.source.scope, lease, "task/evidence", self.evidence, expected_revision=0)

    def command(self, command, config, owner, *args, success=True):
        result = subprocess.run([sys.executable, "-m", "contextrail", command, "--workspace", str(config.workspace),
                                 "--task", config.scope.task, "--session", owner, *args],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0 if success else 1, result.stderr.decode("utf-8", "replace"))
        return json.loads(result.stdout if success else result.stderr)

    def export(self, *, ref=None, success=True):
        return self.command("portable-export", self.source, "owner", "--snapshot", self.data["snapshot"],
                            "--archive", str(self.archive), "--evidence", json.dumps(asdict(ref or self.ref)),
                            "--require-path", "src/下一步.py", "--knowledge-link", "memory://main/geometry/notes",
                            success=success)

    def receive(self, *, success=True):
        return self.command("portable-import", self.receiver, "receiver-owner", "--epoch", "0",
                            "--archive", str(self.archive), success=success)

    def test_new_workspace_keeps_checkpoint_and_exact_materials_and_reports_dependencies(self):
        exported = self.export()
        self.assertEqual(exported["bridge_snapshot"], "client-acceptance/rev-1")
        self.assertEqual(exported["dependencies"], [{"path": "src/下一步.py", "status": "present"}])
        # Model the old machine going offline after handing the archive over.
        shutil.move(self.data["workspace"], self.root / "原工作区已离线")
        received = self.receive()
        self.assertEqual(received["bridge_snapshot"], exported["bridge_snapshot"])
        self.assertEqual(received["source_snapshot"], self.data["snapshot"])
        self.assertEqual(received["dependencies"], [{"path": "src/下一步.py", "status": "missing"}])
        self.assertEqual(received["knowledge_links"], ["memory://main/geometry/notes"])
        self.assertEqual(received["snapshot"], self.receive()["snapshot"])
        with Store(self.receiver.store) as store:
            task = store.task(self.receiver.scope)
            self.assertEqual(task["owner"], "receiver-owner")
            bridge = BridgeEvidence(store, self.receiver.scope, "receiver-owner")
            packet = bridge.packet(received["snapshot"], budget=7000)
            self.assertEqual(packet["context"]["task"]["objective"], self.expected["objective"])
            manifest = json.loads(store.get(self.receiver.scope, Ref(**packet["manifest_ref"])).content)
            cold = Ref(**{**manifest["sources"]["cold.log"], "start": self.data["start"]})
            self.assertEqual(store.get(self.receiver.scope, cold).content, self.expected["cold_tail"].encode())
            material = received["materials"][0]
            self.assertEqual(material["source_ref"], asdict(self.ref))
            self.assertEqual(store.get(self.receiver.scope, Ref(**material["ref"])).content, self.evidence)

    def test_export_refuses_credential_material_and_import_rejects_changed_archive(self):
        with Store(self.source.store) as store:
            blocked = store.put(self.source.scope, Lease("owner", 0), "task/private",
                                b"API_KEY=mock-secret-placeholder-123456789\n", expected_revision=0)
        self.assertEqual(self.export(ref=blocked, success=False)["error"]["code"], "access_denied")
        self.export()
        original = self.root / "original.zip"
        self.archive.rename(original)
        with zipfile.ZipFile(original) as source, zipfile.ZipFile(self.archive, "w") as target:
            for name in source.namelist():
                content = source.read(name)
                target.writestr(name, b"changed evidence" if name == "evidence/0" else content)
        self.assertEqual(self.receive(success=False)["error"]["code"], "integrity_error")
        with Store(self.receiver.store) as store:
            self.assertEqual(store.task(self.receiver.scope)["objective"], "receiver draft")
