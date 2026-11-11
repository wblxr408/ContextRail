"""I01 managed native entry through the existing Bridge, process and MCP adapters."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from contextrail import Lease, Scope, Store
from examples import native_client_acceptance as fixture


DRIVER = r'''
import asyncio,json,sys,tomllib
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
client=sys.argv[1]; data=json.loads(Path(sys.argv[2]).read_bytes()); args=sys.argv[3:]
prompt=sys.stdin.buffer.read().decode('utf-8')
assert data['snapshot'] in prompt and prompt.endswith('用户原话：继续工作🙂\r\n'), prompt
if client=='codex':
    override=args[args.index('-c')+1]
    server=tomllib.loads(override)['mcp_servers']['contextrail']
    print(json.dumps({'type':'thread.started','thread_id':'codex-native-fixture'}),flush=True)
else:
    server=json.loads(Path(args[args.index('--mcp-config')+1]).read_bytes())['mcpServers']['contextrail']
    print(json.dumps({'type':'system','subtype':'init','session_id':'claude-native-fixture'}),flush=True)
async def run():
    async with stdio_client(StdioServerParameters(command=server['command'],args=server['args'])) as (source,sink):
        async with ClientSession(source,sink) as session:
            await session.initialize()
            result=await session.call_tool('context.packet',{'snapshot':data['snapshot'],'budget':7000})
            packet=json.loads(result.content[0].text)
            result=await session.call_tool('context.get',{**data['cold_ref'],'start':data['start']})
            cold=json.loads(result.content[0].text)
            print(json.dumps({'snapshot':packet['snapshot'],'objective':packet['context']['task']['objective'],
                              'cold':cold['content']},ensure_ascii=False),flush=True)
asyncio.run(run())
print('native diagnostic',file=sys.stderr)
raise SystemExit(17 if client=='claude' else 0)
'''


class ManagedNativeContinuationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="native-stage-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "中文 path"
        self.data, self.expected = fixture.fixture(self.root)
        self.scope = Scope(**self.data["scope"])
        self.prompt = "用户原话：继续工作🙂\r\n"
        self.driver = self.root / "driver.py"
        self.driver.write_text(DRIVER, encoding="utf-8")
        self.registry = self.data["layout"]["registry"]

    def command(self, client):
        command = self.root / (client + "-argv.json")
        argv = [sys.executable, str(self.driver), client, str(self.root / "fixture.json")]
        if client == "claude" and sys.platform == "win32":
            shim = self.root / "Claude shim.cmd"
            shim.write_bytes(b'@echo off\r\n%*\r\n')
            argv = [str(shim), *argv]
        command.write_text(json.dumps(argv), encoding="utf-8")
        return str(command)

    def launch(self, client, *options, snapshot=None, timeout=30):
        argv = [sys.executable, "-m", "contextrail", "client-continue", "--workspace", self.data["workspace"],
                "--task", self.scope.task, "--session", "owner", "--registry", self.registry,
                "--snapshot", snapshot or self.data["snapshot"], "--client", client,
                "--client-command", self.command(client), "--timeout", str(timeout), *options]
        return subprocess.run(argv, input=self.prompt.encode("utf-8"), capture_output=True, timeout=timeout + 12)

    def receipts(self):
        with Store(self.data["store"]) as store:
            return [row["metadata"] for row in store.events(self.scope) if row["kind"] == "native_managed.finished"]

    def test_both_clients_read_same_exact_bridge_packet_and_keep_native_identity_and_exit(self):
        codex = self.launch("codex")
        self.assertEqual(codex.returncode, 0, codex.stderr.decode("utf-8", "replace"))
        claude = self.launch("claude", "--source-client", "codex", "--source-native-session", "codex-native-fixture")
        self.assertEqual(claude.returncode, 17, claude.stderr.decode("utf-8", "replace"))
        for run in (codex, claude):
            events = [json.loads(line) for line in run.stdout.splitlines()]
            result = events[-1]
            self.assertEqual(result, {"snapshot": self.data["snapshot"], "objective": self.expected["objective"],
                                      "cold": self.expected["cold_tail"]})
            self.assertIn(b"native diagnostic", run.stderr)
        first, second = self.receipts()
        self.assertEqual(first["native_session"], "codex-native-fixture")
        self.assertEqual(second["native_session"], "claude-native-fixture")
        self.assertEqual(second["native_exit_code"], 17)
        self.assertEqual(second["history"]["status"], "not_bound")
        self.assertEqual(second["history"]["native_session"], "codex-native-fixture")
        with Store(self.data["store"]) as store:
            self.assertEqual(store.task(self.scope)["owner"], "owner")

    def test_stale_or_unrelated_snapshot_is_rejected_before_starting_client(self):
        with Store(self.data["store"]) as store:
            unrelated = store.snapshot(self.scope, Lease("owner", 0), ())
        run = self.launch("codex", snapshot=unrelated)
        self.assertEqual(run.returncode, 1, run.stderr.decode("utf-8", "replace"))
        self.assertEqual(run.stdout, b"")
        self.assertIn(b"snapshot", run.stderr.lower())
        self.assertEqual(self.receipts(), [])
