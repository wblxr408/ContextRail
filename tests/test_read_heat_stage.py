"""G07 consumer contracts for read heat, optional prefetch and protected evidence."""
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from contextrail import Compiler, Scope, Selection, Store, Target
from contextrail.accounting import HostAccounting
from contextrail.errors import NotFound
from contextrail.host import ContextTools
from contextrail.models import Ref, SelectionGroup
from contextrail.project import load_project


DAY = 86400


class ReadHeatTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "rail.sqlite"
        self.now = 1_790_000_000
        self.store = Store(self.path, clock=lambda: self.now)
        self.addCleanup(self.store.close)
        self.scope = Scope("local", "project", "branch", "heat")
        self.lease = self.store.create_task(self.scope, Target("owner", "host", "external"),
                                            "Keep the current constraints accessible.",
                                            allowed_providers=("host",))
        self.tools = ContextTools(self.store, self.scope, "owner")
        self.meter = HostAccounting(self.store, self.scope, "owner")

    def put(self, name, length=1500):
        return self.store.put(self.scope, self.lease, name, (name[0] * length).encode(),
                              expected_revision=0)

    def read(self, ref, observation):
        return self.meter.call_tool(self.tools, "context.get", asdict(ref), observation_id=observation)

    def advice(self, snapshot):
        return self.tools.call("context.read_heat", {"snapshot": snapshot, "budget": 16384})

    def test_observed_read_reorders_navigation_and_prefetch_under_the_same_budget(self):
        required = self.put("required", 10)
        earlier = self.put("a-reference")
        hot = self.put("z-reference")
        snapshot = self.store.snapshot(self.scope, self.lease, (
            Selection(required, required=True), Selection(earlier), Selection(hot)))
        self.read(Ref(hot.name, hot.revision, 10, 80), "cold-window-1")
        self.read(Ref(hot.name, hot.revision, 60, 100), "cold-window-2")
        self.read(Ref(hot.name, hot.revision, 60, 100), "cold-window-2")

        # Use an independent connection to exercise durable receipts.
        with Store(self.path, clock=lambda: self.now) as reopened:
            reader = ContextTools(reopened, self.scope, "owner")
            found = reader.call("context.list", {"snapshot": snapshot, "prefer_frequent_reads": True})
            self.assertEqual([row["ref"]["name"] for row in found["entries"]], [hot.name, earlier.name])
            compiler = Compiler(reopened)
            original = compiler.compile(self.scope, snapshot, "owner", budget=3000)
            packet = compiler.compile(self.scope, snapshot, "owner", budget=3000,
                                      prefer_frequent_reads=True)
        self.assertEqual(original.included, (required, earlier))
        self.assertEqual(packet.included, (required, hot))
        self.assertEqual(packet.omitted, (earlier,))
        self.assertLessEqual(packet.units, 3000)
        rows = {row["ref"]["name"]: row for row in self.advice(snapshot)["entries"]}
        self.assertEqual(rows[hot.name]["observed_reads"], 2)
        self.assertEqual(rows[earlier.name]["heat_state"], "unobserved")

    def test_old_signal_can_suggest_review_only_and_never_cools_live_sources(self):
        source = self.put("constraint-source", 100)
        mandatory = self.put("mandatory", 100)
        grouped = self.put("required-group-member", 100)
        low = self.put("old-optional", 100)
        bridge = self.put("bridge/task/exports/v1/sources/obligation", 100)
        self.store.add_task_state(self.scope, self.lease, kind="constraint",
                                  text="Retain the exact initial condition.", sources=(source,))
        for ref in (source, mandatory, grouped, low, bridge):
            self.read(ref, ref.name)
        self.now += 31 * DAY
        snapshot = self.store.snapshot(self.scope, self.lease, (
            Selection(source), Selection(mandatory, required=True), Selection(grouped),
            Selection(low), Selection(bridge)),
            groups=(SelectionGroup("source-dependency", (grouped,), required=True),))
        page = self.advice(snapshot)
        rows = {row["ref"]["name"]: row for row in page["entries"]}
        self.assertEqual(rows[low.name]["suggestion"], "review_for_deprioritization_or_merge")
        for ref in (source, mandatory, grouped, bridge):
            self.assertEqual(rows[ref.name]["suggestion"], "protected")
            self.assertEqual(self.read(ref, "recover-" + ref.name)["content"], ref.name[0] * 100)
        self.assertEqual(self.read(low, "recover-low")["content"], "o" * 100)

    def test_only_delivered_bytes_heat_overlapping_ranges_of_the_exact_revision(self):
        long = self.put("a-omitted", 800)
        short = self.put("z-delivered", 120)
        hit = Ref(short.name, short.revision, 0, 30)
        disjoint = Ref(short.name, short.revision, 30, 100)
        new = self.store.put(self.scope, self.lease, short.name, b"b" * 120, expected_revision=1)
        snapshot = self.store.snapshot(self.scope, self.lease, (
            Selection(long), Selection(hit, current=False), Selection(disjoint, current=False), Selection(new)))
        batch = self.meter.call_tool(self.tools, "context.batch", {
            "calls": [{"id": "a", "tool": "context.get", "arguments": asdict(long)},
                      {"id": "z", "tool": "context.get", "arguments": asdict(hit)}],
            "concurrency": 1, "per_call_bytes": 1300, "budget": 1000,
        }, observation_id="metered-batch")
        self.assertEqual([entry["delivery"] for entry in batch["results"]], ["omitted", "full"])
        with self.assertRaises(NotFound):
            self.read(Ref(short.name, 3), "missing")
        rows = {Ref(**row["ref"]): row for row in self.advice(snapshot)["entries"]}
        self.assertEqual(rows[hit]["observed_reads"], 1)
        self.assertEqual({rows[ref]["observed_reads"] for ref in (long, disjoint, new)}, {None})

    def test_optional_group_prefetch_keeps_all_members_and_owner_priority(self):
        required = self.put("required", 8)
        left = self.put("a-first", 700)
        right = self.put("a-second", 700)
        hot = self.put("z-hot", 700)
        related = self.put("z-related", 700)
        selections = (Selection(required, required=True), Selection(left), Selection(right),
                      Selection(hot), Selection(related))
        self.read(hot, "hot-group-evidence")
        groups = (SelectionGroup("a-group", (left, right)), SelectionGroup("z-group", (hot, related)))
        compiler = Compiler(self.store)
        snap = self.store.snapshot(self.scope, self.lease, selections, groups=groups)
        packet = compiler.compile(self.scope, snap, "owner", budget=3400, prefer_frequent_reads=True)
        self.assertEqual(set(packet.included), {required, hot, related})
        self.assertEqual(set(packet.omitted), {left, right})
        self.assertLessEqual(packet.units, 3400)
        favored = (SelectionGroup("a-group", (left, right), priority=1), groups[1])
        snap = self.store.snapshot(self.scope, self.lease, selections, groups=favored)
        packet = compiler.compile(self.scope, snap, "owner", budget=3400, prefer_frequent_reads=True)
        self.assertEqual(set(packet.included), {required, left, right})

    def test_prefetch_treats_overlapping_groups_as_one_unit_and_uses_ungrouped_priority(self):
        left = self.put("a-left", 750)
        shared = self.put("b-shared", 750)
        right = self.put("c-right", 750)
        solo = self.put("z-ungrouped", 750)
        self.read(left, "prefer-overlap")
        groups = (SelectionGroup("first", (left, shared)), SelectionGroup("second", (shared, right)))
        snapshot = self.store.snapshot(self.scope, self.lease, (
            Selection(left), Selection(shared), Selection(right), Selection(solo, priority=1)), groups=groups)
        compiler = Compiler(self.store)
        packet = compiler.compile(self.scope, snapshot, "owner", budget=3300, prefer_frequent_reads=True)
        self.assertEqual(packet.included, (solo,))
        self.assertEqual(set(packet.omitted), {left, shared, right})
        self.assertLessEqual(packet.units, 3300)

    def test_mcp_read_drives_terminal_prefetch_after_reconnect(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        workspace = self.path.parent
        config = load_project(workspace, overrides={"store": str(self.path), "task": "mcp-heat"})
        owner = self.store.create_task(config.scope, Target("owner", "host", "external"),
                                       "Find the frequently read reference.", allowed_providers=("host",))
        cold = self.store.put(config.scope, owner, "a-cold", b"cold" * 375, expected_revision=0)
        hot = self.store.put(config.scope, owner, "z-hot", b"hot!" * 375, expected_revision=0)
        snapshot = self.store.snapshot(config.scope, owner, (Selection(cold), Selection(hot)))
        args = ["--workspace", str(workspace), "--task", "mcp-heat", "--store", str(self.path),
                "--session", "owner"]

        async def observed_read():
            params = StdioServerParameters(command=sys.executable, args=["-m", "contextrail", "mcp", *args])
            async with stdio_client(params) as (source, sink):
                async with ClientSession(source, sink) as session:
                    await session.initialize()
                    reply = await session.call_tool("context.get", {"name": hot.name, "revision": 1, "end": 16})
                    self.assertEqual(json.loads(reply.content[0].text)["content"], "hot!" * 4)
                    report = await session.call_tool("context.read_heat", {"snapshot": snapshot})
                    rows = {row["ref"]["name"]: row for row in json.loads(report.content[0].text)["entries"]}
                    self.assertEqual(rows[hot.name]["observed_reads"], 1)
                    self.assertIsNone(rows[cold.name]["observed_reads"])

        asyncio.run(asyncio.wait_for(observed_read(), 25))
        run = subprocess.run(
            [sys.executable, "-m", "contextrail", "continue", *args, "--snapshot", snapshot,
             "--budget", "2800", "--prefer-frequent-reads"],
            capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual([item["ref"]["name"] for item in result["context"]["evidence"]], [hot.name])
        self.assertEqual([item["ref"]["name"] for item in result["context"]["available"]], [cold.name])
        self.assertIn("--prefer-frequent-reads", result["resume"]["argv"])
