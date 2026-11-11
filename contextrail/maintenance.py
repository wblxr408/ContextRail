"""Explicit local backup and recovery, with review copies for activation."""

from contextlib import closing, contextmanager
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time

from .errors import Conflict, IntegrityError, InvalidRequest, Unavailable
from .models import Ref, Scope, canonical
from .store import APPLICATION_ID, CORE_SCHEMA_VERSION, SCHEMA_V7, SUPPORTED_SCHEMA_VERSIONS, Store


BACKUP = "contextrail.local-backup.v1"
RESTORE = "contextrail.local-restore.v1"
INDEX_RECOVERY = "contextrail.index-recovery.v1"
FILES = {"rail": "rail.sqlite3", "queue": "queue.sqlite3", "project": "project.toml",
         "code": "source.zip", "knowledge": "knowledge"}
FTS_TABLES = {"evidence_fts", "evidence_fts_data", "evidence_fts_idx", "evidence_fts_content",
              "evidence_fts_docsize", "evidence_fts_config"}
DERIVED_TABLES = FTS_TABLES | {"evidence_index_state", "timeline_state", "timeline_entries", "timeline_paths"}
REQUIRED_TABLES = {
    1: {"tasks", "sessions", "blobs", "artifacts", "heads", "snapshots", "handoffs", "actions", "events"},
    3: {"usage_records", "compactions", "summaries"},
    4: {"request_usage"},
    6: {"task_state_items"},
    8: {"directory_sources", "directory_runs", "directory_files", "directory_entries"},
}


def _linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _source(path, *, directory=False):
    info = path.lstat()
    valid_type = stat.S_ISDIR if directory else stat.S_ISREG
    if _linked(info) or not valid_type(info.st_mode):
        raise InvalidRequest("Backup inputs must be regular files or directories, without links.")
    return info


@contextmanager
def _staging(output):
    output = output.absolute()
    if os.path.lexists(output):
        raise Conflict("Choose a new backup or recovery directory.")
    with tempfile.TemporaryDirectory(prefix=".rail-maintenance-", dir=output.parent) as folder:
        yield Path(folder), output


def _publish(stage, output, receipt):
    manifest = stage / "manifest.json"
    manifest.write_bytes((canonical(receipt) + "\n").encode("utf-8"))
    try:
        output.mkdir(mode=0o700)
    except FileExistsError:
        raise Conflict("Choose a new backup or recovery directory.") from None
    # Reserve the new name without replacing user files. A partial copy after an
    # interrupted publish cannot be restored because the manifest is moved last.
    for item in stage.iterdir():
        if item != manifest:
            item.rename(output / item.name)
    manifest.rename(output / manifest.name)


