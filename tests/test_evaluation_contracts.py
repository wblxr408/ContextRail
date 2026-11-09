"""Consumer contracts for accounting, authorization and evaluation."""

from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

from contextrail.auth import AuthorizationService, Principal
from contextrail.context import Compiler
from contextrail.errors import AccessDenied, Conflict
from contextrail.models import Scope, Selection, Target
from contextrail.oidc_adapter import AuthorizedContextTools
from contextrail.policy import UsageLedger
from contextrail.store import Store
from contextrail.evaluation.agent import MinimalApiAgent, run_stress_suite
from contextrail.evaluation.models import ModelReply, ModelRequestError, ModelUsage, OpenAICompatibleModel, ToolCall
from contextrail.evaluation.switching import ModelProfile, SwitchingApiAgent
from contextrail.evaluation.tasks import PRESSURE_TASKS, STRESS_TASKS


class LedgerAndAuthorization(unittest.TestCase):
    def setUp(self):
        workspace = self.enterContext(tempfile.TemporaryDirectory())
        self.store = self.enterContext(Store(Path(workspace) / "data.sqlite"))
        self.scope = Scope("tenant", "project", "main", "one")
        self.target = Target("session", "provider", "model")
        self.lease = self.store.create_task(self.scope, self.target, "Read evidence",
                                            allowed_providers=(self.target.provider,))
        self.ref = self.store.put(self.scope, self.lease, "note", b"original", expected_revision=0)
        snapshot = self.store.snapshot(self.scope, self.lease, (Selection(self.ref),))
        self.packet = Compiler(self.store).compile(self.scope, snapshot, self.target.session, budget=8000)

    def test_spent_request_remains_recordable_after_evidence_withdrawal(self):
        self.store.invalidate(self.scope, self.lease, self.ref)
        ledger = UsageLedger(self.store)
        record = ledger.record_request(self.scope, self.packet, unit="tokens", input_units=21, output_units=5)
        self.assertEqual(record.snapshot, self.packet.snapshot)
        self.assertEqual(ledger.totals(self.scope)["input_units"], 21)

    def test_request_metadata_and_provider_id_deduplication(self):
        ledger = UsageLedger(self.store)
        fields = dict(unit="tokens", input_units=21, output_units=5, request_id="spent-1",
                      timestamp=1234.5, price_version="2026-11", request_cost=0.00015,
                      tool_calls=2, retries=0, error_code=None, ttft_ms=45)
        record = ledger.record_request(self.scope, self.packet, **fields)
        self.assertEqual((record.snapshot, record.provider, record.timestamp, record.request_cost),
                         (self.packet.snapshot, "provider", 1234.5, 0.00015))
        self.assertEqual((record.price_version, record.tool_calls, record.retries, record.ttft_ms),
                         ("2026-11", 2, 0, 45))
        with self.assertRaises(Conflict):
            ledger.record_request(self.scope, self.packet, **fields)
        self.assertEqual(ledger.totals(self.scope)["input_units"], 21)

    def test_totals_preserve_partial_counts_and_keep_incompatible_units_separate(self):
        ledger = UsageLedger(self.store)
        ledger.record(self.scope, self.packet, unit="tokens", input_units=9, output_units=2)
        ledger.record_request(self.scope, self.packet, unit="tokens", input_units=None, output_units=3)
        ledger.record_request(self.scope, self.packet, unit="utf8_bytes", input_units=120, output_units=50)
        totals = ledger.totals(self.scope, session=self.target.session)
        self.assertEqual(totals["requests"], 3)
        self.assertIsNone(totals["input_units"])
        self.assertEqual(totals["by_unit"]["tokens"]["output_units"], 5)
        self.assertIsNone(totals["by_unit"]["tokens"]["input_units"])
        self.assertEqual(totals["by_unit"]["tokens"]["observed"]["input_units"], 9)
        self.assertEqual(totals["by_unit"]["utf8_bytes"]["input_units"], 120)

    def test_project_role_does_not_expose_sibling_branch_or_task(self):
        auth = AuthorizationService(self.store)
        principal = Principal("https://identity.example.test", "member")
        auth.create_tenant(self.scope.tenant)
        auth.create_project(self.scope.tenant, self.scope.project)
        auth.grant_project_role(principal, self.scope.tenant, self.scope.project, "viewer")
        siblings = (Scope("tenant", "project", "dev", "one"), Scope("tenant", "project", "main", "two"))
        for sibling in siblings:
            lease = self.store.create_task(sibling, self.target, "Sibling", allowed_providers=(self.target.provider,))
            self.store.put(sibling, lease, self.ref.name, b"private sibling", expected_revision=0)
            tools = AuthorizedContextTools(self.store, sibling, self.target.session,
                                           principal=principal, authorization=auth)
            with self.assertRaises(AccessDenied):
                tools.call("context.get", {"name": self.ref.name, "revision": self.ref.revision})
        auth.grant_scope_access(principal, self.scope)
        tools = AuthorizedContextTools(self.store, self.scope, self.target.session,
                                       principal=principal, authorization=auth)
        self.assertEqual(tools.call("context.get", {"name": self.ref.name, "revision": 1})["content"], "original")
        auth.revoke_scope_access(principal, self.scope)
        with self.assertRaises(AccessDenied):
            tools.call("context.get", {"name": self.ref.name, "revision": 1})
        self.assertEqual([(event["branch"], event["task"]) for event in auth.audit_events()[:2]],
                         [("dev", "one"), ("main", "two")])


