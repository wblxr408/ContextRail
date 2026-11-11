"""H04/H05 consumer contracts, verified together as one module."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tomllib
import unittest

from contextrail.client_config import change_clients
from contextrail.errors import Conflict
from tests import test_native_client_context as native


class ProjectConfigOwnershipStage(unittest.TestCase):
    def setUp(self):
        self.fixture = native.NativeContextTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.workspace
        self.defaults = self.fixture.root / "personal defaults.toml"
        self.defaults.write_text(
            "[context]\nresponse_bytes=12000\nread_bytes=55000\ncontext_bytes=11000\nhook_bytes=30000\n",
            encoding="utf-8",
        )

    def cli(self, command, *args):
        return self.fixture.cli(command, "--defaults", self.defaults, *args)

    def layout(self, name, *args, personal=True):
        cli = self.cli if personal else self.fixture.cli
        prepared = cli("client-prepare", "--session", "owner", "--epoch", "0", "--snapshot",
                            self.fixture.exported["snapshot"], "--output", self.fixture.root / name, *args)
        return prepared, Path(prepared["registry"]).parent

    def test_cli_override_is_per_call_and_does_not_change_neighboring_project(self):
        config = self.root / ".contextrail.toml"
        config.write_text('[project]\ntask="native-demo"\n[context]\nresponse_bytes=8500\n', encoding="utf-8")
        other = self.fixture.root / "other-project"
        other.mkdir()
        other_config = other / ".contextrail.toml"
        other_config.write_text("[context]\nread_bytes=65000\n", encoding="utf-8")
        before = self.cli("project-info")
        self.assertEqual((before["response_bytes"], before["read_bytes"]), (8500, 55000))
        self.assertEqual((before["sources"]["response_bytes"], before["sources"]["read_bytes"]),
                         ("config", "personal"))
        temporary = self.cli("project-info", "--response-bytes", "5400")
        self.assertEqual((temporary["response_bytes"], temporary["sources"]["response_bytes"]), (5400, "explicit"))
        unrelated = self.cli("project-info", "--workspace", other)
        self.assertEqual((unrelated["response_bytes"], unrelated["read_bytes"]), (12000, 65000))
        self.assertEqual(self.cli("project-info"), before)

    def test_bridge_packet_and_native_layout_share_the_effective_budget(self):
        initial = self.cli("bridge-packet", "--session", "owner", "--snapshot", self.fixture.exported["snapshot"])
        temporary = self.cli("bridge-packet", "--session", "owner", "--snapshot", self.fixture.exported["snapshot"],
                             "--budget", "9600")
        self.assertEqual((initial["budget"], temporary["budget"]), (11000, 9600))
        prepared, layout = self.layout("bounded layout", "--context-bytes", "9500")
        registry = json.loads((layout / "registry.json").read_bytes())
        for binding in registry["bindings"]:
            self.assertEqual((binding["context_bytes"], binding["hook_bytes"]), (9500, 30000))
        self.assertEqual(prepared["configuration"]["sources"]["context_bytes"], "explicit")
        self.assertEqual(prepared["configuration"]["sources"]["hook_bytes"], "personal")
        self.assertEqual(self.cli("project-info")["context_bytes"], 11000)

    def test_project_override_is_visible_in_task_status(self):
        status = self.cli("status", "--session", "owner", "--response-bytes", "7600")
        resolved = status["effective_config"]["project"]
        self.assertEqual((resolved["response_bytes"], resolved["context_bytes"]), (7600, 11000))
        self.assertEqual(resolved["sources"]["response_bytes"], "explicit")
        self.assertLessEqual(len(json.dumps(status, ensure_ascii=False, separators=(",", ":")).encode()), 7600)

    def test_invalid_personal_identity_cannot_redirect_project_storage(self):
        self.defaults.write_text('[storage]\npath="elsewhere.sqlite"\n', encoding="utf-8")
        result = self.fixture.run_cli("project-info", "--defaults", self.defaults)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "invalid_request")

    def test_reapply_upgrade_and_remove_preserve_both_client_hook_order(self):
        _, original = self.layout("original layout", personal=False)
        _, upgraded = self.layout("upgraded layout", personal=False)
        codex = self.root / ".codex"
        codex.mkdir()
        original_toml = b'# my Windows comment\r\n[mcp_servers.local_search]\r\ncommand="keep-search"\r\n'
        (codex / "config.toml").write_bytes(original_toml)
        hook_paths = (codex / "hooks.json", self.root / ".claude/settings.local.json")
        memory_argv = [sys.executable, str(self.fixture.memory_script)]
        memory_command = subprocess.list2cmdline(memory_argv) if os.name == "nt" else shlex.join(memory_argv)
        first = {"hooks": [{"type": "command", "command": memory_command}]}
        later = {"hooks": [{"type": "command", "command": "user-plugin-added-later"}]}
        for path in hook_paths:
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps({"hooks": {"UserPromptSubmit": [first]}, "user_setting": True}),
                            encoding="utf-8")
        self.assertTrue(self.fixture.cli("client-apply", "--layout", original)["applied"])
        for path in hook_paths:
            value = json.loads(path.read_bytes())
            value["hooks"]["UserPromptSubmit"].append(later)
            path.write_text(json.dumps(value, indent=4), encoding="utf-8")
        original_groups = [json.loads(path.read_bytes())["hooks"]["UserPromptSubmit"] for path in hook_paths]
        self.assertEqual(self.fixture.cli("client-apply", "--layout", original)["applied"], [])
        self.assertEqual([json.loads(path.read_bytes())["hooks"]["UserPromptSubmit"] for path in hook_paths], original_groups)
        changed = self.fixture.cli("client-apply", "--layout", upgraded)
        self.assertTrue(changed["applied"])
        for path, before in zip(hook_paths, original_groups):
            value = json.loads(path.read_bytes())
            groups = value["hooks"]["UserPromptSubmit"]
            self.assertEqual((groups[0], groups[2], value["user_setting"]), (first, later, True))
            self.assertNotEqual(groups[1], before[1])
        self.fixture.cli("client-remove")
        for path in hook_paths:
            value = json.loads(path.read_bytes())
            self.assertEqual(value, {"hooks": {"UserPromptSubmit": [first, later]}, "user_setting": True})
            retained = value["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
            completed = subprocess.run(retained, shell=True, input=b"{}", capture_output=True, timeout=15)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout), self.fixture.memory_output)
        self.assertEqual((codex / "config.toml").read_bytes(), original_toml)
        self.assertEqual(tomllib.loads(original_toml.decode())["mcp_servers"]["local_search"]["command"], "keep-search")

    def test_unowned_null_server_is_preserved_and_the_other_client_is_not_changed(self):
        _, layout = self.layout("ownership boundary", personal=False)
        mcp = self.root / ".mcp.json"
        mcp.write_text('{"mcpServers":{"contextrail":null,"existing":{"command":"keep"}}}', encoding="utf-8")
        codex = self.root / ".codex"
        codex.mkdir()
        codex_file = codex / "config.toml"
        original = b'# keep the user file\r\n'
        codex_file.write_bytes(original)
        with self.assertRaises(Conflict):
            change_clients(self.root, ("codex", "claude"), layout=layout)
        self.assertEqual(codex_file.read_bytes(), original)
        self.assertEqual(json.loads(mcp.read_bytes())["mcpServers"]["contextrail"], None)
