"""Local SQLite evidence store and transactional ownership boundary.

Authentication and authorization are enforced by the authenticated host boundary;
the Store exposes the durable membership data used by that boundary. It is not an
identity provider, encrypted secrets vault, or OS sandbox.
"""

from contextlib import contextmanager
from dataclasses import asdict
import json
import math
from pathlib import Path
import sqlite3
import time
from typing import Callable, Iterator
import uuid

from .errors import AccessDenied, Conflict, IntegrityError, InvalidRequest, NotFound, StaleSnapshot, Unavailable
from .models import (Artifact, Compaction, Handoff, Lease, Ref, RequestUsage, Scope, Selection, Summary,
                     Target, TaskStateItem, UsageRecord, canonical, digest, identifier, integer, unicode_text)


SCHEMA_V1 = """
CREATE TABLE tasks (
 scope TEXT PRIMARY KEY, identity TEXT NOT NULL, objective TEXT NOT NULL,
 constraints_json TEXT NOT NULL, acceptance_json TEXT NOT NULL, providers_json TEXT NOT NULL,
 version INTEGER NOT NULL, owner TEXT NOT NULL, epoch INTEGER NOT NULL, handoff TEXT
);
CREATE TABLE sessions (
 scope TEXT NOT NULL REFERENCES tasks(scope), id TEXT NOT NULL, provider TEXT NOT NULL,
 model TEXT NOT NULL, PRIMARY KEY(scope,id)
);
CREATE TABLE blobs (
 scope TEXT NOT NULL REFERENCES tasks(scope), sha256 TEXT NOT NULL, content BLOB NOT NULL,
 PRIMARY KEY(scope,sha256)
);
CREATE TABLE artifacts (
 scope TEXT NOT NULL REFERENCES tasks(scope), name TEXT NOT NULL, revision INTEGER NOT NULL,
 sha256 TEXT NOT NULL, size INTEGER NOT NULL, media_type TEXT NOT NULL,
 expires REAL, invalidated INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(scope,name,revision)
);
CREATE TABLE heads (
 scope TEXT NOT NULL REFERENCES tasks(scope), name TEXT NOT NULL, revision INTEGER NOT NULL,
 PRIMARY KEY(scope,name)
);
CREATE TABLE snapshots (
 scope TEXT NOT NULL REFERENCES tasks(scope), id TEXT NOT NULL, version INTEGER NOT NULL,
 body TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(scope,id)
);
CREATE TABLE handoffs (
 scope TEXT NOT NULL REFERENCES tasks(scope), id TEXT NOT NULL, snapshot TEXT NOT NULL,
 source TEXT NOT NULL, epoch INTEGER NOT NULL, target TEXT NOT NULL, phase TEXT NOT NULL,
 packet_sha256 TEXT, receipt_sha256 TEXT, PRIMARY KEY(scope,id)
);
CREATE TABLE actions (
 scope TEXT NOT NULL REFERENCES tasks(scope), id TEXT NOT NULL, state TEXT NOT NULL,
 PRIMARY KEY(scope,id)
);
CREATE TABLE events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL REFERENCES tasks(scope),
 kind TEXT NOT NULL, at REAL NOT NULL, metadata TEXT NOT NULL
);
"""
SCHEMA_V2 = """
CREATE TABLE identities (
 id TEXT PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL,
 email TEXT, display_name TEXT, created_at REAL NOT NULL,
 UNIQUE(issuer,subject)
);
CREATE TABLE tenants (
 id TEXT PRIMARY KEY, created_at REAL NOT NULL
);
CREATE TABLE projects (
 tenant_id TEXT NOT NULL REFERENCES tenants(id), id TEXT NOT NULL,
 created_at REAL NOT NULL, PRIMARY KEY(tenant_id,id)
);
CREATE TABLE tenant_memberships (
 tenant_id TEXT NOT NULL REFERENCES tenants(id), identity_id TEXT NOT NULL REFERENCES identities(id),
 role TEXT NOT NULL CHECK(role IN ('owner','admin','member')), created_at REAL NOT NULL,
 PRIMARY KEY(tenant_id,identity_id)
);
CREATE TABLE project_memberships (
 tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, identity_id TEXT NOT NULL REFERENCES identities(id),
 role TEXT NOT NULL CHECK(role IN ('admin','editor','viewer')), created_at REAL NOT NULL,
 PRIMARY KEY(tenant_id,project_id,identity_id),
 FOREIGN KEY(tenant_id,project_id) REFERENCES projects(tenant_id,id)
);
CREATE TABLE authorization_events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, principal_digest TEXT NOT NULL,
 tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, action TEXT NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('allow','deny')), at REAL NOT NULL
);
"""
SCHEMA_V3 = """
CREATE TABLE usage_records (
 scope TEXT NOT NULL REFERENCES tasks(scope), id TEXT NOT NULL, session TEXT NOT NULL,
 provider TEXT NOT NULL, model TEXT NOT NULL, packet_sha256 TEXT NOT NULL, unit TEXT NOT NULL,
 input_units INTEGER NOT NULL, cached_input_units INTEGER NOT NULL, cache_write_units INTEGER NOT NULL,
 output_units INTEGER NOT NULL, latency_ms INTEGER NOT NULL, at REAL NOT NULL,
 PRIMARY KEY(scope,id), FOREIGN KEY(scope,session) REFERENCES sessions(scope,id)
);
CREATE TABLE compactions (
 scope TEXT NOT NULL REFERENCES tasks(scope), id TEXT NOT NULL, snapshot TEXT NOT NULL,
 session TEXT NOT NULL, reason TEXT NOT NULL, phase TEXT NOT NULL, summary_id TEXT,
 PRIMARY KEY(scope,id), FOREIGN KEY(scope,snapshot) REFERENCES snapshots(scope,id),
 FOREIGN KEY(scope,session) REFERENCES sessions(scope,id)
);
CREATE TABLE summaries (
 scope TEXT NOT NULL REFERENCES tasks(scope), id TEXT NOT NULL, snapshot TEXT NOT NULL,
 title TEXT NOT NULL, text TEXT NOT NULL, spans_json TEXT NOT NULL, sha256 TEXT NOT NULL,
 created_at REAL NOT NULL, PRIMARY KEY(scope,id), FOREIGN KEY(scope,snapshot) REFERENCES snapshots(scope,id)
);
CREATE INDEX summaries_scope_snapshot ON summaries(scope,snapshot);
CREATE INDEX usage_records_scope_session ON usage_records(scope,session,at);
"""
SCHEMA_V4 = """
CREATE TABLE request_usage (
 scope TEXT NOT NULL REFERENCES tasks(scope), id TEXT NOT NULL, session TEXT NOT NULL,
 provider TEXT NOT NULL, model TEXT NOT NULL, model_revision TEXT, request_id TEXT,
 run_id TEXT, attempt INTEGER NOT NULL, packet_sha256 TEXT NOT NULL, snapshot TEXT NOT NULL,
 unit TEXT NOT NULL, input_units INTEGER, cached_input_units INTEGER, cache_write_units INTEGER,
 output_units INTEGER, latency_ms INTEGER, usage_known INTEGER NOT NULL, at REAL NOT NULL,
 PRIMARY KEY(scope,id), FOREIGN KEY(scope,session) REFERENCES sessions(scope,id),
 FOREIGN KEY(scope,snapshot) REFERENCES snapshots(scope,id)
);
CREATE UNIQUE INDEX request_usage_provider_request ON request_usage(scope,provider,request_id)
 WHERE request_id IS NOT NULL;
CREATE INDEX request_usage_scope_session ON request_usage(scope,session,at);
"""
SCHEMA_V5 = """
ALTER TABLE request_usage ADD COLUMN request_digest TEXT;
CREATE INDEX request_usage_scope_request_digest ON request_usage(scope,request_digest);
"""
SCHEMA_V6 = """
CREATE TABLE task_state_items (
 scope TEXT NOT NULL REFERENCES tasks(scope), id TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
 sources_json TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('active','archived','superseded')),
 inferred INTEGER NOT NULL, supersedes_json TEXT NOT NULL, created_at REAL NOT NULL,
 PRIMARY KEY(scope,id)
);
CREATE INDEX task_state_items_scope_status ON task_state_items(scope,status,created_at);
"""
APPLICATION_ID = 0x43524C31
CORE_SCHEMA_VERSION = 6
SUPPORTED_SCHEMA_VERSIONS = frozenset({1, 2, 3, 4, 5, 6})


