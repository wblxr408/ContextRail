"""Windows behavior at the two native profiles and local worker boundary."""

import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import tomllib
import unittest

from contextrail.errors import Conflict
from contextrail import Store
from contextrail.native_clients import toggle_client
from contextrail.process_identity import cancel_tree, inspect_process
from tests import test_native_client_context as native


@unittest.skipUnless(os.name == "nt", "Windows native process and filesystem behavior")
class WindowsNativeTransport(unittest.TestCase):
    def setUp(self):
        self.fixture = native.NativeContextTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        self.shell = Path(os.environ["SystemRoot"]) / "System32/cmd.exe"

    def powershell_cli(self, script, **environment):
        return subprocess.run(
            [str(self.powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "RAIL_TEST_PYTHON": sys.executable, **environment},
            capture_output=True, timeout=25, creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def junction(self):
        link = self.fixture.root / "中文 工作区别名"
        subprocess.run([str(self.shell), "/d", "/c", "mklink", "/J", str(link), str(self.fixture.workspace)],
                       check=True, capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(link.rmdir)
        return link

    def test_powershell_written_toml_uses_the_same_task_through_junction(self):
        link = self.junction()
        result = self.powershell_cli(
            "Set-Content -LiteralPath (Join-Path $env:RAIL_TEST_WORKSPACE '.contextrail.toml') "
            "-Value ('[project]' + [Environment]::NewLine + 'task=\"native-demo\"') -Encoding UTF8; "
            "Set-Content -LiteralPath $env:RAIL_TEST_DEFAULTS "
            "-Value ('[context]' + [Environment]::NewLine + 'context_bytes=9000') -Encoding UTF8; "
            "& $env:RAIL_TEST_PYTHON -m contextrail project-info --workspace $env:RAIL_TEST_WORKSPACE "
            "--defaults $env:RAIL_TEST_DEFAULTS; exit $LASTEXITCODE",
            RAIL_TEST_WORKSPACE=str(link), RAIL_TEST_DEFAULTS=str(self.fixture.root / "个人 默认.toml"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["scope"], self.fixture.initial["scope"])
        self.assertEqual(data["context_bytes"], 9000)
        self.assertEqual(data["store"], self.fixture.initial["store"])

    def test_both_native_profiles_read_the_powershell_write_and_batch_memory(self):
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            self.skipTest("Requires the optional MCP SDK extra.")
        runner = self.fixture.root / "中文 记忆 & 命令.cmd"
        script = self.fixture.root / "中文 工作进程.py"
        script.write_text(
            "import ctypes, json, sys\n"
            "payload = json.load(sys.stdin)\n"
            "assert not ctypes.windll.kernel32.GetConsoleWindow()\n"
            "sys.stderr.buffer.write('记忆诊断\\r\\n'.encode())\n"
            "data = {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit',"
            " 'additionalContext': sys.argv[1] + '\\r\\n' + payload['prompt']}}\n"
            "sys.stdout.buffer.write(json.dumps(data, ensure_ascii=False).encode())\n",
            encoding="utf-8")
        runner.write_bytes(b'@echo off\r\n"%RAIL_TEST_PYTHON%" "%RAIL_TEST_WORKER%" %*\r\n')
        argument = "中文 & 字面量 %RAIL_TEST_SENTINEL% !"
        self.fixture.memory_command.write_text(json.dumps([str(runner), argument]), encoding="utf-8")
        layout = self.fixture.prepare()
        link = self.junction()
        applied = self.powershell_cli(
            "& $env:RAIL_TEST_PYTHON -m contextrail client-apply --workspace $env:RAIL_TEST_WORKSPACE "
            "--layout $env:RAIL_TEST_LAYOUT; exit $LASTEXITCODE",
            RAIL_TEST_WORKSPACE=str(link), RAIL_TEST_LAYOUT=str(Path(layout["registry"]).parent),
        )
        self.assertEqual(applied.returncode, 0, applied.stderr)
        captured = self.fixture.root / "工具 结果.json"
        source = "新写入的工具证据🙂\r\n"
        captured.write_bytes(json.dumps({
            "call_id": "pwsh-output", "tool": "shell", "argv": ["echo"], "cwd": str(link),
            "started_at": 1, "finished_at": 2, "exit_code": 0, "stdout": source, "stderr": "",
        }, ensure_ascii=False).encode())
        write = self.powershell_cli(
            "& $env:RAIL_TEST_PYTHON -m contextrail capture-result --workspace $env:RAIL_TEST_WORKSPACE "
            "--session owner --epoch 0 --result $env:RAIL_TEST_RESULT; exit $LASTEXITCODE",
            RAIL_TEST_WORKSPACE=str(link), RAIL_TEST_RESULT=str(captured))
        self.assertEqual(write.returncode, 0, write.stderr)
        ref = json.loads(write.stdout)["stdout"]["ref"]

        async def read(client, snapshot):
            if client == "codex":
                installed = (link / ".codex/config.toml").read_text(encoding="utf-8")
                server = tomllib.loads(installed)["mcp_servers"]["contextrail"]
            else:
                server = json.loads((link / ".mcp.json").read_bytes())["mcpServers"]["contextrail"]
            params = StdioServerParameters(command=server["command"], args=server["args"], cwd=str(link))
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as connection:
                    await connection.initialize()
                    packet = await connection.call_tool("context.packet", {"snapshot": snapshot})
                    self.assertFalse(packet.isError)
                    body = json.loads(packet.content[0].text)
                    self.assertEqual(body["context"]["task"]["objective"], self.fixture.statement)
                    raw = await connection.call_tool("context.get", ref)
                    self.assertFalse(raw.isError)
                    self.assertEqual(json.loads(raw.content[0].text)["content"], source)

        for client in ("codex", "claude"):
            payload = {"hook_event_name": "UserPromptSubmit", "session_id": client + "-windows",
                       "cwd": str(link), "prompt": "PowerShell 中文回读"}
            path = ".codex/hooks.json" if client == "codex" else ".claude/settings.local.json"
            config = json.loads((link / path).read_bytes())
            command = config["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
            hook = subprocess.run(
                f'"{self.shell}" /d /v:off /s /c "{command}"', executable=str(self.shell),
                input=json.dumps(payload, ensure_ascii=False).encode(), cwd=link,
                env={**os.environ, "RAIL_TEST_PYTHON": sys.executable, "RAIL_TEST_WORKER": str(script),
                     "RAIL_TEST_SENTINEL": "must-not-expand"},
                capture_output=True, timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(hook.returncode, 0, hook.stderr)
            self.assertIn("记忆诊断".encode(), hook.stderr)
            body = json.loads(json.loads(hook.stdout)["hookSpecificOutput"]["additionalContext"])
            self.assertEqual(body["memory"]["status"], "returned")
            self.assertEqual(body["memory"]["text"], argument + "\r\n" + payload["prompt"])
            asyncio.run(read(client, body["task"]["snapshot"]))
        with Store(self.fixture.initial["store"]) as store:
            prepared = {item["metadata"]["client"] for item in store.events(self.fixture.scope)
                        if item["kind"] == "native_context.prepared"}
            self.assertEqual(prepared, {"codex", "claude"})

    def worker_with_child(self, *, delay):
        marker = self.fixture.root / f"owned-process-{delay}.json"
        script = self.fixture.root / "留下的 worker.py"
        script.write_text(
            "import json, subprocess, sys, time\n"
            "from pathlib import Path\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(45)'], "
            "stdin=subprocess.DEVNULL, stdout=1, stderr=2)\n"
            "Path(sys.argv[1]).write_text(json.dumps(child.pid), encoding='utf-8')\n"
            "time.sleep(float(sys.argv[2]))\n"
            "sys.stdout.buffer.write(b'{}')\n",
            encoding="utf-8")
        self.fixture.registry["memory"] = {
            "command": [sys.executable, str(script), str(marker), str(delay)], "timeout_seconds": 2}
        self.fixture.registry_path.write_text(json.dumps(self.fixture.registry), encoding="utf-8")
        body = json.loads(self.fixture.hook()["hookSpecificOutput"]["additionalContext"])
        child = inspect_process(json.loads(marker.read_bytes()))
        if child.get("state") == "running":
            self.addCleanup(cancel_tree, child)
        self.assertIn(child["state"], {"absent", "stopped"})
        return body

    def test_memory_cleanup_cancels_descendants_on_completion_and_timeout(self):
        self.fixture.prepare()
        self.assertEqual(self.worker_with_child(delay=0)["memory"]["status"], "returned")
        timeout = self.worker_with_child(delay=45)
        self.assertEqual(timeout["memory"]["status"], "timed_out")
        self.assertEqual(timeout["task"]["context"]["task"]["objective"], self.fixture.statement)

    def test_memory_deadline_applies_when_the_worker_does_not_read_stdin(self):
        self.fixture.prepare()
        self.fixture.registry["memory"] = {
            "command": [sys.executable, "-c", "import time; time.sleep(45)"], "timeout_seconds": 1}
        self.fixture.registry_path.write_text(json.dumps(self.fixture.registry), encoding="utf-8")
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": "blocked-input", "prompt": "输" * 80000,
                   "cwd": str(self.fixture.workspace)}
        result = subprocess.run(
            [sys.executable, "-m", "contextrail", "host-hook", "--registry", str(self.fixture.registry_path),
             "--client", "codex"],
            input=json.dumps(payload, ensure_ascii=False).encode(), capture_output=True, timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(body["memory"]["status"], "timed_out")
        self.assertEqual(body["task"]["context"]["task"]["objective"], self.fixture.statement)

    @contextmanager
    def hold_registry(self):
        import ctypes
        from ctypes import wintypes

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                    ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        api.CreateFileW.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.CreateFileW(str(self.fixture.registry_path), 0x80000000, 3, None, 3, 0, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        lock = threading.Lock()

        def release():
            nonlocal handle
            with lock:
                if handle is not None:
                    api.CloseHandle(handle)
                    handle = None

        try:
            yield release
        finally:
            release()

    def test_transient_registry_lock_retries_and_persistent_lock_preserves_binding(self):
        self.fixture.prepare()
        with self.hold_registry() as release:
            timer = threading.Timer(0.2, release)
            timer.start()
            try:
                changed = toggle_client(self.fixture.registry_path, "codex", False)
            finally:
                timer.cancel()
                timer.join()
        self.assertFalse(changed["enabled"])
        with self.hold_registry():
            with self.assertRaises(Conflict):
                toggle_client(self.fixture.registry_path, "codex", True)
        binding = json.loads(self.fixture.registry_path.read_bytes())["bindings"][0]
        self.assertFalse(binding["enabled"])