def _copy_file(source, target):
    _source(source)
    with source.open("rb") as reader, target.open("xb") as writer:
        before = os.fstat(reader.fileno())
        shutil.copyfileobj(reader, writer)
        after = source.lstat()
        if (_linked(after) or not os.path.samestat(before, after) or
                (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise Conflict("A backup input changed during copying. Retry after saving edits.")


def _copy_notes(source, target):
    _source(source, directory=True)
    if target.resolve().is_relative_to(source.resolve()):
        raise InvalidRequest("Choose a backup directory outside the knowledge root.")
    target.mkdir()
    count = 0
    for directory, dirs, names in os.walk(source, followlinks=False, onerror=_raise):
        root = Path(directory)
        dirs[:] = sorted(name for name in dirs if not name.startswith(".") and not _linked((root / name).lstat()))
        for name in sorted(names):
            path = root / name
            if name.startswith(".") or path.suffix.lower() != ".md" or _linked(path.lstat()):
                continue
            output = target / path.relative_to(source)
            output.parent.mkdir(parents=True, exist_ok=True)
            _copy_file(path, output)
            count += 1
    return count


def _raise(exc):
    raise exc


def _snapshot(source, target):
    _source(source)
    deadline = time.monotonic() + 60

    def progress(status, remaining, total):
        if status != sqlite3.SQLITE_DONE and time.monotonic() > deadline:
            raise Unavailable("SQLite snapshot did not finish in time. The original was retained.")

    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as origin:
        with closing(sqlite3.connect(target)) as copy:
            origin.backup(copy, pages=256, progress=progress, sleep=0.05)
            copy.execute("PRAGMA journal_mode=DELETE")


def _check_database(db):
    if db.execute("PRAGMA integrity_check(1)").fetchall() != [("ok",)]:
        raise IntegrityError("SQLite integrity check failed. Keep the original and review a known good backup.")


def _check_queue(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        _check_database(db)
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():
            raise InvalidRequest("Expected an existing engineering-memory state database with a jobs table.")


def _rail_state(db, scope):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if (db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID or
            version not in SUPPORTED_SCHEMA_VERSIONS):
        raise InvalidRequest("Expected an initialized ContextRail database with a supported schema.")
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for introduced, required in REQUIRED_TABLES.items():
        if version >= introduced and required - tables:
            raise IntegrityError("An authoritative task table is missing. Recover from a known good backup.")
    if not db.execute("SELECT 1 FROM tasks WHERE scope=?", (scope.key,)).fetchone():
        raise InvalidRequest("The chosen task does not exist in this store. Use its original workspace and scope.")
    if db.execute("PRAGMA foreign_key_check").fetchone() or db.execute(
        "SELECT 1 FROM actions WHERE state IS NULL OR state NOT IN ('pending','succeeded','failed','cancelled') LIMIT 1"
    ).fetchone() or db.execute(
        "SELECT 1 FROM tasks t WHERE NOT EXISTS(SELECT 1 FROM sessions s WHERE s.scope=t.scope AND s.id=t.owner) "
        "OR (t.handoff IS NOT NULL AND NOT EXISTS(SELECT 1 FROM handoffs h WHERE h.scope=t.scope AND h.id=t.handoff "
        "AND h.source=t.owner AND h.epoch=t.epoch AND h.phase IN ('prepared','hydrated','validated'))) LIMIT 1"
    ).fetchone():
        raise IntegrityError("Authoritative task or action state needs review. The original database was retained.")
    pending = [row[0] for row in db.execute(
        "SELECT id FROM actions WHERE scope=? AND state='pending' ORDER BY id LIMIT 21", (scope.key,))]
    return {"schema_version": version, "tasks": db.execute("SELECT count(*) FROM tasks").fetchone()[0],
            "actions_needing_review": db.execute("SELECT count(*) FROM actions WHERE state='pending'").fetchone()[0],
            "open_handoffs": db.execute("SELECT count(*) FROM tasks WHERE handoff IS NOT NULL").fetchone()[0],
            "selected_task_review": {"pending_actions": pending[:20], "truncated": len(pending) > 20,
                                     "handoff": db.execute("SELECT handoff FROM tasks WHERE scope=?",
                                                           (scope.key,)).fetchone()[0]}}


def _inspect_rail(path, scope):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        _check_database(db)
        return _rail_state(db, scope)


def _git_archive(workspace, target):
    base = ["git", "-C", str(workspace), "-c", "core.autocrlf=false", "-c", "core.eol=lf"]
    try:
        revision = subprocess.run([*base, "rev-parse", "--verify", "HEAD^{commit}"],
                                  check=True, capture_output=True, timeout=10).stdout.decode("ascii").strip()
        subprocess.run([*base, "archive", "--format=zip", f"--output={target}", revision],
                       check=True, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise InvalidRequest("Unable to archive committed Git HEAD from the chosen workspace.") from None
    return {"basis": "committed_head", "revision": revision}


def create_backup(config, output, *, knowledge_root=None, queue_db=None, include_code=False):
    with _staging(output) as (stage, target):
        roles = {}
        rail = stage / FILES["rail"]
        _snapshot(config.store, rail)
        roles["rail"] = {"state": _inspect_rail(rail, config.scope), "scope": "whole_database",
                         "indexes": "derived_tables_included_in_sqlite_snapshot"}
        if config.config is not None:
            _copy_file(config.config, stage / FILES["project"])
            roles["project"] = {"basis": "explicit_or_nearest_project_config"}
        if include_code:
            roles["code"] = _git_archive(config.workspace, stage / FILES["code"])
        if knowledge_root is not None:
            roles["knowledge"] = {"files": _copy_notes(knowledge_root, stage / FILES["knowledge"])}
        if queue_db is not None:
            queue = stage / FILES["queue"]
            _snapshot(queue_db, queue)
            _check_queue(queue)
            roles["queue"] = {"scope": "whole_database", "activation": "manual_review"}
        receipt = {"schema": BACKUP, "output": str(target), "scope": asdict(config.scope), "roles": roles,
                   "created_at": time.time(), "sqlite_consistency": "per_database"}
        _publish(stage, target, receipt)
    return receipt


def _read_manifest(backup):
    _source(backup, directory=True)
    manifest = backup / "manifest.json"
    _source(manifest)
    try:
        with manifest.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError()
        data = json.loads(raw)
        roles = data["roles"]
        if (data["schema"] != BACKUP or not isinstance(roles, dict) or "rail" not in roles or
                set(roles) - FILES.keys() or any(not isinstance(item, dict) for item in roles.values())):
            raise ValueError()
        return Scope(**data["scope"]), roles
    except (ValueError, KeyError, TypeError, RecursionError):
        raise InvalidRequest("Invalid or incomplete local backup manifest.") from None


def restore_backup(backup, output):
    scope, roles = _read_manifest(backup)
    with _staging(output) as (stage, target):
        for role in roles.keys() - {"knowledge"}:
            name = FILES[role]
            _copy_file(backup / name, stage / name)
        if "knowledge" in roles:
            count = _copy_notes(backup / FILES["knowledge"], stage / FILES["knowledge"])
            if count != roles["knowledge"].get("files"):
                raise IntegrityError("The backup knowledge collection is incomplete.")
        state = _inspect_rail(stage / FILES["rail"], scope)
        if "queue" in roles:
            _check_queue(stage / FILES["queue"])
        receipt = {"schema": RESTORE, "output": str(target), "scope": asdict(scope), "state": state,
                   "restored_roles": sorted(roles), "activation": "manual_review", "actions_replayed": 0}
        _publish(stage, target, receipt)
    return receipt


def _quoted(name):
    return '"' + name.replace('"', '""') + '"'


def _copy_without_indexes(source, target, scope):
    from .indexing import FTS_SCHEMA
    from .timeline import SCHEMA_V9

    # A broken FTS constructor can reject even DROP TABLE. Read the ordinary
    # tables into a separate SQLite file without opening the damaged module.
    with closing(sqlite3.connect(target, uri=True)) as db:
        db.execute("ATTACH DATABASE ? AS original", (source.as_uri() + "?mode=ro",))
        try:
            if db.execute("PRAGMA original.application_id").fetchone()[0] != APPLICATION_ID:
                raise InvalidRequest("Expected an initialized ContextRail database.")
            version = db.execute("PRAGMA original.user_version").fetchone()[0]
            if version != CORE_SCHEMA_VERSION:
                raise Conflict("Restore or migrate a review copy to the current schema before index recovery.")
            index_sql = db.execute("SELECT sql FROM original.sqlite_master WHERE name='evidence_fts'").fetchone()
            if index_sql is not None and index_sql[0] != FTS_SCHEMA.replace(" IF NOT EXISTS", ""):
                raise IntegrityError("Unrecognized evidence index definition. Review the database without changing it.")
            objects = db.execute("SELECT type,name,tbl_name,sql FROM original.sqlite_master "
                                 "WHERE name NOT GLOB 'sqlite_*' AND sql IS NOT NULL ORDER BY rowid").fetchall()
            indexes = []
            db.execute("BEGIN")
            for kind, name, table, sql in objects:
                if table in DERIVED_TABLES:
                    continue
                if kind == "index":
                    indexes.append(sql)
                    continue
                if kind != "table" or not sql.upper().startswith("CREATE TABLE "):
                    raise IntegrityError("The store contains an unsupported schema object; recovery stopped.")
                db.execute(sql)
                quoted = _quoted(name)
                order = "" if "WITHOUT ROWID" in sql.upper() else " ORDER BY rowid"
                db.execute(f"INSERT INTO main.{quoted} SELECT * FROM original.{quoted} NOT INDEXED{order}")
            for statement in (SCHEMA_V7 + SCHEMA_V9).split(";"):
                if statement.strip():
                    db.execute(statement)
            for sql in indexes:
                db.execute(sql)
            db.execute("DELETE FROM main.sqlite_sequence")
            db.execute("INSERT INTO main.sqlite_sequence SELECT * FROM original.sqlite_sequence")
            db.execute(f"PRAGMA main.application_id={APPLICATION_ID}")
            db.execute(f"PRAGMA main.user_version={version}")
            _check_database(db)
            state = _rail_state(db, scope)
            db.commit()
            return state
        except sqlite3.DatabaseError:
            raise IntegrityError("Could not recover authoritative tables from this database. The original was retained.") from None


def recover_evidence_index(config, output):
    from . import indexing, timeline, timeline_index

    with _staging(output) as (stage, target):
        with tempfile.TemporaryDirectory(prefix=".rail-index-source-", dir=stage.parent) as folder:
            source = Path(folder) / "original.sqlite3"
            _snapshot(config.store, source)
            rail = stage / FILES["rail"]
            state = _copy_without_indexes(source, rail, config.scope)
        with Store(rail) as store, store.transaction():
            if not store.fts_available:
                raise Unavailable("This SQLite build cannot rebuild FTS5.")
            for row in store.db.execute("SELECT identity FROM tasks ORDER BY scope"):
                scope = Scope(**json.loads(row["identity"]))
                indexing.rebuild_contents(store, scope)
                for source in store.db.execute(
                    "SELECT a.name,a.revision,a.invalidated,a.expires,a.size FROM timeline_state s "
                    "JOIN artifacts a ON a.scope=s.partition AND a.name=s.name AND a.revision=s.revision "
                    "WHERE s.partition=?", (scope.key,)
                ):
                    name, revision = source["name"], source["revision"]
                    if source["invalidated"] or (source["expires"] is not None and source["expires"] <= store.clock()):
                        timeline_index.set_state(store.db, scope.key, name, revision, "unavailable", "source_unavailable")
                    elif source["size"] > timeline_index.MAX_SOURCE:
                        timeline_index.set_state(store.db, scope.key, name, revision, "excluded", "source_over_4mib")
                    else:
                        timeline.update(store, scope, name, revision, store.get(scope, Ref(name, revision)).content)
            store.db.execute("INSERT INTO evidence_fts(evidence_fts) VALUES('integrity-check')")
        receipt = {"schema": INDEX_RECOVERY, "output": str(target), "scope": asdict(config.scope),
                   "state": state, "indexes": ["evidence_fts", "timeline"], "activation": "manual_review",
                   "actions_replayed": 0}
        _publish(stage, target, receipt)
    return receipt