class Store:
    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.db = sqlite3.connect(str(path), timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        # One connection per thread. BEGIN IMMEDIATE serializes writers across processes.
        try:
            with self.transaction():
                version = self.db.execute("PRAGMA user_version").fetchone()[0]
                application_id = self.db.execute("PRAGMA application_id").fetchone()[0]
                if version == 0:
                    if application_id != 0 or self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                        raise Conflict("Refusing to initialize a non-ContextRail database.")
                    self._apply_schema(SCHEMA_V1)
                    self._apply_schema(SCHEMA_V3)
                    self._apply_schema(SCHEMA_V4)
                    self._apply_schema(SCHEMA_V5)
                    self._apply_schema(SCHEMA_V6)
                    self.db.execute(f"PRAGMA user_version={CORE_SCHEMA_VERSION}")
                    self.db.execute(f"PRAGMA application_id={APPLICATION_ID}")
                elif application_id != APPLICATION_ID:
                    raise Conflict("Unsupported database schema version.")
                elif version not in SUPPORTED_SCHEMA_VERSIONS:
                    raise Conflict("Unsupported database schema version.")
                elif version < CORE_SCHEMA_VERSION:
                    if version < 3:
                        self._apply_schema(SCHEMA_V3)
                    if version < 4:
                        self._apply_schema(SCHEMA_V4)
                    if version < 5:
                        self._apply_schema(SCHEMA_V5)
                    if version < 6:
                        self._apply_schema(SCHEMA_V6)
                    self.db.execute(f"PRAGMA user_version={CORE_SCHEMA_VERSION}")
        except Exception:
            self.db.close()
            raise

    def _apply_schema(self, schema: str) -> None:
        # Executed statement-by-statement (not sqlite3.executescript) on purpose:
        # executescript implicitly COMMITs the open migration transaction, which
        # would break the atomic init/upgrade in __init__. The schema constants
        # must therefore contain no ';' inside string literals or trigger bodies;
        # if a future migration needs one, split it into its own statement rather
        # than relaxing this loop.
        for statement in schema.split(";"):
            if statement.strip():
                self.db.execute(statement)

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    @contextmanager
    def read_transaction(self) -> Iterator[None]:
        # A read-only view over a single consistent snapshot. BEGIN DEFERRED takes
        # a shared lock, so pure reads (compile, search) neither serialize behind
        # each other on a write lock nor observe a mid-flight invalidate between
        # the outer SELECT and a per-row get. A concurrent writer still blocks
        # until this read commits, keeping the snapshot consistent.
        self.db.execute("BEGIN DEFERRED")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _task(self, scope: Scope) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM tasks WHERE scope=?", (scope.key,)).fetchone()
        if row is None:
            raise NotFound("Task not found in this scope.")
        return row

    def task(self, scope: Scope) -> dict:
        row = self._task(scope)
        return {"objective": row["objective"], "constraints": json.loads(row["constraints_json"]),
                "acceptance": json.loads(row["acceptance_json"]), "version": row["version"],
                "owner": row["owner"], "epoch": row["epoch"], "handoff": row["handoff"]}

    def _event(self, scope: Scope, kind: str, **metadata: object) -> None:
        self.db.execute("INSERT INTO events(scope,kind,at,metadata) VALUES(?,?,?,?)",
                        (scope.key, kind, self.clock(), canonical(metadata)))

    def events(self, scope: Scope) -> list[dict]:
        self._task(scope)
        return [{"sequence": r["sequence"], "kind": r["kind"], "at": r["at"],
                 "metadata": json.loads(r["metadata"])} for r in self.db.execute(
                     "SELECT * FROM events WHERE scope=? ORDER BY sequence", (scope.key,))]

    @staticmethod
    def _text_list(values: tuple[str, ...]) -> list[str]:
        if not isinstance(values, (tuple, list)) or any(not isinstance(v, str) or not v.strip() for v in values):
            raise InvalidRequest("Expected a list of nonempty strings.")
        for value in values:
            unicode_text(value)
        return list(values)

    def create_task(self, scope: Scope, target: Target, objective: str, *,
                    constraints: tuple[str, ...] = (), acceptance: tuple[str, ...] = (),
                    allowed_providers: tuple[str, ...]) -> Lease:
        if not isinstance(objective, str) or not objective.strip():
            raise InvalidRequest("Objective must be nonempty.")
        unicode_text(objective)
        providers = sorted(set(self._text_list(allowed_providers)))
        constraints_json = canonical(self._text_list(constraints))
        acceptance_json = canonical(self._text_list(acceptance))
        if target.provider not in providers:
            raise AccessDenied("Provider is not approved for this task.")
        with self.transaction():
            if self.db.execute("SELECT 1 FROM tasks WHERE scope=?", (scope.key,)).fetchone():
                raise Conflict("Task already exists.")
            self.db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,0,?,0,NULL)",
                            (scope.key, canonical(asdict(scope)), objective, constraints_json,
                             acceptance_json, canonical(providers), target.session))
            self.db.execute("INSERT INTO sessions VALUES(?,?,?,?)",
                            (scope.key, target.session, target.provider, target.model))
            self._event(scope, "task.created")
        return Lease(target.session, 0)

    def _lease(self, scope: Scope, lease: Lease, *, frozen_ok: bool = False) -> sqlite3.Row:
        task = self._task(scope)
        if task["owner"] != lease.session or task["epoch"] != lease.epoch:
            raise AccessDenied("The session lease is stale or is not the task owner.")
        if task["handoff"] is not None and not frozen_ok:
            raise Conflict("Task writes are frozen during handoff.")
        return task

    def update_task(self, scope: Scope, lease: Lease, *, expected_version: int, objective: str,
                    constraints: tuple[str, ...], acceptance: tuple[str, ...]) -> int:
        """Explicit host-authored requirement revision, invalidating older snapshots."""
        integer(expected_version)
        if not isinstance(objective, str) or not objective.strip():
            raise InvalidRequest("Objective must be nonempty.")
        unicode_text(objective)
        constraints_json = canonical(self._text_list(constraints))
        acceptance_json = canonical(self._text_list(acceptance))
        with self.transaction():
            task = self._lease(scope, lease)
            if task["version"] != expected_version:
                raise Conflict("Task state changed; reload before updating requirements.")
            self.db.execute("UPDATE tasks SET objective=?,constraints_json=?,acceptance_json=?,version=version+1 WHERE scope=?",
                            (objective, constraints_json, acceptance_json, scope.key))
            self._event(scope, "task.updated", version=expected_version + 1)
        return expected_version + 1

    def session(self, scope: Scope, session: str) -> Target:
        task = self._task(scope)
        row = self.db.execute("SELECT * FROM sessions WHERE scope=? AND id=?", (scope.key, session)).fetchone()
        if row is None or row["provider"] not in json.loads(task["providers_json"]):
            raise AccessDenied("Session is not approved for this scope.")
        return Target(row["id"], row["provider"], row["model"])

    def permit_target(self, scope: Scope, target: Target) -> None:
        """Validate a routing candidate without registering or activating it.

        Registration belongs to the existing handoff transaction, after a host
        has actually chosen to switch.  This method prevents a policy callback
        from proposing a disallowed provider or rebinding a known session.
        """
        if not isinstance(target, Target):
            raise InvalidRequest("Expected a routing target.")
        task = self._task(scope)
        if target.provider not in json.loads(task["providers_json"]):
            raise AccessDenied("Provider is not approved for this task.")
        existing = self.db.execute("SELECT * FROM sessions WHERE scope=? AND id=?", (scope.key, target.session)).fetchone()
        if existing and (existing["provider"] != target.provider or existing["model"] != target.model):
            raise Conflict("Session identity cannot be rebound to another model.")

    def record_routing_decision(self, scope: Scope, *, purpose: str, target: Target,
                                reason: str, estimated_input_units: int) -> None:
        """Audit a policy result without changing ownership or opening a session."""
        identifier(purpose)
        identifier(reason)
        integer(estimated_input_units)
        with self.transaction():
            self.permit_target(scope, target)
            self._event(scope, "routing.decided", purpose=purpose, target=asdict(target), reason=reason,
                        estimated_input_units=estimated_input_units)

    def _register(self, scope: Scope, target: Target) -> None:
        if target.provider not in json.loads(self._task(scope)["providers_json"]):
            raise AccessDenied("Provider is not approved for this task.")
        existing = self.db.execute("SELECT * FROM sessions WHERE scope=? AND id=?", (scope.key, target.session)).fetchone()
        if existing and (existing["provider"] != target.provider or existing["model"] != target.model):
            raise Conflict("Session identity cannot be rebound to another model.")
        self.db.execute("INSERT OR IGNORE INTO sessions VALUES(?,?,?,?)",
                        (scope.key, target.session, target.provider, target.model))

    def put(self, scope: Scope, lease: Lease, name: str, content: bytes, *, expected_revision: int,
            media_type: str = "text/plain; charset=utf-8", expires_at: float | None = None) -> Ref:
        identifier(name)
        identifier(media_type)
        integer(expected_revision)
        if not isinstance(content, bytes):
            raise InvalidRequest("Artifact content must be bytes.")
        if expires_at is not None and (type(expires_at) not in (float, int) or not math.isfinite(expires_at)
                                       or expires_at <= self.clock()):
            raise InvalidRequest("Expiry must be a finite future timestamp.")
        sha = digest(content)
        with self.transaction():
            self._lease(scope, lease)
            current = self.db.execute("SELECT revision FROM heads WHERE scope=? AND name=?", (scope.key, name)).fetchone()
            if (current[0] if current else 0) != expected_revision:
                raise Conflict("Artifact head changed; reload before writing.")
            revision = expected_revision + 1
            self.db.execute("INSERT OR IGNORE INTO blobs VALUES(?,?,?)", (scope.key, sha, content))
            self.db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,0)",
                            (scope.key, name, revision, sha, len(content), media_type, expires_at))
            self.db.execute("INSERT INTO heads VALUES(?,?,?) ON CONFLICT(scope,name) DO UPDATE SET revision=excluded.revision",
                            (scope.key, name, revision))
            self.db.execute("UPDATE tasks SET version=version+1 WHERE scope=?", (scope.key,))
            self._event(scope, "artifact.created", revision=revision, bytes=len(content))
        return Ref(name, revision)

    def get(self, scope: Scope, ref: Ref) -> Artifact:
        self._task(scope)
        row = self.db.execute("SELECT a.*,b.content FROM artifacts a LEFT JOIN blobs b ON a.scope=b.scope AND a.sha256=b.sha256 "
                              "WHERE a.scope=? AND a.name=? AND a.revision=?", (scope.key, ref.name, ref.revision)).fetchone()
        if row is None:
            raise NotFound("Artifact not found in this scope.")
        if row["invalidated"] or (row["expires"] is not None and row["expires"] <= self.clock()):
            raise Unavailable("Artifact expired or was invalidated.")
        content = row["content"]
        if content is None or digest(content) != row["sha256"] or len(content) != row["size"]:
            raise IntegrityError("Artifact integrity check failed.")
        end = row["size"] if ref.end is None else ref.end
        if end > row["size"] or ref.start > end:
            raise InvalidRequest("Evidence byte range is outside the artifact.")
        return Artifact(ref, row["sha256"], row["size"], row["media_type"], content[ref.start:end])

    def search(self, scope: Scope, query: str, *, limit: int = 20) -> list[Ref]:
        if not isinstance(query, str) or not query.strip():
            raise InvalidRequest("Search query must be nonempty.")
        unicode_text(query)
        integer(limit, 1)
        if limit > 100:
            raise InvalidRequest("Search limit exceeds 100.")
        self._task(scope)
        # Literal substring search: predictable local baseline, not semantic retrieval.
        rows = self.db.execute("SELECT a.name,a.revision FROM heads h JOIN artifacts a "
                               "ON h.scope=a.scope AND h.name=a.name AND h.revision=a.revision "
                               "WHERE a.scope=? AND a.invalidated=0 AND (a.expires IS NULL OR a.expires>?) ORDER BY a.name",
                               (scope.key, self.clock()))
        results = []
        for row in rows:
            artifact = self.get(scope, Ref(row["name"], row["revision"]))
            if query.casefold() in row["name"].casefold() or query.casefold() in artifact.content.decode("utf-8", errors="replace").casefold():
                results.append(Ref(row["name"], row["revision"]))
                if len(results) == limit:
                    break
        return results

    def current_refs(self, scope: Scope, *, limit: int = 10_000) -> list[Ref]:
        """Return readable current artifact heads in a stable order.

        This is intentionally metadata-only.  Callers that want relevance
        ranking must still load the returned refs through ``get`` so integrity,
        expiry, invalidation, and scope checks remain on the normal read path.
        """
        integer(limit, 1)
        if limit > 10_000:
            raise InvalidRequest("Current artifact limit exceeds 10000.")
        self._task(scope)
        rows = self.db.execute(
            "SELECT a.name,a.revision FROM heads h JOIN artifacts a "
            "ON h.scope=a.scope AND h.name=a.name AND h.revision=a.revision "
            "WHERE a.scope=? AND a.invalidated=0 AND (a.expires IS NULL OR a.expires>?) "
            "ORDER BY a.name LIMIT ?",
            (scope.key, self.clock(), limit),
        )
        return [Ref(row["name"], row["revision"]) for row in rows]

    def current_ref_count(self, scope: Scope) -> int:
        """Count readable current artifact heads without materializing content."""
        self._task(scope)
        row = self.db.execute(
            "SELECT COUNT(*) AS count FROM heads h JOIN artifacts a "
            "ON h.scope=a.scope AND h.name=a.name AND h.revision=a.revision "
            "WHERE a.scope=? AND a.invalidated=0 AND (a.expires IS NULL OR a.expires>?)",
            (scope.key, self.clock()),
        ).fetchone()
        return int(row["count"])

    def invalidate(self, scope: Scope, lease: Lease, ref: Ref) -> None:
        with self.transaction():
            self._lease(scope, lease)
            changed = self.db.execute("UPDATE artifacts SET invalidated=1 WHERE scope=? AND name=? AND revision=? AND invalidated=0",
                                      (scope.key, ref.name, ref.revision)).rowcount
            if not changed:
                raise NotFound("Active artifact revision not found.")
            self.db.execute("UPDATE tasks SET version=version+1 WHERE scope=?", (scope.key,))
            self._event(scope, "artifact.invalidated")

    def purge(self, scope: Scope) -> int:
        """Reclaim unreferenced/expired payloads, retaining version tombstones.

        Does not claim forensic erasure from SQLite pages or OS backups.
        """
        with self.transaction():
            self._task(scope)
            count = self.db.execute("DELETE FROM blobs WHERE scope=? AND NOT EXISTS (SELECT 1 FROM artifacts a "
                                    "WHERE a.scope=blobs.scope AND a.sha256=blobs.sha256 AND a.invalidated=0 "
                                    "AND (a.expires IS NULL OR a.expires>?))", (scope.key, self.clock())).rowcount
            self._event(scope, "artifacts.purged", count=count)
        return count

    def snapshot(self, scope: Scope, lease: Lease, selections: tuple[Selection, ...]) -> str:
        if not isinstance(selections, (tuple, list)) or any(not isinstance(s, Selection) for s in selections):
            raise InvalidRequest("Expected evidence selections.")
        if len({s.ref for s in selections}) != len(selections):
            raise InvalidRequest("Duplicate evidence references.")
        with self.transaction():
            task = self._lease(scope, lease)
            evidence = []
            for selection in selections:
                artifact = self.get(scope, selection.ref)
                if selection.current:
                    self._check_head(scope, selection.ref)
                evidence.append({**asdict(selection), "sha256": artifact.sha256})
            task_state = [asdict(item) for item in self.task_state(scope)]
            body = canonical({"schema": "contextrail.snapshot/v1", "scope": asdict(scope),
                              "version": task["version"], "objective": task["objective"],
                              "constraints": json.loads(task["constraints_json"]),
                              "acceptance": json.loads(task["acceptance_json"]), "task_state": task_state,
                              "evidence": evidence})
            sid = uuid.uuid4().hex
            self.db.execute("INSERT INTO snapshots VALUES(?,?,?,?,?)",
                            (scope.key, sid, task["version"], body, digest(body.encode("utf-8"))))
            self._event(scope, "snapshot.created", snapshot=sid)
        return sid

    def _check_head(self, scope: Scope, ref: Ref) -> None:
        row = self.db.execute("SELECT revision FROM heads WHERE scope=? AND name=?", (scope.key, ref.name)).fetchone()
        if row is None or row[0] != ref.revision:
            raise StaleSnapshot("Evidence is no longer the current artifact revision.")

    def load_snapshot(self, scope: Scope, sid: str, *, require_current: bool = True) -> dict:
        task = self._task(scope)
        row = self.db.execute("SELECT * FROM snapshots WHERE scope=? AND id=?", (scope.key, sid)).fetchone()
        if row is None:
            raise NotFound("Snapshot not found in this scope.")
        if digest(row["body"].encode("utf-8")) != row["sha256"]:
            raise IntegrityError("Snapshot integrity check failed.")
        if require_current and task["version"] != row["version"]:
            raise StaleSnapshot("Task changed since the snapshot was captured.")
        result = json.loads(row["body"])
        for selection in result["evidence"]:
            ref = Ref(**selection["ref"])
            if self.get(scope, ref).sha256 != selection["sha256"]:
                raise IntegrityError("Snapshot evidence digest mismatch.")
            if require_current and selection["current"]:
                self._check_head(scope, ref)
        return result

    def snapshot_refs(self, scope: Scope, sid: str, *, offset: int = 0, limit: int = 20) -> tuple[list[dict], int]:
        """Page snapshot-bound cold metadata without releasing evidence bytes."""
        integer(offset)
        integer(limit, 1)
        if limit > 100:
            raise InvalidRequest("Snapshot index page size exceeds 100.")
        state = self.load_snapshot(scope, sid)
        # Required evidence is already in every compiled packet. The index is
        # for optional/cold candidates only, preventing a page tool from
        # reintroducing mandatory material as navigation noise.
        entries = [entry for entry in state["evidence"] if not entry["required"]]
        entries.sort(key=lambda e: (not e.get("cache_stable", False), not e["required"],
                                    -e["priority"], canonical(e["ref"])))
        return entries[offset:offset + limit], len(entries)

    @staticmethod
    def _sha256(value: str) -> None:
        if not isinstance(value, str) or len(value) != 64:
            raise InvalidRequest("Expected a SHA-256 hex digest.")
        try:
            int(value, 16)
        except ValueError:
            raise InvalidRequest("Expected a SHA-256 hex digest.") from None

    def record_usage(self, scope: Scope, session: str, *, packet_sha256: str, unit: str,
                     input_units: int, cached_input_units: int = 0, cache_write_units: int = 0,
                     output_units: int = 0, latency_ms: int = 0) -> UsageRecord:
        """Persist host-reported model usage; the core does not invent billing units."""
        self._sha256(packet_sha256)
        identifier(unit)
        for value in (input_units, cached_input_units, cache_write_units, output_units, latency_ms):
            integer(value)
        if cached_input_units > input_units:
            raise InvalidRequest("Cached input units cannot exceed total input units.")
        with self.transaction():
            target = self.session(scope, session)
            usage_id = uuid.uuid4().hex
            at = self.clock()
            self.db.execute("INSERT INTO usage_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (scope.key, usage_id, session, target.provider, target.model, packet_sha256, unit,
                             input_units, cached_input_units, cache_write_units, output_units, latency_ms, at))
            self._event(scope, "usage.recorded", session=session, unit=unit, input_units=input_units,
                        cached_input_units=cached_input_units, cache_write_units=cache_write_units,
                        output_units=output_units)
        return UsageRecord(usage_id, session, target.provider, target.model, packet_sha256, unit, input_units,
                           cached_input_units, cache_write_units, output_units, latency_ms, at)

    def usage_totals(self, scope: Scope, *, session: str | None = None) -> dict:
        self._task(scope)
        if session is not None:
            self.session(scope, session)
        clause = " AND session=?" if session is not None else ""
        values = (scope.key, session) if session is not None else (scope.key,)
        aggregate_columns = ("COUNT(*) AS requests, COALESCE(SUM(input_units),0) AS input_units, "
                             "COALESCE(SUM(cached_input_units),0) AS cached_input_units, "
                             "COALESCE(SUM(cache_write_units),0) AS cache_write_units, "
                             "COALESCE(SUM(output_units),0) AS output_units, "
                             "COALESCE(SUM(latency_ms),0) AS latency_ms")
        legacy = dict(self.db.execute("SELECT " + aggregate_columns + " FROM usage_records WHERE scope=?" + clause,
                                      values).fetchone())
        # Both ledgers must be aggregated. Hosts on the newer request-granular
        # path write to request_usage; summing only usage_records would silently
        # report zero. request_usage keeps absent counts NULL (usage unknown), so
        # COALESCE(...,0) only adds the units a provider actually reported.
        request = dict(self.db.execute("SELECT " + aggregate_columns + ", "
                                       "COALESCE(SUM(usage_known),0) AS usage_known_requests "
                                       "FROM request_usage WHERE scope=?" + clause, values).fetchone())
        combined = {key: legacy.get(key, 0) + request.get(key, 0)
                    for key in ("requests", "input_units", "cached_input_units",
                                "cache_write_units", "output_units", "latency_ms")}
        combined["aggregate_requests"] = legacy["requests"]
        combined["request_ledger_requests"] = request["requests"]
        combined["request_ledger_usage_known"] = request["usage_known_requests"]
        return combined

    def add_task_state(self, scope: Scope, lease: Lease, *, kind: str, text: str,
                       sources: tuple[Ref, ...] = (), inferred: bool = False,
                       supersedes: tuple[str, ...] = ()) -> TaskStateItem:
        """Persist a compact, current-state fact without losing its provenance.

        ``inferred`` items are explicitly marked.  Factual operational items
        require source spans, so a host cannot accidentally turn an unsupported
        model conclusion into a trusted task fact.
        """
        identifier(kind)
        if not isinstance(text, str) or not text.strip():
            raise InvalidRequest("Task state text must be nonempty.")
        unicode_text(text)
        if not isinstance(sources, tuple) or any(not isinstance(ref, Ref) for ref in sources):
            raise InvalidRequest("Task state sources must be a tuple of references.")
        if not isinstance(supersedes, tuple) or any(not isinstance(item, str) for item in supersedes):
            raise InvalidRequest("Superseded task-state IDs must be a tuple.")
        if type(inferred) is not bool:
            raise InvalidRequest("Task state inference flag must be boolean.")
        if kind in {"completed", "decision", "excluded"} and not sources and not inferred:
            raise InvalidRequest("Factual task state requires source evidence or inferred=true.")
        for item in supersedes:
            identifier(item)
        if len(set(sources)) != len(sources) or len(set(supersedes)) != len(supersedes):
            raise InvalidRequest("Duplicate task-state source or superseded ID.")
        with self.transaction():
            self._lease(scope, lease)
            for ref in sources:
                self.get(scope, ref)
            if supersedes:
                rows = self.db.execute("SELECT id,status FROM task_state_items WHERE scope=? AND id IN (" +
                                       ",".join("?" for _ in supersedes) + ")", (scope.key, *supersedes)).fetchall()
                if len(rows) != len(supersedes) or any(row["status"] != "active" for row in rows):
                    raise Conflict("Task-state item to supersede is missing or no longer active.")
                self.db.execute("UPDATE task_state_items SET status='superseded' WHERE scope=? AND id IN (" +
                                ",".join("?" for _ in supersedes) + ")", (scope.key, *supersedes))
            item_id, created = uuid.uuid4().hex, self.clock()
            self.db.execute("INSERT INTO task_state_items VALUES(?,?,?,?,?,?,?,?,?)",
                            (scope.key, item_id, kind, text, canonical([asdict(ref) for ref in sources]), "active",
                             int(inferred), canonical(supersedes), created))
            self.db.execute("UPDATE tasks SET version=version+1 WHERE scope=?", (scope.key,))
            self._event(scope, "task_state.added", item=item_id, item_kind=kind, inferred=inferred,
                        source_count=len(sources), supersedes=len(supersedes))
        return TaskStateItem(item_id, kind, text, sources, "active", inferred, supersedes, created)

    def archive_task_state(self, scope: Scope, lease: Lease, item_id: str) -> None:
        identifier(item_id)
        with self.transaction():
            self._lease(scope, lease)
            changed = self.db.execute("UPDATE task_state_items SET status='archived' WHERE scope=? AND id=? AND status='active'",
                                      (scope.key, item_id)).rowcount
            if not changed:
                raise Conflict("Task-state item is not active.")
            self.db.execute("UPDATE tasks SET version=version+1 WHERE scope=?", (scope.key,))
            self._event(scope, "task_state.archived", item=item_id)

    def task_state(self, scope: Scope, *, active_only: bool = True) -> tuple[TaskStateItem, ...]:
        self._task(scope)
        clause = " AND status='active'" if active_only else ""
        rows = self.db.execute("SELECT * FROM task_state_items WHERE scope=?" + clause + " ORDER BY created_at,id",
                               (scope.key,)).fetchall()
        return tuple(TaskStateItem(row["id"], row["kind"], row["text"],
                                   tuple(Ref(**item) for item in json.loads(row["sources_json"])), row["status"],
                                   bool(row["inferred"]), tuple(json.loads(row["supersedes_json"])), row["created_at"])
                     for row in rows)

    def record_request_usage(self, scope: Scope, session: str, *, packet_sha256: str, snapshot: str,
                             unit: str, input_units: int | None, cached_input_units: int | None = None,
                             cache_write_units: int | None = None, output_units: int | None = None,
                             latency_ms: int | None = None, request_id: str | None = None,
                             run_id: str | None = None, attempt: int = 1,
                             model_revision: str | None = None, request_digest: str | None = None) -> RequestUsage:
        """Record one provider request without converting absent usage to zero."""
        self._sha256(packet_sha256)
        identifier(snapshot)
        identifier(unit)
        integer(attempt, 1)
        for value in (input_units, cached_input_units, cache_write_units, output_units, latency_ms):
            if value is not None:
                integer(value)
        if input_units is not None and cached_input_units is not None and cached_input_units > input_units:
            raise InvalidRequest("Cached input units cannot exceed total input units.")
        for value in (request_id, run_id, model_revision):
            if value is not None:
                identifier(value)
        if request_digest is not None:
            self._sha256(request_digest)
        usage_known = input_units is not None and output_units is not None
        with self.transaction():
            target = self.session(scope, session)
            # A request already happened; the version may have advanced since the
            # packet was compiled (a later put/invalidate/task-state/action bumps
            # it). Recording spent usage must not fail with StaleSnapshot, or the
            # ledger silently loses cost that was really incurred. Integrity and
            # evidence-digest checks in load_snapshot still run.
            self.load_snapshot(scope, snapshot, require_current=False)
            usage_id = uuid.uuid4().hex
            at = self.clock()
            try:
                self.db.execute(
                    "INSERT INTO request_usage(scope,id,session,provider,model,model_revision,request_id,run_id,"
                    "attempt,packet_sha256,snapshot,unit,input_units,cached_input_units,cache_write_units,"
                    "output_units,latency_ms,usage_known,at,request_digest) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (scope.key, usage_id, session, target.provider, target.model, model_revision, request_id,
                     run_id, attempt, packet_sha256, snapshot, unit, input_units, cached_input_units,
                     cache_write_units, output_units, latency_ms, int(usage_known), at, request_digest),
                )
            except sqlite3.IntegrityError as exc:
                if request_id is not None:
                    raise Conflict("Provider request ID was already recorded for this scope.") from exc
                raise
            self._event(scope, "usage.request_recorded", session=session, request_id=request_id,
                        usage_known=usage_known, packet_sha256=packet_sha256)
        return RequestUsage(usage_id, session, target.provider, target.model, model_revision, request_id,
                            run_id, attempt, packet_sha256, request_digest, snapshot, unit, input_units, cached_input_units,
                            cache_write_units, output_units, latency_ms, usage_known, at)

    def begin_compaction(self, scope: Scope, lease: Lease, sid: str, *, reason: str) -> Compaction:
        identifier(sid)
        identifier(reason)
        with self.transaction():
            self._lease(scope, lease)
            self.load_snapshot(scope, sid)
            compaction_id = uuid.uuid4().hex
            self.db.execute("INSERT INTO compactions VALUES(?,?,?,?,?,'prepared',NULL)",
                            (scope.key, compaction_id, sid, lease.session, reason))
            self._event(scope, "compaction.prepared", compaction=compaction_id, snapshot=sid)
        return Compaction(compaction_id, sid, lease.session, reason, "prepared")

    def compaction(self, scope: Scope, compaction_id: str) -> Compaction:
        identifier(compaction_id)
        self._task(scope)
        row = self.db.execute("SELECT * FROM compactions WHERE scope=? AND id=?", (scope.key, compaction_id)).fetchone()
        if row is None:
            raise NotFound("Compaction not found in this scope.")
        return Compaction(row["id"], row["snapshot"], row["session"], row["reason"], row["phase"])

    def _validate_spans(self, scope: Scope, state: dict, spans: tuple[Ref, ...]) -> None:
        if not isinstance(spans, (tuple, list)) or not spans or any(not isinstance(ref, Ref) for ref in spans):
            raise InvalidRequest("A summary must cite one or more evidence spans.")
        if len(set(spans)) != len(spans):
            raise InvalidRequest("Duplicate evidence spans.")
        selected = [Ref(**entry["ref"]) for entry in state["evidence"]]
        for span in spans:
            artifact = self.get(scope, Ref(span.name, span.revision))
            span_end = artifact.size if span.end is None else span.end
            if not any(source.name == span.name and source.revision == span.revision and
                       source.start <= span.start and (artifact.size if source.end is None else source.end) >= span_end
                       for source in selected):
                raise AccessDenied("Summary span is outside its source snapshot.")

    def complete_compaction(self, scope: Scope, lease: Lease, compaction_id: str, *, title: str,
                            text: str, spans: tuple[Ref, ...]) -> Summary:
        identifier(compaction_id)
        identifier(title)
        if not isinstance(text, str) or not text.strip():
            raise InvalidRequest("Summary text must be nonempty.")
        unicode_text(text)
        with self.transaction():
            self._lease(scope, lease)
            row = self.db.execute("SELECT * FROM compactions WHERE scope=? AND id=?", (scope.key, compaction_id)).fetchone()
            if row is None:
                raise NotFound("Compaction not found in this scope.")
            if row["session"] != lease.session:
                raise AccessDenied("Only the compaction owner can complete it.")
            if row["phase"] != "prepared":
                raise Conflict("Compaction is not awaiting a summary.")
            state = self.load_snapshot(scope, row["snapshot"])
            self._validate_spans(scope, state, spans)
            summary_id = uuid.uuid4().hex
            summary_sha = digest(canonical({"snapshot": row["snapshot"], "title": title, "text": text,
                                            "spans": [asdict(ref) for ref in spans]}).encode("utf-8"))
            self.db.execute("INSERT INTO summaries VALUES(?,?,?,?,?,?,?,?)",
                            (scope.key, summary_id, row["snapshot"], title, text,
                             canonical([asdict(ref) for ref in spans]), summary_sha, self.clock()))
            self.db.execute("UPDATE compactions SET phase='completed',summary_id=? WHERE scope=? AND id=?",
                            (summary_id, scope.key, compaction_id))
            self._event(scope, "compaction.completed", compaction=compaction_id, summary=summary_id)
        return Summary(summary_id, row["snapshot"], title, text, tuple(spans), summary_sha)

    def abort_compaction(self, scope: Scope, lease: Lease, compaction_id: str) -> None:
        with self.transaction():
            self._lease(scope, lease)
            row = self.db.execute("SELECT * FROM compactions WHERE scope=? AND id=?", (scope.key, compaction_id)).fetchone()
            if row is None:
                raise NotFound("Compaction not found in this scope.")
            if row["session"] != lease.session:
                raise AccessDenied("Only the compaction owner can abort it.")
            if row["phase"] == "aborted":
                return
            if row["phase"] != "prepared":
                raise Conflict("Completed compaction cannot be aborted.")
            self.db.execute("UPDATE compactions SET phase='aborted' WHERE scope=? AND id=?", (scope.key, compaction_id))
            self._event(scope, "compaction.aborted", compaction=compaction_id)

    def summary(self, scope: Scope, summary_id: str) -> Summary:
        identifier(summary_id)
        self._task(scope)
        row = self.db.execute("SELECT * FROM summaries WHERE scope=? AND id=?", (scope.key, summary_id)).fetchone()
        if row is None:
            raise NotFound("Summary not found in this scope.")
        spans = tuple(Ref(**item) for item in json.loads(row["spans_json"]))
        expected = digest(canonical({"snapshot": row["snapshot"], "title": row["title"], "text": row["text"],
                                     "spans": [asdict(ref) for ref in spans]}).encode("utf-8"))
        if expected != row["sha256"]:
            raise IntegrityError("Summary integrity check failed.")
        return Summary(row["id"], row["snapshot"], row["title"], row["text"], spans, row["sha256"])

    def search_summaries(self, scope: Scope, query: str, *, limit: int = 20) -> list[Summary]:
        if not isinstance(query, str) or not query.strip():
            raise InvalidRequest("Summary search query must be nonempty.")
        unicode_text(query)
        integer(limit, 1)
        if limit > 100:
            raise InvalidRequest("Summary search limit exceeds 100.")
        self._task(scope)
        matches = []
        for row in self.db.execute("SELECT id,title,text FROM summaries WHERE scope=? ORDER BY created_at DESC,id", (scope.key,)):
            if query.casefold() in row["title"].casefold() or query.casefold() in row["text"].casefold():
                matches.append(self.summary(scope, row["id"]))
                if len(matches) == limit:
                    break
        return matches

    def summary_is_current(self, scope: Scope, summary: Summary) -> bool:
        try:
            self.load_snapshot(scope, summary.snapshot)
            return True
        except (StaleSnapshot, Unavailable):
            return False

    def begin_action(self, scope: Scope, lease: Lease, action_id: str) -> bool:
        """Reserve a host action ID. False means already reserved/completed; do not execute again."""
        identifier(action_id)
        with self.transaction():
            self._lease(scope, lease)
            if self.db.execute("SELECT 1 FROM actions WHERE scope=? AND id=?", (scope.key, action_id)).fetchone():
                return False
            self.db.execute("INSERT INTO actions VALUES(?,?,'pending')", (scope.key, action_id))
            self._event(scope, "action.reserved")
        return True

    def finish_action(self, scope: Scope, lease: Lease, action_id: str, *, state: str) -> None:
        if state not in ("succeeded", "failed", "cancelled"):
            raise InvalidRequest("Unsupported action terminal state.")
        with self.transaction():
            self._lease(scope, lease)
            row = self.db.execute("SELECT state FROM actions WHERE scope=? AND id=?", (scope.key, action_id)).fetchone()
            if row is None:
                raise NotFound("Action not found.")
            if row[0] == state:
                return
            if row[0] != "pending":
                raise Conflict("Action already has a different terminal result.")
            self.db.execute("UPDATE actions SET state=? WHERE scope=? AND id=?", (state, scope.key, action_id))
            self.db.execute("UPDATE tasks SET version=version+1 WHERE scope=?", (scope.key,))
            self._event(scope, "action.finished", state=state)

    def prepare(self, scope: Scope, lease: Lease, sid: str, target: Target) -> Handoff:
        with self.transaction():
            self._lease(scope, lease)
            self.load_snapshot(scope, sid)
            if target.session == lease.session:
                raise InvalidRequest("A handoff requires a different target session.")
            if self.db.execute("SELECT 1 FROM actions WHERE scope=? AND state='pending'", (scope.key,)).fetchone():
                raise Conflict("Resolve in-flight host actions before handoff.")
            self._register(scope, target)
            hid = uuid.uuid4().hex
            self.db.execute("INSERT INTO handoffs VALUES(?,?,?,?,?,?,'prepared',NULL,NULL)",
                            (scope.key, hid, sid, lease.session, lease.epoch, canonical(asdict(target))))
            self.db.execute("UPDATE tasks SET handoff=? WHERE scope=?", (hid, scope.key))
            self._event(scope, "handoff.prepared", handoff=hid)
        return self.handoff(scope, hid)

    def handoff(self, scope: Scope, hid: str) -> Handoff:
        self._task(scope)
        row = self.db.execute("SELECT * FROM handoffs WHERE scope=? AND id=?", (scope.key, hid)).fetchone()
        if row is None:
            raise NotFound("Handoff not found in this scope.")
        return Handoff(row["id"], row["snapshot"], Lease(row["source"], row["epoch"]),
                       Target(**json.loads(row["target"])), row["phase"], row["packet_sha256"])

    def _active_handoff(self, scope: Scope, hid: str) -> Handoff:
        handoff = self.handoff(scope, hid)
        task = self._lease(scope, handoff.source, frozen_ok=True)
        if task["handoff"] != hid:
            raise Conflict("Handoff is not the pending task handoff.")
        return handoff

    def bind_packet(self, scope: Scope, hid: str, sha: str) -> None:
        with self.transaction():
            h = self._active_handoff(scope, hid)
            if h.phase not in ("prepared", "hydrated"):
                raise Conflict("Cannot rebuild context in this handoff phase.")
            self.load_snapshot(scope, h.snapshot)
            self.db.execute("UPDATE handoffs SET packet_sha256=?,receipt_sha256=NULL,phase='prepared' WHERE scope=? AND id=?",
                            (sha, scope.key, hid))
            self._event(scope, "handoff.context_built", handoff=hid)

    def acknowledge(self, scope: Scope, hid: str, target_session: str, sha: str) -> None:
        """Trusted host confirms that this exact compiled packet was delivered to target."""
        with self.transaction():
            h = self._active_handoff(scope, hid)
            if target_session != h.target.session:
                raise AccessDenied("Receipt session does not match the target.")
            if h.phase not in ("prepared", "hydrated") or h.packet_sha256 is None or h.packet_sha256 != sha:
                raise Conflict("Receipt does not match an issued context packet.")
            self.load_snapshot(scope, h.snapshot)
            self.db.execute("UPDATE handoffs SET receipt_sha256=?,phase='hydrated' WHERE scope=? AND id=?", (sha, scope.key, hid))
            self._event(scope, "handoff.delivered", handoff=hid)

    def validate(self, scope: Scope, hid: str) -> None:
        with self.transaction():
            h = self._active_handoff(scope, hid)
            if h.phase not in ("hydrated", "validated"):
                raise Conflict("Target delivery must be acknowledged first.")
            self.load_snapshot(scope, h.snapshot)
            self.db.execute("UPDATE handoffs SET phase='validated' WHERE scope=? AND id=?", (scope.key, hid))
            self._event(scope, "handoff.validated", handoff=hid)

    def activate(self, scope: Scope, hid: str) -> Lease:
        with self.transaction():
            h = self.handoff(scope, hid)
            task = self._task(scope)
            if h.phase == "activated":
                if task["owner"] == h.target.session and task["epoch"] == h.source.epoch + 1 and task["handoff"] is None:
                    return Lease(h.target.session, h.source.epoch + 1)
                raise Conflict("This activation has been superseded.")
            self._active_handoff(scope, hid)
            if h.phase != "validated":
                raise Conflict("Handoff must be validated before activation.")
            self.load_snapshot(scope, h.snapshot)
            self.db.execute("UPDATE tasks SET owner=?,epoch=epoch+1,handoff=NULL WHERE scope=?", (h.target.session, scope.key))
            self.db.execute("UPDATE handoffs SET phase='activated' WHERE scope=? AND id=?", (scope.key, hid))
            self._event(scope, "handoff.activated", handoff=hid, epoch=h.source.epoch + 1)
        return Lease(h.target.session, h.source.epoch + 1)

    def abort(self, scope: Scope, hid: str, lease: Lease) -> None:
        with self.transaction():
            self._lease(scope, lease, frozen_ok=True)
            h = self._active_handoff(scope, hid)
            if h.source != lease:
                raise AccessDenied("Only the source owner can abort a pending handoff.")
            self.db.execute("UPDATE tasks SET handoff=NULL WHERE scope=?", (scope.key,))
            self.db.execute("UPDATE handoffs SET phase='aborted' WHERE scope=? AND id=?", (scope.key, hid))
            self._event(scope, "handoff.aborted", handoff=hid)
