"""Read-only terminal navigation with explicit, task-bound resume cursors."""

from dataclasses import asdict
import json
import sys

from .bridge import BridgeEvidence, MANIFEST_TYPE
from .context import Compiler
from .errors import RailError
from .host import ContextTools
from .models import IndexPolicy, Ref, integer


class TaskNavigation:
    def __init__(self, store, config, session):
        self.store, self.config, self.scope, self.session = store, config, config.scope, session
        store.session(self.scope, session)

    def _resume(self, sid):
        argv = [sys.executable, "-m", "contextrail", "continue", "--workspace", str(self.config.workspace),
                "--store", str(self.config.store), "--project-id", self.scope.project, "--task", self.scope.task,
                "--session", self.session, "--snapshot", sid]
        if self.config.config:
            argv += ["--config", str(self.config.config)]
        return {"argv": argv, "powershell": "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in argv)}

    def _source(self, sid):
        row = self.store.db.execute("SELECT metadata FROM events WHERE scope=? AND kind IN ('context.published','context.refreshed') "
                                    "AND json_extract(metadata,'$.snapshot')=? ORDER BY sequence DESC LIMIT 1",
                                    (self.scope.key, sid)).fetchone()
        if row is None:
            return {"kind": "rail", "source_current": "not_checked"}
        ref = Ref(**json.loads(row["metadata"])["source_ref"])
        artifact = self.store.get(self.scope, ref)
        if artifact.media_type != MANIFEST_TYPE:
            return {"kind": "derived_context", "ref": asdict(ref), "source_current": "not_checked"}
        manifest = json.loads(artifact.content)
        return {"kind": "bridge", "ref": asdict(ref), "bridge_snapshot": manifest["snapshot_id"],
                "bridge_scope": manifest["bridge_scope"], "source_current": "not_checked"}

    def status(self):
        with self.store.read_transaction():
            self.store.session(self.scope, self.session)
            task = self.store.task(self.scope)
            total = self.store.db.execute("SELECT COUNT(*) FROM snapshots WHERE scope=?", (self.scope.key,)).fetchone()[0]
            current = self.store.db.execute("SELECT COUNT(*) FROM snapshots WHERE scope=? AND version=?",
                                            (self.scope.key, task["version"])).fetchone()[0]
            return {"scope": asdict(self.scope), "session": self.session, "task": task,
                    "checkpoints": {"total": total, "store_current": current, "source_current": "not_checked"},
                    "resume_requires": "explicit_snapshot", "workspace": str(self.config.workspace), "store": str(self.config.store)}

    def checkpoints(self, *, offset=0, limit=20):
        integer(offset)
        integer(limit, 1)
        if limit > 100:
            from .errors import InvalidRequest
            raise InvalidRequest("Checkpoint page size cannot exceed 100.")
        with self.store.read_transaction():
            self.store.session(self.scope, self.session)
            task = self.store.task(self.scope)
            total = self.store.db.execute("SELECT COUNT(*) FROM snapshots WHERE scope=?", (self.scope.key,)).fetchone()[0]
            rows = self.store.db.execute("SELECT id,version FROM snapshots WHERE scope=? ORDER BY rowid DESC LIMIT ? OFFSET ?",
                                         (self.scope.key, limit, offset)).fetchall()
            entries = []
            for row in rows:
                try:
                    source = self._source(row["id"])
                except RailError as exc:
                    source = {"kind": "unavailable", "error": {"code": exc.code, "message": str(exc)}}
                entries.append({"snapshot": row["id"], "task_version": row["version"],
                                "store_current": row["version"] == task["version"], "source": source,
                                "resume": self._resume(row["id"])})
            return {"scope": asdict(self.scope), "offset": offset, "limit": limit, "total": total,
                    "entries": entries, "next_offset": offset + len(entries) if offset + len(entries) < total else None}

    def search(self, query, *, limit=20):
        result = ContextTools(self.store, self.scope, self.session).call("context.search", {"query": query, "limit": limit})
        return {**result, "source_current": "not_checked", "historical_exports_may_match": True}

    def resume(self, sid, *, budget=8192):
        # Continue never chooses another cursor, creates a chat or acquires a lease.
        source = self._source(sid)
        if source["kind"] == "bridge":
            result = BridgeEvidence(self.store, self.scope, self.session).packet(sid, budget=budget)
        else:
            packet = Compiler(self.store).compile(self.scope, sid, self.session, budget=budget, index=IndexPolicy(max_entries=5))
            result = {"snapshot": sid, "context": json.loads(packet.body), "units": packet.units,
                      "unit": packet.unit, "budget": packet.budget}
        return {**result, "resume": self._resume(sid)}