class EvaluationContracts(unittest.TestCase):
    def test_switch_preserves_unknown_counts_across_known_and_unobserved_requests(self):
        class Reply:
            def __init__(self, usage):
                self.usage = usage

            def complete(self, messages, tools):
                return ModelReply("COMPATIBILITY=legacy-v1", usage=self.usage)

        profiles = {"A": ModelProfile("A", "first", Reply(ModelUsage(10, 1, 0))),
                    "B": ModelProfile("B", "second", Reply(ModelUsage(None, 2, None)))}
        result = SwitchingApiAgent(profiles).run(STRESS_TASKS[0], "A->B")
        self.assertTrue(result.success)
        self.assertEqual(result.output_tokens, 3)
        self.assertIsNone(result.input_tokens)
        self.assertIsNone(result.cached_input_tokens)

    def test_stream_measures_first_output_and_merges_fragmented_tool_and_usage(self):
        requests = []

        class Provider(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("x-request-id", "req-stream")
                self.end_headers()
                chunks = [
                    {"choices": [{"index": 0, "delta": {"role": "assistant"}}]},
                    {"choices": [{"index": 0, "delta": {"tool_calls": [
                        {"index": 0, "id": "tool-1", "function": {"name": "context__get", "arguments": '{"name":'}}]}}]},
                    {"choices": [{"index": 0, "delta": {"tool_calls": [
                        {"index": 0, "function": {"arguments": '"note","revision":1}'}}]}, "finish_reason": "tool_calls"}]},
                    {"choices": [], "usage": {"prompt_tokens": 34, "completion_tokens": 8,
                                               "prompt_tokens_details": {"cached_tokens": 12}}},
                ]
                for chunk in chunks:
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")

            def log_message(self, *_):
                pass

        server = self.enterContext(ThreadingHTTPServer(("127.0.0.1", 0), Provider))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        client = OpenAICompatibleModel(base_url=f"http://127.0.0.1:{server.server_port}", api_key="test",
                                       model="local", provider="fixture-wire", stream=True)
        with patch("contextrail.evaluation.models.time.perf_counter", side_effect=[0, 0.025, 0.100]):
            reply = client.complete([{"role": "user", "content": "Read it"}],
                                    [{"type": "function", "function": {"name": "context.get"}}])
        self.assertEqual((reply.ttft_ms, reply.latency_ms, reply.request_id), (25, 100, "req-stream"))
        self.assertEqual(reply.tool_calls, (ToolCall("tool-1", "context.get", {"name": "note", "revision": 1}),))
        self.assertEqual(reply.usage, ModelUsage(34, 8, 12, None))
        self.assertTrue(requests[0]["stream_options"]["include_usage"])

    def test_pressure_oracle_rejects_wrong_revision_and_forbidden_attempt(self):
        task = next(task for task in STRESS_TASKS if task.id == "X03")
        class WrongRead:
            def __init__(self):
                self.calls = 0

            def complete(self, messages, tools):
                self.calls += 1
                self_outer.assertNotIn("forbidden_actions", json.dumps(messages))
                if self.calls == 1:
                    return ModelReply("", (ToolCall("read", "context.get", {"name": "evidence-1", "revision": 1}),
                                           ToolCall("publish", "publish_release", {})))
                return ModelReply("API_REVISION=v2")

        self_outer = self
        result = MinimalApiAgent(WrongRead()).run(task, "B")
        self.assertTrue(result.required_facts_present)
        self.assertFalse(result.required_reads_present)
        self.assertFalse(result.success)
        self.assertIn("publish_release", result.forbidden_actions_attempted)
        self.assertEqual(result.wrong_revision_reads, 1)
        self.assertTrue(all(task.oracle.acceptance.commands and task.oracle.must_read for task in PRESSURE_TASKS))

    def test_pure_summary_uses_separate_billed_call_and_reports_cache_cohorts(self):
        task = PRESSURE_TASKS[0]
        class Summarizer:
            model = "fixture"
            provider = "fixture"

            def __init__(self):
                self.requests = []

            def complete(self, messages, tools):
                self.requests.append(messages)
                if "phase" in json.loads(messages[1]["content"]):
                    return ModelReply("NEEDLE=deep-buried-constant", usage=ModelUsage(120, 7, 0),
                                      request_id="summary", latency_ms=40, ttft_ms=10)
                return ModelReply("NEEDLE=deep-buried-constant", usage=ModelUsage(60, 3, 20),
                                  request_id="answer", latency_ms=25, ttft_ms=5)

        model = Summarizer()
        root = Path(self.enterContext(tempfile.TemporaryDirectory())) / "run"
        result, = run_stress_suite(MinimalApiAgent(model), (task,), root, strategies=("B-summary",))
        self.assertIn("cold reference material block", model.requests[0][1]["content"])
        self.assertNotIn("cold reference material block", model.requests[1][1]["content"])
        self.assertTrue(result.required_facts_present)
        self.assertFalse(result.required_reads_present)
        self.assertEqual(result.input_tokens, 180)
        metrics = json.loads((root / "comparison.json").read_text(encoding="utf-8"))["telemetry"]["B-summary"]
        self.assertEqual(metrics["cache"]["hit_rate"], 0.5)
        self.assertEqual(metrics["request_latency"]["cold"]["completion_ms"]["p95"], 40)
        self.assertEqual(metrics["request_latency"]["hot"]["ttft_ms"]["p50"], 5)

    def test_cold_read_repeats_are_counted_with_followup_requests_and_token_prefix(self):
        task = PRESSURE_TASKS[0]

        class ReReader:
            def __init__(self):
                self.turn = 0

            def complete(self, messages, tools):
                self.turn += 1
                calls = (ToolCall(f"read-{self.turn}", "context.get", {"name": "evidence-1", "revision": 1}),)
                return ModelReply("" if self.turn < 3 else task.expected_facts[0],
                                  calls if self.turn < 3 else (), ModelUsage(100, 5, 0), latency_ms=10)

        root = Path(self.enterContext(tempfile.TemporaryDirectory())) / "run"
        agent = MinimalApiAgent(ReReader(), tokenizer=str.split, tokenizer_id="fixture-words")
        result, = run_stress_suite(agent, (task,), root, strategies=("B",))
        self.assertTrue(result.success)
        self.assertEqual(result.requests[0]["repeated_prefix_tokens"], 0)
        self.assertGreater(result.requests[1]["repeated_prefix_token_ratio"], 0)
        self.assertEqual(result.requests[0]["packet_bytes_per_input_token"],
                         result.requests[0]["packet_bytes"] / 100)
        metrics = json.loads((root / "comparison.json").read_text(encoding="utf-8"))["telemetry"]["B"]
        cold = metrics["cold_pages"]
        self.assertEqual((cold["requests"], cold["successful_reads"], cold["followup_model_requests"]), (2, 2, 2))
        self.assertEqual(cold["repeated_read_rate"], 0.5)
        self.assertEqual(cold["bytes"], 2 * len(task.fact_artifacts[0].text.encode("utf-8")))

    def test_failed_provider_request_is_exported_with_unknown_usage_and_no_cost_claim(self):
        class Denied:
            provider = "test-provider"

            def complete(self, messages, tools):
                raise ModelRequestError("http_429", ModelReply("", request_id="quota-1", latency_ms=20, retries=0))

        root = Path(self.enterContext(tempfile.TemporaryDirectory())) / "run"
        result, = run_stress_suite(MinimalApiAgent(Denied()), (PRESSURE_TASKS[0],), root, strategies=("A",))
        self.assertFalse(result.success)
        self.assertIsNone(result.input_tokens)
        record = json.loads((root / "tasks" / result.task_id / "A" / "usage.jsonl").read_text(encoding="utf-8"))
        self.assertEqual((record["provider"], record["request_id"], record["error_code"], record["retries"]),
                         ("test-provider", "quota-1", "http_429", 0))
        self.assertIsNone(record["request_cost"])
        self.assertIsNone(record["tool_calls"])

    def test_oracle_checks_exact_path_and_rejects_conflicting_values(self):
        class Answer:
            def __init__(self, text):
                self.text = text

            def complete(self, messages, tools):
                return ModelReply(self.text)

        task = next(task for task in STRESS_TASKS if task.id == "X04")
        self.assertFalse(MinimalApiAgent(Answer("ERROR=E409; PATH=src/中文.py.bak")).run(task, "A").success)
        self.assertTrue(MinimalApiAgent(Answer(json.dumps({"ERROR": "E409", "PATH": "src/中文.py"}))).run(task, "A").success)
        task = next(task for task in STRESS_TASKS if task.id == "X03")
        self.assertFalse(MinimalApiAgent(Answer("API_REVISION=v1; API_REVISION=v2")).run(task, "A").success)
