from pathlib import Path
import tempfile
import unittest

from contextrail import Compiler, Scope, Selection, Store, Target


class RailTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.sqlite3"
        self.now = 1000.0
        self.store = Store(self.path, clock=lambda: self.now)
        self.addCleanup(self.store.close)
        self.scope = Scope("tenant", "project", "branch", "task")
        self.a = Target("session-a", "provider-a", "model-a")
        self.b = Target("session-b", "provider-b", "model-b")
        self.lease = self.store.create_task(self.scope, self.a, "Exact evidence handoff",
                                            constraints=("Keep original constraints",),
                                            acceptance=("Pass exact evidence checks",),
                                            allowed_providers=("provider-a", "provider-b"))
        self.compiler = Compiler(self.store)

    def put(self, name="source", content=b"exact original evidence", **kwargs):
        return self.store.put(self.scope, self.lease, name, content, expected_revision=kwargs.pop("expected_revision", 0), **kwargs)

    def snapshot(self, *selections):
        return self.store.snapshot(self.scope, self.lease, selections)

    def transfer(self, target):
        sid = self.snapshot()
        h = self.store.prepare(self.scope, self.lease, sid, target)
        packet = self.compiler.for_handoff(self.scope, h.id, budget=10000)
        self.store.acknowledge(self.scope, h.id, target.session, packet.sha256)
        self.store.validate(self.scope, h.id)
        self.lease = self.store.activate(self.scope, h.id)
        return h.id
