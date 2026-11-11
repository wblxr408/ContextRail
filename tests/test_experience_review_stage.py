"""Current G04/G05 behavior through the local CLI and the original capture tables."""
from pathlib import Path
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from contextrail.event_stream import SCHEMA as EVENT_SCHEMA, capture_record, get_event
from contextrail.source_events import SCHEMA as SOURCE_SCHEMA, workspace_key


class ExperienceReviewTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.home = self.root / "memory"
        self.home.mkdir()
        self.binding = self.root / "binding.json"
        self.db = sqlite3.connect(self.home / "state.sqlite")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE jobs(source TEXT)")
        self.db.executescript(SOURCE_SCHEMA + EVENT_SCHEMA)
        self.run_cli("project-init", "--objective", "Review repeated work")
        self.select("one", "two")

    def run_cli(self, command, *args, expected=0):
        base = ["--workspace", str(self.workspace), "--task", "experience",
                "--session", "owner", "--store", str(self.root / "rail.sqlite"),
                "--response-bytes", "16384"]
        if command.startswith("experience-"):
            base.extend(["--query-binding", str(self.binding)])
        run = subprocess.run([sys.executable, "-m", "contextrail", command, *base, *args],
                             capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(run.returncode, expected, run.stderr or run.stdout)
        return json.loads(run.stdout) if expected == 0 else run.stderr

    def select(self, *sessions):
        self.binding.write_text(json.dumps({
            "schema": 1, "workspace": str(self.workspace), "task": "experience",
            "memory_home": str(self.home),
            "native_sessions": [{"client": "codex", "session": name} for name in sessions],
        }), encoding="utf-8")

    def capture(self, session, actions, day, *, workspace=None, advice=None):
        workspace = workspace or self.workspace
        context = {}
        records = [{"type": "session_meta", "payload": {"id": session, "cwd": str(workspace)}}]
        for index, (command, code) in enumerate(actions):
            call = f"call-{index}"
            call_payload = {"type": "function_call", "call_id": call, "name": "functions.exec_command",
                            "arguments": json.dumps({"cmd": command, "workdir": str(workspace)})}
            output = {"chunk_id": call, "wall_time_seconds": 0.3,
                      "output": "Permission denied" if code else "done"}
            if code is None:
                output["session_id"] = 17
            else:
                output["exit_code"] = code
            result_payload = {"type": "function_call_output", "call_id": call, "output": json.dumps(output)}
            if isinstance(command, dict):
                call_payload = {"type": "custom_tool_call", "name": "exec", "call_id": call,
                                "input": command["script"]}
                result_payload = {"type": "custom_tool_call_output", "call_id": call, "output": [
                    {"type": "input_text", "text": "Script completed\nWall time 0.3 seconds\nOutput:"},
                    {"type": "input_text", "text": json.dumps(output) if command["full"] else "done"},
                ]}
            records.extend({"type": "response_item", "payload": payload}
                           for payload in (call_payload, result_payload))
        if advice:
            records.append({"type": "response_item", "payload": {
                "type": "message", "id": "suggestion", "role": "assistant",
                "content": [{"type": "output_text", "text": advice}]}})
        with self.db:
            self.db.execute('BEGIN')
            for index, row in enumerate(records):
                row["timestamp"] = f"2026-11-{day:02d}T08:{index:02d}:00Z"
                capture_record(self.db, "codex", row, context, f"{session}.jsonl",
                               index * 100, (index + 1) * 100, {"generation": 1}, workspace_key)

    def review(self, *args):
        return self.run_cli("experience-review", *args)

    def reject(self, candidate, *args, expected=0):
        return self.run_cli("experience-reject", "--epoch", "0", "--candidate", candidate["id"],
                            "--evidence-version", candidate["evidence_version"],
                            "--reason", "These are temporary project steps", *args, expected=expected)

    def test_selected_sessions_supply_exact_sources_for_repeated_work_and_failures(self):
        actions = [("python build.py", 0), ("python pack.py", 0),
                   ("python protected.py", 1), ("python pending.py", None)]
        self.capture("one", actions + [("python once.py", 0), ("python once.py", 0)], 1,
                     advice="Always deploy after builds")
        self.capture("two", actions, 2)
        self.capture("three", [("python once.py", 0)], 2)
        self.capture("two", [("python once.py", 0)], 2, workspace=self.root / "elsewhere")
        review = self.review()
        candidates = review["candidates"]
        steps = [tuple(action["command"] for action in row["actions"]) for row in candidates]
        self.assertCountEqual(steps, [("python build.py",), ("python pack.py",),
                                      ("python protected.py",), ("python build.py", "python pack.py")])
        failure = next(row for row in candidates if row["kind"] == "repeated_failure")
        self.assertEqual(failure["category"], "permission_denied")
        self.assertEqual({row["native_session"] for row in failure["evidence"]}, {"one", "two"})
        for evidence in failure["evidence"]:
            for ref, kind in zip(evidence["refs"], ("tool.call", "tool.result"), strict=True):
                self.assertEqual(ref["reader"], "memory.stream_get")
                captured = get_event(self.db, ref["workspace"], ref["client"],
                                     ref["native_session"], ref["event_id"])
                self.assertEqual((captured["native_session"], captured["event_type"]),
                                 (evidence["native_session"], kind))
        first = self.review("--limit", "1")
        rest = self.review("--offset", str(first["next_offset"]))
        self.assertEqual(first["candidates"] + rest["candidates"], candidates)
        self.assertEqual(self.review("--since", "2026-11-02T00:00:00Z")["candidates"], [])
        self.assertEqual(review["coverage"][1]["unknown_outcomes"], 1)

    def test_rejection_is_idempotent_and_can_be_reconsidered_or_reopened_with_new_evidence(self):
        for day, session in enumerate(("one", "two", "three"), 1):
            self.capture(session, [("python build.py", 0)], day)
        original, = self.review()["candidates"]
        recorded = self.reject(original)
        self.assertEqual(self.reject(original), recorded)
        self.assertIn("different rejection reason",
                      self.reject(original, "--reason", "Another reason", expected=1))
        hidden = self.review()
        self.assertEqual(hidden["candidates"], [])
        self.assertEqual(hidden["suppressed_count"], 1)
        reopened, = self.review("--reconsider", original["id"])["candidates"]
        self.assertEqual(reopened["review_reason"], "explicit_reconsideration")
        self.assertEqual(reopened["last_rejection"]["reason"], "These are temporary project steps")
        self.select("one", "two", "three")
        expanded, = self.review()["candidates"]
        self.assertEqual(expanded["id"], original["id"])
        self.assertEqual(expanded["review_reason"], "new_evidence")
        self.assertEqual(expanded["last_rejection"]["evidence_version"], original["evidence_version"])
        self.assertEqual(set(expanded["new_evidence_event_ids"]),
                         {ref["event_id"] for item in expanded["evidence"] if item["native_session"] == "three"
                          for ref in item["refs"]})
        self.assertIn("evidence changed", self.reject(original, expected=1).lower())
        self.reject(expanded)
        self.select("two", "three")
        self.assertEqual(self.review()["candidates"], [])

    def test_review_requires_independent_sessions_and_rejection_requires_current_owner(self):
        self.capture("one", [("python build.py", 0)], 1)
        self.capture("two", [("python build.py", 0)], 2)
        self.select("one")
        self.assertIn("two distinct", self.run_cli("experience-review", expected=1))
        self.select("one", "two")
        candidate, = self.review()["candidates"]
        self.assertIn("stale", self.reject(candidate, "--epoch", "1", expected=1).lower())
        self.assertEqual(self.review()["candidates"], [candidate])

    def test_code_tool_wrapper_needs_a_recorded_child_outcome(self):
        script = 'text(await tools.exec_command({cmd:"python build.py"}))'
        for day, session in enumerate(("one", "two"), 1):
            self.capture(session, [
                ({"script": script, "full": True}, 0),
                ({"script": 'text("done")', "full": False}, 0),
                ({"script": 'text({exit_code:0})', "full": True}, 0),
            ], day)
        review = self.review()
        self.assertEqual([row["actions"] for row in review["candidates"]],
                         [[{"tool": "exec", "script": script}]])
        self.assertEqual([row["unknown_outcomes"] for row in review["coverage"]], [2, 2])
