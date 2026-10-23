"""Consumer contracts for the eight selected self-use TODO items only."""

import asyncio
import base64
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from contextrail import ContextTools, Lease, Ref, Scope, Store, Target
from contextrail.errors import Conflict, NotFound


class OutputLoopTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="rail-self-use-")
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name) / "中文 project"
        self.root.mkdir()
        self.marker = self.root / ".contextrail.toml"
        self.marker.write_text('[project]\ntask="repair"\n[context]\nresponse_bytes=1536\n', encoding="utf-8")

    def cli(self, command, *args):
        result = subprocess.run([sys.executable, "-m", "contextrail", command,
                                 "--workspace", str(self.root), *map(str, args)],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        return json.loads(result.stdout), result.stdout.rstrip(b"\r\n")

    def initialize(self):
        return self.cli("project-init", "--session", "host-session", "--objective", "Find the final error")[0]

    def result_input(self, call="build-1", stdout=None, stderr="末尾异常\r\n", **extra):
        data = dict(call_id=call, tool="shell", argv=["python", "build.py"], cwd=str(self.root),
                    started_at=1000.0, finished_at=1001.0, exit_code=1,
                    stdout=stdout if stdout is not None else ("中文🙂\r\n" * 4000 + "TAIL-7429\r\n"),
                    stderr=stderr)
        data.update(extra)
        path = self.root / "observed-result.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path, data

    def test_marker_relative_paths_and_temporary_override_select_the_right_task(self):
        self.marker.write_text('[project]\nid="personal"\ntask="repair"\n'
                               '[storage]\npath="state/evidence.sqlite3"\n'
                               '[capture]\nignore=["private/**"]\n'
                               '[context]\nrequired=["contract.md"]\nresponse_bytes=1536\n', encoding="utf-8")
        child = self.root / "sub"
        child.mkdir()
        result = subprocess.run([sys.executable, "-m", "contextrail", "project-info", "--workspace", str(child),
                                 "--task", "temporary"], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        output = json.loads(result.stdout)
        self.assertEqual(output["scope"]["task"], "temporary")
        self.assertEqual(output["scope"]["project"], "personal")
        self.assertEqual(Path(output["workspace"]), self.root.resolve())
        self.assertEqual(Path(output["store"]), (self.root / "state/evidence.sqlite3").resolve())
        self.assertEqual(output["required"], ["contract.md"])
        self.assertEqual(output["capture"]["ignore"], ["private/**"])
        self.assertEqual(output["sources"]["task"], "explicit")
        original, _ = self.cli("project-info")
        self.assertEqual(original["scope"]["task"], "repair")

    def test_same_directory_names_and_linked_worktrees_do_not_mix_task_scopes(self):
        other = Path(self.folder.name) / "other" / self.root.name
        other.mkdir(parents=True)
        first, _ = self.cli("project-info")
        second = subprocess.run([sys.executable, "-m", "contextrail", "project-info", "--workspace", str(other)],
                                capture_output=True, timeout=30)
        self.assertEqual(second.returncode, 0, second.stderr.decode("utf-8", "replace"))
        self.assertNotEqual(first["scope"]["project"], json.loads(second.stdout)["scope"]["project"])
        def git(*args):
            return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                                  capture_output=True, timeout=30)
        git("init", "-q")
        git("-c", "user.name=Self use", "-c", "user.email=self@example.invalid", "commit", "--allow-empty", "-qm", "fixture")
        worktree = Path(self.folder.name) / "linked"
        git("worktree", "add", "-qb", "second", str(worktree))
        first, _ = self.cli("project-info")
        second = subprocess.run([sys.executable, "-m", "contextrail", "project-info", "--workspace", str(worktree),
                                 "--task", "repair"], capture_output=True, timeout=30)
        self.assertEqual(second.returncode, 0, second.stderr.decode("utf-8", "replace"))
        linked = json.loads(second.stdout)
        self.assertEqual(first["scope"]["project"], linked["scope"]["project"])
        self.assertNotEqual(first["scope"]["branch"], linked["scope"]["branch"])

    @unittest.skipUnless(os.name == "nt", "Windows path identity")
    def test_windows_case_and_junction_share_identity_but_keep_display_path(self):
        expected, _ = self.cli("project-info")
        junction = Path(self.folder.name) / "alias"
        subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(self.root)],
                       check=True, capture_output=True, timeout=30)
        for path in (str(junction), str(self.root).swapcase()):
            result = subprocess.run([sys.executable, "-m", "contextrail", "project-info", "--workspace", path],
                                    capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
            actual = json.loads(result.stdout)
            self.assertEqual(actual["scope"], expected["scope"])
            self.assertEqual(actual["identity"]["requested_workspace"], path)

    def test_large_result_remains_exact_after_reopen_and_display_is_bounded(self):
        initialized = self.initialize()
        path, data = self.result_input()
        output, wire = self.cli("capture-result", "--session", "host-session", "--epoch", "0", "--result", path)
        self.assertLessEqual(len(wire), 1536)
        self.assertEqual(output["status"], "failed")
        self.assertEqual(output["exit_code"], 1)
        self.assertTrue(output["stdout"]["truncated"])
        with Store(initialized["store"]) as store:
            scope = Scope(**initialized["scope"])
            self.assertEqual(store.get(scope, Ref(**output["stdout"]["ref"])).content, data["stdout"].encode())
            self.assertEqual(store.get(scope, Ref(**output["stderr"]["ref"])).content, data["stderr"].encode())
            ref = Ref(**output["stdout"]["ref"])
            original = data["stdout"].encode()
            tail = Ref(ref.name, ref.revision, len(original) - len("TAIL-7429\r\n"), None)
            self.assertEqual(ContextTools(store, scope, "host-session").call("context.get", asdict(tail))["content"], "TAIL-7429\r\n")
            before = store.task(scope)["version"]
        retried, _ = self.cli("capture-result", "--session", "host-session", "--epoch", "0", "--result", path)
        self.assertEqual(retried["result_ref"], output["result_ref"])
        with Store(initialized["store"]) as store:
            self.assertEqual(store.task(scope)["version"], before)
        path, _ = self.result_input(stdout="different")
        conflict = subprocess.run([sys.executable, "-m", "contextrail", "capture-result", "--workspace", str(self.root),
                                   "--session", "host-session", "--epoch", "0", "--result", str(path)], capture_output=True, timeout=30)
        self.assertNotEqual(conflict.returncode, 0)
        self.assertEqual(json.loads(conflict.stderr)["error"]["code"], "conflict")

    def test_binary_result_can_be_recovered_and_no_match_does_not_hide_failures(self):
        initialized = self.initialize()
        for call, tool, argv, rc, stdout, stderr, extra, expected in [
            ("nomatch", "shell", ["rg", "absent", "."], 1, "", "", {}, "no_match"),
            ("grepnomatch", "shell", ["grep", "absent", "file"], 1, "", "", {}, "no_match"),
            ("wrapped", "shell", ["sh", "-c", "rg absent ."], 1, "", "", {}, "failed"),
            ("build", "shell", ["python", "build.py"], 1, "some ordinary output", "error", {}, "failed"),
            ("rgerror", "shell", ["rg", "x"], 1, "", "denied", {}, "failed"),
            ("timeout", "shell", ["python", "job.py"], None, "partial", "", {"timed_out": True}, "timed_out"),
            ("unknown", "external", [], None, "partial", "", {}, "unknown"),
            ("partial", "external", [], 0, "partial", "", {"partial": True}, "partial"),
            ("ok", "external", [], 0, "done", "", {}, "succeeded"),
        ]:
            path, _ = self.result_input(call, stdout, stderr, tool=tool, argv=argv, exit_code=rc, **extra)
            output, _ = self.cli("capture-result", "--session", "host-session", "--epoch", "0", "--result", path)
            self.assertEqual(output["status"], expected)
            self.assertEqual(output["exit_code"], rc)
        path, data = self.result_input("binary", stdout="")
        data.pop("stdout")
        raw = bytes(range(256)) * 40
        data["stdout_base64"] = base64.b64encode(raw).decode()
        path.write_text(json.dumps(data), encoding="utf-8")
        output, _ = self.cli("capture-result", "--session", "host-session", "--epoch", "0", "--result", path)
        self.assertEqual(output["stdout"]["encoding"], "binary")
        with Store(initialized["store"]) as store:
            self.assertEqual(store.get(Scope(**initialized["scope"]), Ref(**output["stdout"]["ref"])).content, raw)

    def test_invalid_configuration_and_out_of_scope_capture_are_rejected(self):
        self.marker.write_text('[context]\nresponse_bytes=-1\n', encoding="utf-8")
        invalid = subprocess.run([sys.executable, "-m", "contextrail", "project-info", "--workspace", str(self.root)], capture_output=True, timeout=30)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertEqual(json.loads(invalid.stderr)["error"]["code"], "invalid_request")

    def test_capture_rejects_outside_cwd_and_tiny_budget_without_partial_evidence(self):
        initialized = self.initialize()
        path, _ = self.result_input(cwd=str(self.root.parent))
        rejected = subprocess.run([sys.executable, "-m", "contextrail", "capture-result", "--workspace", str(self.root),
                                   "--session", "host-session", "--epoch", "0", "--result", str(path)], capture_output=True, timeout=30)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(json.loads(rejected.stderr)["error"]["code"], "access_denied")
        path, _ = self.result_input()
        rejected = subprocess.run([sys.executable, "-m", "contextrail", "capture-result", "--workspace", str(self.root),
                                   "--session", "host-session", "--epoch", "0", "--response-bytes", "32",
                                   "--result", str(path)], capture_output=True, timeout=30)
        self.assertEqual(json.loads(rejected.stderr)["error"]["code"], "budget_exceeded")
        with Store(initialized["store"]) as store:
            scope = Scope(**initialized["scope"])
            self.assertEqual(store.task(scope)["version"], 0)
            with self.assertRaises(NotFound):
                store.get(scope, Ref("tool-result/build-1/stdout", 1))

    def test_atomic_result_batch_rolls_back_on_conflicting_stream(self):
        initialized = self.initialize()
        with Store(initialized["store"]) as store:
            scope = Scope(**initialized["scope"])
            lease = Lease(**initialized["lease"])
            existing = store.put(scope, lease, "tool-result/build-1/stderr", b"occupied", expected_revision=0)
            before = store.task(scope)["version"]
            from contextrail.tool_results import OutputRecorder
            _, data = self.result_input()
            with self.assertRaises(Conflict):
                OutputRecorder(store, scope, lease, self.root).capture(data, budget=1536)
            self.assertEqual(store.get(scope, existing).content, b"occupied")
            self.assertEqual(store.task(scope)["version"], before)
            for suffix in ("stdout", "result"):
                with self.assertRaises(NotFound):
                    store.get(scope, Ref("tool-result/build-1/" + suffix, 1))

    def test_project_capture_respects_declared_ignored_sources(self):
        self.marker.write_text('[project]\ntask="repair"\n[capture]\nignore=["private/**"]\n', encoding="utf-8")
        initialized = self.initialize()
        path, _ = self.result_input(stdout="sensitive", source_paths=["private/credentials.txt"])
        rejected = subprocess.run([sys.executable, "-m", "contextrail", "capture-result", "--workspace", str(self.root),
                                   "--session", "host-session", "--epoch", "0", "--result", str(path)], capture_output=True, timeout=30)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(json.loads(rejected.stderr)["error"]["code"], "access_denied")
        with Store(initialized["store"]) as store:
            self.assertEqual(store.task(Scope(**initialized["scope"]))["version"], 0)

    def test_unrepresentable_timestamps_are_rejected_without_writing_evidence(self):
        initialized = self.initialize()
        path, _ = self.result_input(started_at=10**400, finished_at=10**401)
        result = subprocess.run([sys.executable, "-m", "contextrail", "capture-result", "--workspace", str(self.root),
                                 "--session", "host-session", "--epoch", "0", "--result", str(path)],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        try:
            error = json.loads(result.stderr)
        except ValueError:
            self.fail("Invalid timestamp must return a structured rejection, not an uncaught exception.")
        self.assertEqual(error["error"]["code"], "invalid_request")
        with Store(initialized["store"]) as store:
            self.assertEqual(store.task(Scope(**initialized["scope"]))["version"], 0)

    def test_mcp_client_reads_revisions_pages_and_bound_scope_with_unknown_host_controls(self):
        from contextrail import Selection
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            self.skipTest("Install the optional contextrail[mcp] extra for the real MCP client contract.")
        initialized = self.initialize()
        scope, lease = Scope(**initialized["scope"]), Lease(**initialized["lease"])
        program = ('import sys; sys.stdout.buffer.write(("中文🙂\\r\\n"*4000+"TAIL-live-7429\\r\\n").encode()); '
                   'sys.stderr.buffer.write("真实失败\\r\\n".encode()); sys.exit(1)')
        began = time.time()
        observed = subprocess.run([sys.executable, "-c", program], cwd=self.root, capture_output=True, timeout=30)
        path, data = self.result_input("live-1", stdout="", stderr="", argv=[sys.executable, "-c", program],
                                       started_at=began, finished_at=time.time(), exit_code=observed.returncode)
        data.pop("stdout")
        data.pop("stderr")
        data["stdout_base64"] = base64.b64encode(observed.stdout).decode()
        data["stderr_base64"] = base64.b64encode(observed.stderr).decode()
        path.write_text(json.dumps(data), encoding="utf-8")
        captured, wire = self.cli("capture-result", "--session", "host-session", "--epoch", "0", "--result", path)
        self.assertEqual(captured["status"], "failed")
        self.assertLessEqual(len(wire), 1536)
        with Store(initialized["store"]) as store:
            original = store.put(scope, lease, "proof", "原始\r\n🙂".encode(), expected_revision=0)
            updated = store.put(scope, lease, "proof", b"updated", expected_revision=1)
            second = store.put(scope, lease, "second", b"second artifact", expected_revision=0)
            snapshot = store.snapshot(scope, lease, (Selection(updated), Selection(second)))
            foreign = Scope(scope.tenant, scope.project, scope.branch, "foreign")
            other = store.create_task(foreign, Target("foreign-session", "host", "external"), "Other scope", allowed_providers=("host",))
            store.put(foreign, other, "secret", b"private text", expected_revision=0)

        async def exercise():
            parameters = StdioServerParameters(command=sys.executable,
                args=["-m", "contextrail", "mcp", "--workspace", str(self.root), "--session", "host-session"])
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    async def call(name, arguments, error=False):
                        response = await client.call_tool(name, arguments=arguments)
                        self.assertEqual(response.isError, error)
                        return json.loads(response.content[0].text)
                    listing = await client.list_tools()
                    self.assertTrue(all(tool.annotations.readOnlyHint for tool in listing.tools))
                    read = await call("context.get", asdict(original))
                    self.assertEqual(read["content"], "原始\r\n🙂")
                    live = await call("context.get", {**captured["stdout"]["ref"],
                                                      "start": len(observed.stdout) - len(b"TAIL-live-7429\r\n")})
                    self.assertEqual(live["content"], "TAIL-live-7429\r\n")
                    manifest = await call("context.get", captured["result_ref"])
                    execution = json.loads(manifest["content"])
                    self.assertEqual(execution["exit_code"], observed.returncode)
                    self.assertEqual(execution["source_session"], "host-session")
                    page1 = await call("context.list", {"snapshot": snapshot, "limit": 1})
                    page2 = await call("context.list", {"snapshot": snapshot, "offset": 1, "limit": 1})
                    self.assertEqual(page1["total"], 2)
                    self.assertEqual([page1["entries"][0]["ref"], page2["entries"][0]["ref"]], [asdict(updated), asdict(second)])
                    denied = await call("context.get", {"name": "secret", "revision": 1}, error=True)
                    self.assertEqual(denied["error"]["code"], "not_found")
                    denied = await call("context.get", {**asdict(original), "task": "foreign"}, error=True)
                    self.assertEqual(denied["error"]["code"], "invalid_request")
                    denied = await call("context.execute", {}, error=True)
                    self.assertEqual(denied["error"]["code"], "invalid_request")
                    recovered = await call("context.get", asdict(updated))
                    self.assertEqual(recovered["content"], "updated")
                    caps = await call("context.capabilities", {})
                    self.assertEqual(caps["capabilities"]["read"]["state"], "supported")
                    self.assertEqual(caps["capabilities"]["final_request_control"]["state"], "unknown")
                    self.assertEqual(caps["history_control"], "not_controlled")
                    self.assertFalse(caps["history_replaced"])
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
