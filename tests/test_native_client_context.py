"""Current native-client adapter contracts; actual CLI acceptance is recorded separately."""

import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
import uuid

from contextrail import Lease, Ref, Scope, Store, Target
from contextrail.bridge import BridgeEvidence
from contextrail.errors import AccessDenied


class NativeContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rail-native-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "中文 client's workspace"
        self.workspace.mkdir()
        (self.workspace / ".contextrail.toml").write_text('[project]\ntask="native-demo"\n', encoding="utf-8")
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.statement = "当前任务：自用 demo；保留要求与精确原文。"
        (self.bundle / "user.md").write_bytes((self.statement + "\r\n").encode())
        self.cold = ("冷页🙂\r\n" * 5000 + "NATIVE-COLD-9264\r\n").encode()
        (self.bundle / "large.log").write_bytes(self.cold)
        self.ledger = {"schema": 1, "task_id": "native-demo", "snapshot_id": "native-demo/rev-1", "requirements": [
            {"id": "R001", "revision": 1, "status": "active", "kind": "goal", "scope": "global", "always_load": True,
             "statement": self.statement, "source": {"path": "user.md", "quote": self.statement, "locator": "latest user"},
             "acceptance": "读回任务包和冷页", "progress": "pending", "evidence": []},
            {"id": "R002", "revision": 1, "status": "active", "kind": "deliverable", "scope": "implementation", "always_load": False,
             "statement": "提供大日志来源", "source": {"path": "user.md", "quote": self.statement, "locator": "user"},
             "acceptance": "保留末尾标记", "progress": "done", "evidence": ["large.log"]}]}
        (self.bundle / "requirements.json").write_text(json.dumps(self.ledger, ensure_ascii=False), encoding="utf-8")
        self.initial = self.cli("project-init", "--session", "owner", "--objective", "client adapter task")
        self.scope = Scope(**self.initial["scope"])
        with Store(self.initial["store"]) as store:
            self.exported = BridgeEvidence(store, self.scope, "owner").import_bundle(self.bundle, "implementation", Lease("owner", 0))
            manifest = json.loads(store.get(self.scope, Ref(**self.exported["manifest_ref"])).content)
            self.cold_ref = manifest["sources"]["large.log"]
        # A real subprocess command implementing the existing hook wire contract.
        # The live main-memory command is exercised in the separate client run.
        self.calls = self.root / "memory-calls.jsonl"
        self.memory_text = "有出处的历史资料🙂\r\n" * 3000
        self.memory_output = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": self.memory_text}}
        self.memory_script = self.root / "existing_hook.py"
        self.memory_script.write_text(
            "import json,sys\nfrom pathlib import Path\npayload=json.load(sys.stdin)\n"
            + "with Path(" + repr(str(self.calls)) + ").open('a',encoding='utf-8') as f: f.write(json.dumps(payload)+'\\n')\n"
            + "sys.stdout.buffer.write(json.dumps(" + repr(self.memory_output) + ",ensure_ascii=False).encode())\n", encoding="utf-8")
        self.memory_command = self.root / "memory-command.json"
        self.memory_command.write_text(json.dumps([sys.executable, str(self.memory_script)]), encoding="utf-8")

    def run_cli(self, command, *args, input=None):
        return subprocess.run([sys.executable, "-m", "contextrail", command, "--workspace", str(self.workspace),
                               *map(str, args)], input=input, capture_output=True, timeout=45)

    def cli(self, command, *args, input=None):
        result = self.run_cli(command, *args, input=input)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        return json.loads(result.stdout)

    def prepare(self):
        self.layout = self.cli("client-prepare", "--session", "owner", "--epoch", "0", "--snapshot", self.exported["snapshot"],
                               "--output", self.root / "client layout", "--memory-command", self.memory_command)
        self.registry_path = Path(self.layout["registry"])
        self.registry = json.loads(self.registry_path.read_bytes())
        return self.layout

    def hook(self, client="codex", *, cwd=None, turn="turn-1"):
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": client + "-native-session", "cwd": str(cwd or self.workspace),
                   "prompt": "读取任务包并找回日志末尾。", "transcript_path": "never-open-this-transcript"}
        if turn is not None:
            payload["turn_id"] = turn
        return self.cli("host-hook", "--client", client, "--registry", self.registry_path,
                        input=json.dumps(payload, ensure_ascii=False).encode())

    def test_client_profiles_expose_bound_packet_and_cold_page_over_real_stdio(self):
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            self.skipTest("Requires the optional MCP SDK extra.")
        layout = self.prepare()
        async def exercise(client):
            profile = layout["clients"][client]
            if client == "codex":
                server = tomllib.loads(Path(profile["mcp_config"]).read_text(encoding="utf-8"))["mcp_servers"]["contextrail"]
            else:
                server = json.loads(Path(profile["mcp_config"]).read_bytes())["mcpServers"]["contextrail"]
            params = StdioServerParameters(command=server["command"], args=server["args"], cwd=str(self.workspace))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as connection:
                    await connection.initialize()
                    packet = await connection.call_tool("context.packet", {"snapshot": self.exported["snapshot"], "budget": 6000})
                    self.assertFalse(packet.isError)
                    data = json.loads(packet.content[0].text)
                    self.assertEqual(data["context"]["task"]["objective"], self.statement)
                    tail = await connection.call_tool("context.get", {**self.cold_ref, "start": len(self.cold) - len(b"NATIVE-COLD-9264\r\n")})
                    self.assertFalse(tail.isError)
                    self.assertEqual(json.loads(tail.content[0].text)["content"], "NATIVE-COLD-9264\r\n")
                    denied = await connection.call_tool("context.packet", {"snapshot": self.exported["snapshot"], "task": "other"})
                    self.assertTrue(denied.isError)
        for client in ("codex", "claude"):
            asyncio.run(exercise(client))
        with Store(self.initial["store"]) as store:
            self.assertEqual(store.task(self.scope)["owner"], "owner")
            for binding in self.registry["bindings"]:
                with self.assertRaises(AccessDenied):
                    store.put(self.scope, Lease(binding["session"], 0), "not-permitted", b"write", expected_revision=0)

    def test_hook_invokes_memory_once_and_preserves_required_context_under_byte_limit(self):
        self.prepare()
        for binding in self.registry["bindings"]:
            binding["hook_bytes"] = 10000
            binding["context_bytes"] = 6000
        self.registry_path.write_text(json.dumps(self.registry), encoding="utf-8")
        result = self.hook()
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()), 10000)
        body = json.loads(result["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(body["task"]["context"]["task"]["objective"], self.statement)
        self.assertTrue(body["memory"]["truncated"])
        self.assertEqual(body["origin"]["turn_id"], "turn-1")
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

    def test_disabled_rail_preserves_the_original_memory_hook_output(self):
        self.prepare()
        self.cli("client-toggle", "--registry", self.registry_path, "--client", "codex", "--disabled")
        result = self.hook()
        self.assertEqual(result, self.memory_output)
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

    def test_claude_context_stays_inline_instead_of_native_file_preview(self):
        self.prepare()
        result = self.hook("claude")
        text = result["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(text.encode("utf-16-le")) // 2, 10000)
        body = json.loads(text)
        self.assertEqual(body["task"]["context"]["task"]["objective"], self.statement)
        self.assertEqual(body["origin"]["native_session"], "claude-native-session")
        self.assertTrue(body["memory"]["truncated"])

    def test_task_specific_memory_override_is_removed_when_rail_is_disabled(self):
        self.prepare()
        special = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "explicit task-only retrieval mode"}}
        self.registry["bindings"][0]["memory"] = {"command": [sys.executable, "-c", "print(" + repr(json.dumps(special)) + ")"], "timeout_seconds": 1}
        self.registry_path.write_text(json.dumps(self.registry), encoding="utf-8")
        enabled = json.loads(self.hook()["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(enabled["memory"]["text"], "explicit task-only retrieval mode")
        self.cli("client-toggle", "--registry", self.registry_path, "--client", "codex", "--disabled")
        self.assertEqual(self.hook(), self.memory_output)

    def test_runtime_refresh_reuses_same_base_version_and_stale_lease_cannot_write(self):
        self.prepare()
        with Store(self.initial["store"]) as store:
            store.put(self.scope, Lease("owner", 0), "new-tool-output", b"new evidence", expected_revision=0)
            before = store.task(self.scope)
        first = json.loads(self.hook()["hookSpecificOutput"]["additionalContext"])
        second = json.loads(self.hook("claude", turn=None)["hookSpecificOutput"]["additionalContext"])
        self.assertNotEqual(first["task"]["snapshot"], self.exported["snapshot"])
        self.assertEqual(first["task"]["snapshot"], second["task"]["snapshot"])
        self.assertIsNone(second["origin"]["turn_id"])
        with Store(self.initial["store"]) as store:
            self.assertEqual(store.task(self.scope), before)
        self.registry["bindings"][0]["owner"]["epoch"] = 99
        self.registry_path.write_text(json.dumps(self.registry), encoding="utf-8")
        blocked = self.hook()
        self.assertEqual(blocked["decision"], "block")

    def test_hook_source_change_blocks_task_instead_of_promoting_history(self):
        self.prepare()
        (self.bundle / "user.md").write_text("source changed", encoding="utf-8")
        result = self.hook()
        self.assertEqual(result["decision"], "block")
        self.assertNotIn("additionalContext", result.get("hookSpecificOutput", {}))

    def test_optional_memory_failure_or_timeout_keeps_the_complete_current_task(self):
        self.prepare()
        for command, timeout, expected in [
            ([sys.executable, "-c", "import sys; sys.exit(9)"], 1, "failed"),
            ([sys.executable, "-c", "import time; time.sleep(2)"], .02, "timed_out"),
        ]:
            self.registry["memory"] = {"command": command, "timeout_seconds": timeout}
            self.registry_path.write_text(json.dumps(self.registry), encoding="utf-8")
            result = self.hook()
            body = json.loads(result["hookSpecificOutput"]["additionalContext"])
            self.assertEqual(body["task"]["context"]["task"]["objective"], self.statement)
            self.assertEqual(body["memory"]["status"], expected)
            self.assertTrue(result["systemMessage"])

    def test_pending_handoff_blocks_refresh_and_preserves_ownership(self):
        self.prepare()
        with Store(self.initial["store"]) as store:
            store.prepare(self.scope, Lease("owner", 0), self.exported["snapshot"], Target("next", "host", "unknown"))
            before = store.task(self.scope)
        blocked = self.hook()
        self.assertEqual(blocked["decision"], "block")
        with Store(self.initial["store"]) as store:
            self.assertEqual(store.task(self.scope), before)

    def test_ambiguous_or_invalid_bindings_return_structured_rejections_before_memory(self):
        self.prepare()
        self.calls.write_text("", encoding="utf-8")
        original = json.loads(self.registry_path.read_bytes())
        self.registry["bindings"].append(dict(self.registry["bindings"][0]))
        self.registry_path.write_text(json.dumps(self.registry), encoding="utf-8")
        payload = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "native", "cwd": str(self.workspace), "prompt": "read"}).encode()
        result = self.run_cli("host-hook", "--client", "codex", "--registry", self.registry_path, input=payload)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "invalid_request")
        self.assertEqual(self.calls.read_text(encoding="utf-8"), "")
        original["bindings"][0]["scope"] = "invalid scope object"
        self.registry_path.write_text(json.dumps(original), encoding="utf-8")
        result = self.run_cli("host-hook", "--client", "codex", "--registry", self.registry_path, input=payload)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "invalid_request")
        self.assertEqual(self.calls.read_text(encoding="utf-8"), "")

    def test_existing_main_hook_can_use_explicit_text_mode_without_a_private_memory_service(self):
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": "rail-live-main-" + uuid.uuid4().hex,
                   "cwd": str(Path(__file__).resolve().parents[1]), "prompt": "ContextRail 自用 demo 要求"}
        result = self.cli("existing-memory-hook", "--home", Path.home() / ".codex/services/engineering-memory", "--mode", "text",
                          input=json.dumps(payload, ensure_ascii=False).encode())
        self.assertEqual(result["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertTrue(result["hookSpecificOutput"]["additionalContext"])

    def test_project_install_preserves_existing_servers_hooks_and_repeat_has_no_duplicates(self):
        self.prepare()
        codex = self.workspace / ".codex"
        claude = self.workspace / ".claude"
        codex.mkdir()
        claude.mkdir()
        original_toml = '# user comment\n[mcp_servers.user_owned]\ncommand="some-existing-command"\n'
        (codex / "config.toml").write_text(original_toml, encoding="utf-8")
        existing = {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "existing-user-hook"}]}]}}
        (codex / "hooks.json").write_text(json.dumps(existing), encoding="utf-8")
        (self.workspace / ".mcp.json").write_text(json.dumps({"mcpServers": {"user_owned": {"command": "old"}}}), encoding="utf-8")
        (claude / "settings.local.json").write_text(json.dumps({**existing, "env": {"USER_VALUE": "preserve-this"}}), encoding="utf-8")
        first = self.cli("client-apply", "--layout", Path(self.layout["entry"]).parent)
        self.assertEqual(len(first["applied"]), 4)
        second = self.cli("client-apply", "--layout", Path(self.layout["entry"]).parent)
        self.assertEqual(second["applied"], [])
        self.assertTrue((codex / "config.toml").read_text(encoding="utf-8").startswith(original_toml))
        self.assertEqual(tomllib.loads((codex / "config.toml").read_text(encoding="utf-8"))["mcp_servers"]["user_owned"]["command"], "some-existing-command")
        for path in (codex / "hooks.json", claude / "settings.local.json"):
            values = json.loads(path.read_bytes())
            commands = [hook["command"] for group in values["hooks"]["UserPromptSubmit"] for hook in group["hooks"]]
            self.assertEqual(commands[0], "existing-user-hook")
            self.assertEqual(len(commands), 2)
        self.assertEqual(json.loads((claude / "settings.local.json").read_bytes())["env"]["USER_VALUE"], "preserve-this")


if __name__ == "__main__":
    unittest.main()
