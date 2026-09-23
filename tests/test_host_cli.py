import base64
from dataclasses import asdict
import io
import json
import subprocess
import sys

from contextrail import ContextTools
from contextrail.cli import demo, serve
from contextrail.errors import AccessDenied, InvalidRequest
from .common import RailTest


class HostTests(RailTest):
    def test_utf8_and_binary_read_are_exact_and_bounded(self):
        text = self.put("text", "中文\r\n".encode())
        binary = self.put("binary", bytes(range(256)), media_type="application/octet-stream")
        tools = ContextTools(self.store, self.scope, self.a.session, max_read_bytes=100)
        self.assertEqual(tools.call("context.get", asdict(text))["content"].encode(), "中文\r\n".encode())
        with self.assertRaises(InvalidRequest):
            tools.call("context.get", asdict(binary))
        args = {**asdict(binary), "start": 150, "end": 200}
        output = tools.call("context.get", args)
        self.assertEqual(base64.b64decode(output["content"]), bytes(range(150, 200)))

    def test_untrusted_arguments_cannot_change_scope_or_execute_mutations(self):
        ref = self.put()
        tools = ContextTools(self.store, self.scope, self.a.session)
        for key in ("scope", "path", "provider", "sql"):
            with self.subTest(key=key), self.assertRaises(InvalidRequest):
                tools.call("context.get", {**asdict(ref), key: "escape"})
        for operation in ("artifact.put", "handoff.activate", "shell"):
            with self.assertRaises(InvalidRequest):
                tools.call(operation, {})
        with self.assertRaises(AccessDenied):
            ContextTools(self.store, self.scope, "not-registered")

    def test_transport_errors_are_structured_and_next_request_still_works(self):
        self.put(content=b"marker")
        request = {"id": "ok", "tool": "context.search", "arguments": {"query": "marker"}}
        stream = io.StringIO("bad secret prompt JSON\n" + json.dumps(request) + "\n")
        sink = io.StringIO()
        serve(ContextTools(self.store, self.scope, self.a.session), stream, sink)
        responses = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(responses[0]["error"]["code"], "invalid_request")
        self.assertNotIn("secret prompt", sink.getvalue())
        self.assertEqual(responses[1]["id"], "ok")
        self.assertEqual(len(responses[1]["result"]["refs"]), 1)

    def test_transport_oversize_consumes_one_frame_and_recovers(self):
        request = {"id": 2, "tool": "context.search", "arguments": {"query": "missing"}}
        stream = io.StringIO("x" * 1000 + "\n" + json.dumps(request) + "\n")
        sink = io.StringIO()
        serve(ContextTools(self.store, self.scope, self.a.session), stream, sink, max_request_chars=128)
        responses = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]["error"]["code"], "invalid_request")
        self.assertEqual(responses[1]["result"]["refs"], [])

    def test_transport_invalid_argument_types_do_not_crash(self):
        cases = [None, [], {"id": 1}, {"id": True, "tool": "context.search", "arguments": {}},
                 {"id": 2, "tool": "context.get", "arguments": {"name": [], "revision": 1}},
                 {"id": 3, "tool": "context.get", "arguments": {"name": "x", "revision": "1"}},
                 {"id": 4, "tool": "context.search", "arguments": {"query": None}},
                 {"id": "\ud800", "tool": "context.search", "arguments": {"query": "text"}},
                 {"id": 6, "tool": "context.search", "arguments": {"query": "\ud800"}},
                 {"id": 7, "tool": "context.get", "arguments": {"name": "x", "revision": 10**100}},
                 {"id": 5, "tool": "context.get", "arguments": {"name": "x", "revision": 1, "start": None}}]
        sink = io.StringIO()
        serve(ContextTools(self.store, self.scope, self.a.session), io.StringIO("\n".join(map(json.dumps, cases))), sink)
        self.assertEqual(len(sink.getvalue().splitlines()), len(cases))
        self.assertTrue(all(json.loads(line)["error"]["code"] == "invalid_request" for line in sink.getvalue().splitlines()))

    def test_cli_subprocess_serves_bound_task_without_stdout_noise(self):
        ref = self.put(content=b"subprocess evidence")
        command = [sys.executable, "-m", "contextrail", "serve", "--store", str(self.path),
                   "--tenant", self.scope.tenant, "--project", self.scope.project,
                   "--branch", self.scope.branch, "--task", self.scope.task, "--session", self.a.session]
        result = subprocess.run(command, input=json.dumps({"id": 1, "tool": "context.get", "arguments": asdict(ref)}) + "\n",
                                text=True, encoding="utf-8", capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout)["result"]["content"], "subprocess evidence")

    def test_cli_init_existing_task_preserved_and_errors_on_stderr(self):
        command = [sys.executable, "-m", "contextrail", "init", "--store", str(self.path),
                   "--tenant", self.scope.tenant, "--project", self.scope.project, "--branch", self.scope.branch,
                   "--task", self.scope.task, "--session", self.a.session, "--provider", self.a.provider,
                   "--model", self.a.model, "--objective", "overwrite", "--allow-provider", self.a.provider]
        result = subprocess.run(command, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "conflict")
        self.assertEqual(self.store.task(self.scope)["objective"], "Exact evidence handoff")

    def test_cli_invalid_utf8_fails_without_echoing_input(self):
        command = [sys.executable, "-m", "contextrail", "serve", "--store", str(self.path),
                   "--tenant", self.scope.tenant, "--project", self.scope.project,
                   "--branch", self.scope.branch, "--task", self.scope.task, "--session", self.a.session]
        result = subprocess.run(command, input=b"private input \xff\n", capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "invalid_request")
        self.assertNotIn(b"private input", result.stderr)

    def test_demo_is_real_local_state_flow_not_model_evaluation(self):
        result = demo()
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual([s["epoch"] for s in result["handoffs"]], [1, 2])
        self.assertEqual([s["cold_pages"] for s in result["handoffs"]], [1, 1])
        self.assertEqual(result["final_owner"], "session-a")
        self.assertTrue(result["exact_evidence_verified"])
