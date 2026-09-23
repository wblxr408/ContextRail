import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from contextrail.evaluation import (ALL_TASKS, DeterministicEvidenceModel, MinimalApiAgent, PRESSURE_TASKS,
                                    STRESS_TASKS, run_stress_suite, summarize_comparison)
from contextrail.evaluation.models import ModelReply, ModelUsage, OpenAICompatibleModel, ToolCall
from contextrail.evaluation.dashboard import DashboardState, _html
from contextrail.evaluation.agent_runtime import ServerStatus
from contextrail.evaluation.fixture_api import fixture_handler
from contextrail.evaluation.pricing import PriceBook
from contextrail.evaluation.preflight import api_preflight
from contextrail.evaluation.switching import ModelProfile, SwitchingApiAgent, run_switch_suite
from contextrail.evaluation.swebench import (SWE_BENCH_VERIFIED_SUBSET, SWEbenchTaskInput,
                                             verifier_command, write_official_task_inputs,
                                             write_predictions, write_subset_manifest)
from contextrail.evaluation.workspace import WorkspaceTools


class EvaluationTests(unittest.TestCase):
    def test_api_preflight_does_not_expose_credentials_and_reports_missing_configuration(self):
        with patch.dict("os.environ", {"CONTEXT_RAIL_EVAL_API_BASE_URL": "", "CONTEXT_RAIL_EVAL_API_KEY": "secret", "CONTEXT_RAIL_EVAL_MODEL": ""}, clear=False):
            report = api_preflight()
        self.assertFalse(report["provider_api_ready"])
        self.assertNotIn("secret", json.dumps(report))
        self.assertTrue(report["checks"]["final_request_capture"])
    def test_twelve_stress_tasks_run_a_b_c_with_context_recovery(self):
        results = [MinimalApiAgent(DeterministicEvidenceModel(STRESS_TASKS)).run(task, strategy)
                   for task in STRESS_TASKS for strategy in ("A", "B", "C")]
        grouped = {strategy: [item for item in results if item.strategy == strategy] for strategy in ("A", "B", "C")}
        self.assertEqual(len(STRESS_TASKS), 12)
        self.assertTrue(all(item.success for item in grouped["A"]))
        self.assertTrue(all(not item.success for item in grouped["B"]))
        self.assertTrue(all(item.success for item in grouped["C"]))
        self.assertGreater(sum(item.tool_calls for item in grouped["C"]), 0)
        self.assertTrue(any(event.event == "tool.result" for item in grouped["C"] for event in item.trace))

    def test_tool_loop_is_forced_to_a_final_answer_instead_of_scoring_empty(self):
        # Reproduces the X07 pathology: a model that keeps calling a tool and
        # never emits a tool-free reply within the turn budget.  The harness must
        # give it one final tool-free turn (tools == []) so it answers from the
        # context it already has, rather than scoring the empty tool-loop output.
        task = STRESS_TASKS[0]

        class LoopingThenAnswersModel:
            def __init__(self):
                self.forced_turn_saw_no_tools = None
                self.calls = 0

            def complete(self, _messages, tools):
                self.calls += 1
                if tools:
                    # Never stop calling tools while any are offered.
                    return ModelReply("", (ToolCall("loop", "context.search", {"query": "x"}),),
                                      ModelUsage(5, 1, 0), f"loop-{self.calls}", 1)
                # Offered no tools -> forced to answer from context.
                self.forced_turn_saw_no_tools = True
                return ModelReply("\n".join(task.expected_facts), (), ModelUsage(6, 2, 0), f"forced-{self.calls}", 1)

        # Budget large enough that the fact page is in the initial packet, as in
        # the real X07 run; this isolates the tool-loop fix from must-read recovery.
        model = LoopingThenAnswersModel()
        result = MinimalApiAgent(model, max_turns=3, context_budget=12_000).run(task, "C")
        self.assertTrue(model.forced_turn_saw_no_tools)
        self.assertTrue(result.answer)
        self.assertTrue(result.required_facts_present)
        self.assertTrue(result.success)
        self.assertIn("agent.timeout", [event.event for event in result.trace])
        self.assertIn("forced.answer", [event.event for event in result.trace])

    def test_forced_answer_turn_still_fails_when_model_cannot_answer(self):
        # The forced turn must not manufacture success: a model that loops and
        # then produces a wrong/empty answer is still a genuine failure.
        task = STRESS_TASKS[0]

        class NeverAnswersModel:
            def __init__(self):
                self.calls = 0

            def complete(self, _messages, tools):
                self.calls += 1
                if tools:
                    return ModelReply("", (ToolCall("loop", "context.search", {"query": "x"}),),
                                      ModelUsage(5, 1, 0), f"loop-{self.calls}", 1)
                return ModelReply("", (), ModelUsage(1, 1, 0), f"forced-empty-{self.calls}", 1)

        result = MinimalApiAgent(NeverAnswersModel(), max_turns=3).run(task, "C")
        self.assertFalse(result.answer)
        self.assertFalse(result.success)
        self.assertIn("forced.answer", [event.event for event in result.trace])

    def test_control_and_pressure_tiers_stay_separate_and_correctly_labelled(self):
        self.assertEqual(len(STRESS_TASKS), 12)
        self.assertTrue(all(task.tier == "control" for task in STRESS_TASKS))
        self.assertTrue(PRESSURE_TASKS)
        self.assertTrue(all(task.tier == "pressure" for task in PRESSURE_TASKS))
        self.assertEqual(ALL_TASKS, STRESS_TASKS + PRESSURE_TASKS)
        self.assertEqual(len({task.id for task in ALL_TASKS}), len(ALL_TASKS))

    def test_pressure_tier_lets_recover_on_demand_beat_full_history_on_tokens(self):
        # The pressure tier is where C's cold-page economy must show up: A carries
        # the whole cold haystack every turn, C recovers only the needle page.
        agent = MinimalApiAgent(DeterministicEvidenceModel(PRESSURE_TASKS), context_budget=4000)
        results = [agent.run(task, strategy) for task in PRESSURE_TASKS for strategy in ("A", "B", "C")]
        by_strategy = {s: [r for r in results if r.strategy == s] for s in ("A", "B", "C")}
        self.assertTrue(all(r.success for r in by_strategy["A"]))
        self.assertTrue(all(not r.success for r in by_strategy["B"]))
        self.assertTrue(all(r.success for r in by_strategy["C"]))
        self.assertTrue(all(r.tier == "pressure" for r in results))
        # C must recover at least one cold page and end below A on input tokens.
        self.assertTrue(all(r.tool_calls > 0 for r in by_strategy["C"]))
        self.assertLess(sum(r.input_tokens for r in by_strategy["C"]),
                        sum(r.input_tokens for r in by_strategy["A"]))

    def test_comparison_breaks_the_decision_down_by_tier(self):
        agent = MinimalApiAgent(DeterministicEvidenceModel(ALL_TASKS), context_budget=4000)
        results = [agent.run(task, strategy) for task in ALL_TASKS for strategy in ("A", "C")]
        comparison = summarize_comparison(results, evidence_kind="provider_api", min_paired_runs=1)
        self.assertEqual(set(comparison["per_tier"]), {"control", "pressure"})
        self.assertEqual(comparison["per_tier"]["control"]["paired_runs"], 12)
        self.assertEqual(comparison["per_tier"]["pressure"]["paired_runs"], len(PRESSURE_TASKS))
        # Only the pressure tier can show an input-token reduction.
        self.assertGreater(comparison["per_tier"]["pressure"]["input_tokens_saved"], 0)
        self.assertEqual(comparison["per_tier"]["pressure"]["decision"], "ready_for_scoped_claim")

    def test_suite_writes_reviewable_trace_metrics_and_report(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "run"
            results = run_stress_suite(MinimalApiAgent(DeterministicEvidenceModel(STRESS_TASKS)), STRESS_TASKS, output)
            self.assertEqual(len(results), 36)
            self.assertTrue((output / "metrics.csv").is_file())
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema"], "contextrail.evaluation-manifest/v1")
            self.assertEqual(len(manifest["run_order"]), 36)
            self.assertNotEqual([row["strategy"] for row in manifest["run_order"][:3]], ["A", "B", "C"])
            comparison = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
            self.assertEqual(comparison["decision"], "mechanism_only")
            self.assertIn("| C | 12 | 12/12 |", (output / "report.md").read_text(encoding="utf-8"))
            trace = json.loads((output / "tasks" / "X06" / "C" / "trace.json").read_text(encoding="utf-8"))
            self.assertIn("packet.compiled", [event["event"] for event in trace["events"]])

    def test_comparison_requires_real_paired_nonregressing_token_savings(self):
        results = [MinimalApiAgent(DeterministicEvidenceModel(STRESS_TASKS)).run(task, strategy)
                   for task in STRESS_TASKS for strategy in ("A", "C")]
        fixture = summarize_comparison(results, evidence_kind="fixture", min_paired_runs=12)
        self.assertEqual(fixture["decision"], "mechanism_only")
        api = summarize_comparison(results, evidence_kind="provider_api", min_paired_runs=12)
        self.assertEqual(api["decision"], "no_observed_input_token_reduction")
        self.assertEqual(api["quality_difference_ci95"], [0.0, 0.0])
        self.assertTrue(api["statistical_noninferior"])

    def test_unknown_provider_usage_remains_unknown_in_run_and_comparison(self):
        class UnknownUsageModel:
            def complete(self, _messages, _tools):
                return ModelReply("COMPATIBILITY=legacy-v1", (), ModelUsage(), "unknown-usage", 1)

        result = MinimalApiAgent(UnknownUsageModel()).run(STRESS_TASKS[0], "A")
        self.assertIsNone(result.input_tokens)
        self.assertIsNone(result.output_tokens)
        comparison = summarize_comparison([result, result.__class__(**{**result.__dict__, "strategy": "C"})],
                                          evidence_kind="provider_api", min_paired_runs=1)
        self.assertIsNone(comparison["baseline_input_tokens"])
        self.assertEqual(comparison["decision"], "no_observed_input_token_reduction")

    def test_repetitions_keep_each_trace_and_require_the_configured_pair_count(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "run"
            results = run_stress_suite(MinimalApiAgent(DeterministicEvidenceModel(STRESS_TASKS)), STRESS_TASKS,
                                       output, repetitions=2, min_paired_runs=24)
            self.assertEqual(len(results), 72)
            self.assertTrue((output / "tasks" / "X06" / "C" / "attempt-01" / "trace.json").is_file())
            self.assertTrue((output / "tasks" / "X06" / "C" / "attempt-02" / "trace.json").is_file())
            comparison = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
            self.assertEqual(comparison["paired_runs"], 24)

    def test_swebench_subset_is_frozen_and_predictions_reject_unknown_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = root / "subset.json"
            write_subset_manifest(manifest)
            rows = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 8)
            self.assertEqual(len({row["repo"] for row in rows}), 8)
            predictions = root / "predictions.jsonl"
            write_predictions(predictions, [(SWE_BENCH_VERIFIED_SUBSET[0].instance_id, "diff --git a/a b/a\n")], model_name="test")
            self.assertEqual(json.loads(predictions.read_text(encoding="utf-8"))["instance_id"], SWE_BENCH_VERIFIED_SUBSET[0].instance_id)
            with self.assertRaises(ValueError):
                write_predictions(root / "bad.jsonl", [("outside", "")], model_name="test")

    def test_swebench_agent_inputs_exclude_gold_and_verifier_is_restricted_to_subset(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            inputs = tuple(SWEbenchTaskInput(item.instance_id, item.repo, item.base_commit, item.version,
                                             f"Issue for {item.instance_id}", "a" * 64)
                           for item in SWE_BENCH_VERIFIED_SUBSET)
            path = root / "inputs.json"
            write_official_task_inputs(path, inputs)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(saved["tasks"]), 8)
            self.assertEqual(saved["gold_fields_excluded"], ["patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS"])
            self.assertNotIn("patch", saved["tasks"][0])
            command = verifier_command(root / "predictions.jsonl", run_id="context-rail-c", report_dir=root / "report")
            self.assertEqual(command[:4], ["swebench", "eval", "verified", "--predictions"])
            self.assertEqual(command.count("--instance"), 8)

    def test_openai_compatible_adapter_sends_tools_and_parses_usage(self):
        seen = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                seen["path"] = self.path
                seen["authorization"] = self.headers.get("Authorization")
                seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                response = {"id": "request-1", "choices": [{"message": {"content": "", "tool_calls": [{"id": "tool-1", "type": "function", "function": {"name": "context.get", "arguments": '{"name":"evidence-1","revision":1}'}}]}}], "usage": {"prompt_tokens": 12, "completion_tokens": 3, "prompt_tokens_details": {"cached_tokens": 4}}}
                encoded = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("x-request-id", "header-request")
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        adapter = OpenAICompatibleModel(base_url=f"http://127.0.0.1:{server.server_port}/v1", api_key="test-key", model="test-model")
        reply = adapter.complete([{"role": "user", "content": "hello"}], [{"type": "function", "function": {"name": "context.get", "parameters": {}}}])
        self.assertEqual(seen["path"], "/v1/chat/completions")
        self.assertEqual(seen["authorization"], "Bearer test-key")
        self.assertEqual(seen["body"]["model"], "test-model")
        self.assertEqual(reply.tool_calls[0].arguments, {"name": "evidence-1", "revision": 1})
        self.assertEqual(reply.usage.cached_input_tokens, 4)

    def test_dotted_tool_names_are_wire_safe_on_send_and_restored_on_receive(self):
        # DeepSeek/OpenAI reject function names containing '.', so the adapter
        # must rewrite context.get -> context__get on the wire, echo the wire
        # name in the payload's tools and prior tool_calls, and restore the
        # exact dotted name on the returned tool call.
        seen = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                # A real provider echoes back the wire name it was given.
                response = {"id": "r", "choices": [{"message": {"content": "", "tool_calls": [
                    {"id": "t1", "type": "function",
                     "function": {"name": "workspace__run", "arguments": "{}"}}]}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
                encoded = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        adapter = OpenAICompatibleModel(base_url=f"http://127.0.0.1:{server.server_port}", api_key="k", model="m")
        messages = [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "prev", "type": "function",
                 "function": {"name": "context.get", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "prev", "content": "{}"},
        ]
        tools = [{"type": "function", "function": {"name": "context.get", "parameters": {}}},
                 {"type": "function", "function": {"name": "workspace.run", "parameters": {}}}]
        reply = adapter.complete(messages, tools)
        sent_tool_names = {tool["function"]["name"] for tool in seen["body"]["tools"]}
        self.assertEqual(sent_tool_names, {"context__get", "workspace__run"})
        self.assertNotIn(".", "".join(sent_tool_names))
        # The prior assistant tool_call name is rewritten to the wire form too.
        sent_history_call = seen["body"]["messages"][1]["tool_calls"][0]["function"]["name"]
        self.assertEqual(sent_history_call, "context__get")
        # The wire name the provider returned is restored to its dotted form.
        self.assertEqual(reply.tool_calls[0].name, "workspace.run")

    def test_openai_compatible_adapter_completes_a_real_loopback_context_recovery_run(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), fixture_handler(DeterministicEvidenceModel(STRESS_TASKS)))
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        adapter = OpenAICompatibleModel(base_url=f"http://127.0.0.1:{server.server_port}/v1", api_key="fixture",
                                        model="fixture-model")
        result = MinimalApiAgent(adapter).run(STRESS_TASKS[0], "C")
        self.assertTrue(result.success)
        self.assertIsNotNone(result.input_tokens)
        self.assertTrue(any(event.event == "tool.result" for event in result.trace))

    def test_workspace_tools_confine_paths_and_run_in_task_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tools = WorkspaceTools(root)
            tools.call("workspace.write_file", {"path": "nested/example.txt", "content": "exact text"})
            self.assertEqual(tools.call("workspace.read_file", {"path": "nested/example.txt"})["content"], "exact text")
            self.assertEqual(tools.call("workspace.run", {"command": "python -c \"print('workspace-ok')\""})["stdout"].strip(), "workspace-ok")
            with self.assertRaises(ValueError):
                tools.call("workspace.read_file", {"path": "../outside.txt"})

    def test_coding_agent_uses_workspace_tool_and_emits_patch(self):
        class CodingModel:
            calls = 0

            def complete(self, _messages, _tools):
                self.calls += 1
                if self.calls == 1:
                    return ModelReply("", (ToolCall("write-1", "workspace.write_file", {"path": "answer.txt", "content": "fixed\n"}),), ModelUsage(5, 2, 0), "local-1", 1)
                return ModelReply("done", (), ModelUsage(6, 2, 0), "local-2", 1)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "answer.txt").write_text("broken\n", encoding="utf-8")
            for command in (("git", "init"), ("git", "config", "user.email", "test@example.com"),
                            ("git", "config", "user.name", "Test"), ("git", "add", "answer.txt"), ("git", "commit", "-m", "base")):
                subprocess.run(command, cwd=root, check=True, capture_output=True)
            result = MinimalApiAgent(CodingModel()).run_coding_task(task_id="fixture__repo-1", objective="Change answer.txt to fixed.",
                                                                    workspace=WorkspaceTools(root), strategy="C")
            self.assertEqual(result.answer, "done")
            self.assertIn("+fixed", result.patch)
            self.assertEqual(result.tool_calls, 1)

    def test_versioned_price_book_counts_cached_tokens_once(self):
        price = PriceBook("fixture-2026-09", input_per_million=2, output_per_million=8, cached_input_per_million=0.5)
        self.assertAlmostEqual(price.estimate(input_tokens=1_000_000, cached_input_tokens=400_000, output_tokens=100_000), 2.2)
        with self.assertRaises(ValueError):
            price.estimate(input_tokens=10, cached_input_tokens=11, output_tokens=0)
        cache_write = PriceBook("fixture-write", input_per_million=2, output_per_million=8,
                                cache_write_per_million=1)
        self.assertAlmostEqual(cache_write.estimate(input_tokens=1_000_000, output_tokens=0,
                                                    cache_write_tokens=500_000), 2.5)
        with self.assertRaises(ValueError):
            cache_write.estimate(input_tokens=1, output_tokens=1)

    def test_dashboard_external_result_is_visible_without_a_persisted_key_and_html_exposes_four_views(self):
        with tempfile.TemporaryDirectory() as folder:
            state = DashboardState(Path(folder))
            directory = Path(folder) / "upstream-run"
            directory.mkdir()
            (directory / "external-result.json").write_text(
                '{"agent":"swe-agent","status":"complete","artifacts":["trajectory.json"],'
                '"details":{"model":"example-model","api_key_persisted":false}}', encoding="utf-8"
            )
            (directory / "sweagent.stdout.log").write_text("trajectory complete\n", encoding="utf-8")
            report = state.read_run("upstream-run")
            self.assertEqual(report["kind"], "external")
            self.assertNotIn("super-secret", json.dumps(report))
            self.assertIn("upstream-run", [row["id"] for row in state.list_runs()])
        page = _html()
        self.assertIn("01 // RUN CONTROL", page)
        self.assertIn("02 // Overview", page)
        self.assertIn("03 // SWE-BENCH", page)
        self.assertIn("04 // PRESSURE MATRIX", page)
        self.assertIn("RUN OFFICIAL SWE-AGENT", page)
        self.assertIn("RUN OPENHANDS AGENT", page)

    def test_dashboard_catalog_reports_upstream_readiness_without_failing_state_route(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog = DashboardState(Path(folder)).catalog()
        self.assertEqual([row["id"] for row in catalog], ["minimal", "swe-agent", "openhands"])
        self.assertTrue(catalog[0]["available"])

    def test_dashboard_preflight_rejects_openhands_before_queue_when_server_is_not_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            state = DashboardState(Path(folder))
            payload = {
                "agent": "openhands",
                "mode": "context",
                "base_url": "https://api.example/v1",
                "api_key": "secret",
                "model": "example/model",
                "openhands_server_url": "http://127.0.0.1:8000",
                "openhands_workspace": "workspace/project",
                "openhands_task": "Inspect the repository.",
            }
            with self.assertRaisesRegex(ValueError, "not READY"):
                state.submit(payload)
            self.assertEqual(state.jobs, {})

    def test_dashboard_preflight_checks_managed_openhands_workspace(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = DashboardState(root)
            state.openhands_server._url = "http://127.0.0.1:8123"
            ready = ServerStatus("ready", "http://127.0.0.1:8123", True, 123, [])
            payload = {
                "agent": "openhands",
                "mode": "context",
                "base_url": "https://api.example/v1",
                "api_key": "secret",
                "model": "example/model",
                "openhands_server_url": "http://127.0.0.1:8123",
                "openhands_workspace": "missing-workspace",
                "openhands_task": "Inspect the repository.",
            }
            with patch.object(state.openhands_server, "status", return_value=ready), self.assertRaisesRegex(
                ValueError, "workspace does not exist"
            ):
                state.submit(payload)
            self.assertEqual(state.jobs, {})

    def test_switching_agent_verifies_receipt_epoch_and_action_ledger_across_profiles(self):
        profiles = {profile: ModelProfile(profile, f"fixture-{profile}", DeterministicEvidenceModel(STRESS_TASKS))
                    for profile in ("A", "B", "C")}
        agent = SwitchingApiAgent(profiles)
        with tempfile.TemporaryDirectory() as folder:
            results = run_switch_suite(agent, STRESS_TASKS[:1], Path(folder),
                                       trajectories=("A->B", "A->B->A", "A->B->C", "A->B-failover"))
            self.assertEqual(len(results), 4)
            self.assertTrue(all(result.success for result in results))
            self.assertTrue(all(result.receipt_verified and result.epoch_verified for result in results))
            self.assertTrue(all(result.duplicate_action_blocked for result in results))
            self.assertTrue((Path(folder) / "switch-metrics.csv").is_file())
