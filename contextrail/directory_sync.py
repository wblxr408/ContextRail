"""Explicit bounded filesystem observations, durable continuation and scoped revisions."""

from collections import deque
from contextlib import contextmanager
from dataclasses import asdict
from functools import lru_cache
import fnmatch
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import uuid

from .errors import BudgetExceeded, Conflict, InvalidRequest, NotFound, RailError
from .models import Ref, canonical, identifier, integer


SCHEMA_V8 = """
CREATE TABLE directory_sources (
 scope TEXT NOT NULL REFERENCES tasks(scope), id TEXT NOT NULL, workspace TEXT NOT NULL,
 root TEXT NOT NULL, active_run TEXT NOT NULL, PRIMARY KEY(scope,id)
);
CREATE TABLE directory_runs (
 scope TEXT NOT NULL REFERENCES tasks(scope), id TEXT NOT NULL, source TEXT NOT NULL,
 policy TEXT NOT NULL, budgets TEXT NOT NULL, queue TEXT NOT NULL, step INTEGER NOT NULL,
 phase TEXT NOT NULL, counts TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
 PRIMARY KEY(scope,id), FOREIGN KEY(scope,source) REFERENCES directory_sources(scope,id)
);
CREATE TABLE directory_files (
 scope TEXT NOT NULL REFERENCES tasks(scope), source TEXT NOT NULL, path_key TEXT NOT NULL,
 path TEXT NOT NULL, name TEXT NOT NULL, revision INTEGER NOT NULL, last_seen TEXT NOT NULL,
 active INTEGER NOT NULL, revision_stat TEXT NOT NULL, PRIMARY KEY(scope,source,path_key)
);
CREATE INDEX directory_files_name ON directory_files(scope,name);
CREATE TABLE directory_entries (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL REFERENCES tasks(scope),
 run TEXT NOT NULL, path TEXT NOT NULL, outcome TEXT NOT NULL, reason TEXT NOT NULL,
 name TEXT, revision INTEGER, stat TEXT, FOREIGN KEY(scope,run) REFERENCES directory_runs(scope,id)
);
CREATE INDEX directory_entries_run ON directory_entries(scope,run,sequence);
"""
DEFAULTS = dict(include=[], exclude=[], respect_gitignore=True, max_files=32, max_depth=6,
                max_file_bytes=1024 * 1024, max_directory_entries=20000)
LIMITS = dict(max_files=(1, 1000), max_depth=(0, 64), max_file_bytes=(1, 16 * 1024 * 1024),
              max_directory_entries=(1, 200000))
DEFAULT_EXCLUDES = frozenset({"node_modules", ".venv", "venv", "__pycache__", "build", "dist", "target",
                             ".next", ".nuxt", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox"})
MAX_QUEUE_BYTES = 8 * 1024 * 1024
MAX_PAGE_BYTES = 16 * 1024 * 1024


def directory_options(options):
    result = {**DEFAULTS, **options}
    if set(result) != set(DEFAULTS):
        raise InvalidRequest("Unsupported directory options.")
    for name, (minimum, maximum) in LIMITS.items():
        integer(result[name], minimum)
        if result[name] > maximum:
            raise InvalidRequest(f"Directory {name} exceeds {maximum}.")
    if type(result["respect_gitignore"]) is not bool:
        raise InvalidRequest("respect_gitignore must be boolean.")
    for name in ("include", "exclude"):
        values = result[name]
        if not isinstance(values, list) or len(values) > 128:
            raise InvalidRequest("Directory patterns must be a list of at most 128 globs.")
        for value in values:
            identifier(value)
            if len(value) > 1024 or value.startswith(("/", "\\")) or ".." in value.replace("\\", "/").split("/") or Path(value).is_absolute():
                raise InvalidRequest("Directory globs must be relative without parent traversal.")
    return result


def _matches(path, pattern):
    parts = tuple(path.split("/"))
    pattern = pattern.replace("\\", "/").rstrip("/")
    if "/" not in pattern:
        return any(fnmatch.fnmatchcase(part, pattern) for part in parts)
    tokens = tuple(pattern.split("/"))
    @lru_cache(maxsize=None)
    def match(i, j):
        if j == len(tokens):
            return i == len(parts)
        if tokens[j] == "**":
            return match(i, j + 1) or i < len(parts) and match(i + 1, j)
        return i < len(parts) and fnmatch.fnmatchcase(parts[i], tokens[j]) and match(i + 1, j + 1)
    return match(0, 0)


def _link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _inside(path, workspace):
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(workspace):
        raise InvalidRequest("Directory source must stay inside the selected workspace.")
    return resolved


def _safe_components(path, workspace):
    relative = path.relative_to(workspace)
    current = workspace
    for part in relative.parts:
        current /= part
        if _link(current.lstat()):
            raise InvalidRequest("link_or_reparse")
    return _inside(path, workspace)


def _windows_handle_path(handle):
    import ctypes
    from ctypes import wintypes
    function = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
    function.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    function.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = function(handle, buffer, len(buffer), 0)
    if not length or length >= len(buffer):
        raise OSError("Cannot validate opened source path.")
    name = buffer.value
    if name.startswith("\\\\?\\UNC\\"):
        name = "\\\\" + name[8:]
    elif name.startswith("\\\\?\\"):
        name = name[4:]
    return Path(name).resolve(strict=True)


def _opened_path(fd, original):
    if os.name == "nt":
        import msvcrt
        return _windows_handle_path(msvcrt.get_osfhandle(fd))
    descriptor = Path("/proc/self/fd") / str(fd)
    if descriptor.is_symlink():
        return descriptor.resolve(strict=True)
    return original.resolve(strict=True)


@contextmanager
def _directory_entries(path, workspace):
    physical = _safe_components(path, workspace)
    if os.name != "nt":
        fd = os.open(physical, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            actual = _opened_path(fd, physical)
            if actual != physical or not actual.is_relative_to(workspace):
                raise InvalidRequest("opened_directory_changed")
            with os.scandir(fd) as iterator:
                yield iterator
        finally:
            os.close(fd)
        return
    # Pin every workspace path component against rename/delete while Windows
    # enumerates by name. OPEN_REPARSE_POINT prevents opening a swapped link's
    # target; strict final-handle checks precede enumeration. No lexical fallback.
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
    handles = []
    try:
        components = [workspace]
        for part in physical.relative_to(workspace).parts:
            components.append(components[-1] / part)
        for component in components:
            handle = create(str(component), 1, 3, None, 3, 0x02000000 | 0x00200000, None)
            if handle == ctypes.c_void_p(-1).value:
                raise OSError("Cannot pin directory for bounded enumeration.")
            handles.append(handle)
            if _link(component.lstat()):
                raise InvalidRequest("link_or_reparse")
            if _windows_handle_path(handle) != component:
                raise InvalidRequest("opened_directory_changed")
        with os.scandir(physical) as iterator:
            yield iterator
    finally:
        for handle in reversed(handles):
            close(handle)


def _read_file(path, workspace, maximum):
    physical = _safe_components(path, workspace)
    before = physical.lstat()
    if not stat.S_ISREG(before.st_mode) or _link(before):
        raise InvalidRequest("special_file")
    if before.st_size > maximum:
        raise InvalidRequest("max_file_bytes")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(physical, flags)
    try:
        opened = os.fstat(fd)
        actual = _opened_path(fd, physical)
        if not actual.is_relative_to(workspace) or actual != physical:
            raise InvalidRequest("opened_path_changed")
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise InvalidRequest("source_changed_during_read")
        chunks, size = [], 0
        while size <= maximum:
            block = os.read(fd, min(65536, maximum + 1 - size))
            if not block:
                break
            chunks.append(block)
            size += len(block)
        after = os.fstat(fd)
        final = _safe_components(path, workspace).stat()
        signature = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if signature(before) != signature(after) or signature(final) != signature(after):
            raise InvalidRequest("source_changed_during_read")
        if size > maximum:
            raise InvalidRequest("max_file_bytes")
        return b"".join(chunks), {"mtime_ns": after.st_mtime_ns, "size": size}
    finally:
        os.close(fd)


@contextmanager
def _git_ignores(workspace, enabled):
    if not enabled:
        yield lambda paths: set()
        return
    temp = None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    def run(args, raw=None):
        try:
            return subprocess.run(args, input=raw, capture_output=True, timeout=30, creationflags=flags)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InvalidRequest("Native gitignore evaluation is unavailable; explicitly disable it or repair Git.") from exc
    try:
        found = run(["git", "-C", str(workspace), "rev-parse", "--show-toplevel"])
        if found.returncode == 0:
            base = ["git", "-C", str(workspace)]
        else:
            temp = tempfile.TemporaryDirectory(prefix="rail-gitignore-")
            metadata = Path(temp.name) / "metadata"
            initialized = run(["git", "init", "--bare", "--quiet", "--template=", str(metadata)])
            if initialized.returncode:
                raise InvalidRequest("Cannot prepare temporary native gitignore rules.")
            base = ["git", "-C", str(workspace), "--git-dir=" + str(metadata), "--work-tree=" + str(workspace), "-c", "core.bare=false"]
        cache = {}
        def ignored(paths):
            missing = [path for path in paths if path not in cache]
            if missing:
                data = b"".join(path.encode("utf-8") + b"\x00" for path in missing)
                result = run([*base, "check-ignore", "--no-index", "-z", "--stdin"], data)
                if result.returncode not in (0, 1):
                    raise InvalidRequest("Native gitignore evaluation failed; directory coverage has not advanced.")
                hits = set(result.stdout.decode("utf-8").strip("\x00").split("\x00")) if result.stdout else set()
                cache.update((path, path in hits) for path in missing)
            return {path for path in paths if cache[path]}
        yield ignored
    finally:
        if temp is not None:
            temp.cleanup()


def _run(store, scope, run):
    identifier(run)
    row = store.db.execute("SELECT r.*,s.workspace,s.root,s.active_run FROM directory_runs r JOIN directory_sources s "
                           "ON s.scope=r.scope AND s.id=r.source WHERE r.scope=? AND r.id=?", (scope.key, run)).fetchone()
    if row is None:
        raise NotFound("Directory run not found in this task.")
    return row


def _artifact_name(source, path_key):
    return "directory/" + source + "/file/" + uuid.uuid5(uuid.NAMESPACE_URL, path_key).hex


def _budget(result, budget):
    while len(canonical(result).encode("utf-8")) > budget:
        if not result["entries"]:
            raise BudgetExceeded("Directory progress metadata cannot fit the complete response budget.")
        result["entries"].pop()
        result["returned"] = len(result["entries"])
        result["next_offset"] = result["offset"] + result["returned"]
        result["omitted"] = result["total"] - result["offset"] - result["returned"]
        result["continuation"] = "increase_response_budget" if not result["entries"] else "directory_status_next_offset"
    return result


def directory_status(store, scope, session, run, *, offset=0, limit=20, budget=8192):
    store.session(scope, session)
    integer(offset)
    integer(limit, 1)
    integer(budget, 1)
    if limit > 100 or offset > 1000000:
        raise InvalidRequest("Directory status limit exceeds 100 or offset exceeds 1000000.")
    with store.read_transaction():
        row = _run(store, scope, run)
        total = store.db.execute("SELECT COUNT(*) FROM directory_entries WHERE scope=? AND run=?", (scope.key, run)).fetchone()[0]
        entries = []
        for entry in store.db.execute("SELECT * FROM directory_entries WHERE scope=? AND run=? ORDER BY sequence LIMIT ? OFFSET ?", (scope.key, run, limit, offset)):
            ref, availability = None, "not_captured"
            if entry["name"]:
                ref = asdict(Ref(entry["name"], entry["revision"]))
                try:
                    store.get(scope, Ref(**ref))
                    head = store.db.execute("SELECT revision FROM heads WHERE scope=? AND name=?", (scope.key, entry["name"])).fetchone()
                    availability = "current" if head and head[0] == entry["revision"] else "historical"
                except RailError:
                    availability = "unavailable"
            entries.append({"path": entry["path"], "outcome": entry["outcome"], "reason": entry["reason"],
                            "ref": ref, "availability": availability})
        result = {"schema": "contextrail.directory-run.v1", "run": run, "source": row["source"], "root": row["root"],
                  "status": row["phase"] if row["active_run"] == run else "superseded", "step": row["step"],
                  "counts": json.loads(row["counts"]), "pending": len(json.loads(row["queue"])),
                  "coverage": "observed_files_only; filesystem_is_not_an_atomic_snapshot", "updated_at": row["updated"],
                  "offset": offset, "total": total, "returned": len(entries), "entries": entries,
                  "omitted": max(0, total - offset - len(entries)),
                  "next_offset": offset + len(entries) if offset + len(entries) < total else None,
                  "resume": {"run": run, "step": row["step"]} if row["phase"] == "partial" and row["active_run"] == run else None,
                  "continuation": "directory_status_next_offset", "trust": "untrusted_source_metadata"}
        return _budget(result, budget)


class DirectorySync:
    def __init__(self, store, config, lease):
        self.store, self.config, self.lease, self.scope = store, config, lease, config.scope

    def sync(self, *, root=None, resume=None, step=None, **budgets):
        config, store, scope = self.config, self.store, self.scope
        # Preflight metadata budget before mutating the task. Full entries may be read via status paging.
        if config.response_bytes < 1536:
            raise BudgetExceeded("Directory writes require at least 1536 response bytes; use directory-status for narrower reads.")
        options = directory_options({**config.directory, **{key: value for key, value in budgets.items() if value is not None}})
        policy = {key: options[key] for key in ("include", "exclude", "respect_gitignore")}
        policy.update(ignore=list(config.ignore), workspace=str(config.workspace), store=str(config.store))
        limits = {key: options[key] for key in LIMITS}
        if resume is not None:
            if root is not None or step is None:
                raise InvalidRequest("Resume requires exact run and step, without a new root.")
            integer(step)
            row = _run(store, scope, resume)
            if row["active_run"] != resume or row["phase"] != "partial" or row["step"] != step:
                raise Conflict("Directory cursor is stale, complete or superseded.")
            if row["policy"] != canonical(policy):
                raise Conflict("Directory selection policy changed; start a new source run.")
            previous_limits = json.loads(row["budgets"])
            limits = {key: max(limits[key], previous_limits[key]) for key in LIMITS}
        else:
            if root is None or step is not None:
                raise InvalidRequest("New directory sync requires an explicit root, without a step.")
            identifier(str(root))
            selected = Path(root)
            if not selected.is_absolute():
                selected = config.workspace / selected
            selected = Path(os.path.abspath(selected))
            if not selected.is_relative_to(config.workspace):
                raise InvalidRequest("Directory source must stay inside the selected workspace.")
            try:
                physical = _safe_components(selected, config.workspace)
                if not physical.is_dir():
                    raise InvalidRequest("Source root must be a directory.")
            except FileNotFoundError:
                # Only a previously selected physical root can transition to an empty deleted root.
                physical = selected
            source = uuid.uuid5(uuid.NAMESPACE_URL, os.path.normcase(str(config.workspace)) + "\x00" + os.path.normcase(str(physical))).hex
            root_name = physical.relative_to(config.workspace).as_posix()
            run = uuid.uuid4().hex
            with store.transaction():
                store._lease(scope, self.lease)
                existing = store.db.execute("SELECT 1 FROM directory_sources WHERE scope=? AND id=?", (scope.key, source)).fetchone()
                if not selected.exists() and not existing:
                    raise InvalidRequest("New source root must exist.")
                store.db.execute("INSERT INTO directory_sources VALUES(?,?,?,?,?) ON CONFLICT(scope,id) DO UPDATE SET active_run=excluded.active_run",
                                 (scope.key, source, str(config.workspace), root_name, run))
                store.db.execute("INSERT INTO directory_runs VALUES(?,?,?,?,?,?,0,'partial',?,?,?)",
                                 (scope.key, run, source, canonical(policy), canonical(limits), canonical([[root_name, -1]]),
                                  canonical(dict(captured=0, unchanged=0, skipped=0, deleted=0, deferred=0)), store.clock(), store.clock()))
            row = _run(store, scope, run)
        store._lease(scope, self.lease)
        queue, deferred = deque(json.loads(row["queue"])), []
        staged, observations = [], []
        consumed = total_bytes = visits = 0
        with _git_ignores(config.workspace, options["respect_gitignore"]) as ignored:
            while queue and consumed < limits["max_files"] and visits < max(256, limits["max_files"] * 4):
                path_name, depth = queue.popleft()
                if depth == -2:
                    # Persistent reconciliation continuation, never a filesystem path.
                    break
                visits += 1
                path = config.workspace / path_name
                relative_root = Path(path_name).relative_to(Path(row["root"])).as_posix()
                reason = ""
                try:
                    info = path.lstat()
                    if _link(info):
                        reason = "link_or_reparse"
                    elif any(part in {".git", ".context-rail"} for part in Path(path_name).parts) or path.resolve() == config.store:
                        reason = "internal_state"
                    elif any(part in DEFAULT_EXCLUDES for part in Path(path_name).parts):
                        reason = "dependency_or_generated"
                    elif any(_matches(relative_root, pattern) for pattern in options["exclude"]) or any(_matches(path_name, pattern) for pattern in config.ignore):
                        reason = "excluded"
                    elif path_name in ignored([path_name]):
                        reason = "gitignored"
                    if reason:
                        observations.append((path_name, "skipped", reason, None, None))
                        continue
                    _safe_components(path, config.workspace)
                    if stat.S_ISDIR(info.st_mode):
                        if depth >= limits["max_depth"]:
                            raise InvalidRequest("max_depth")
                        with _directory_entries(path, config.workspace) as iterator:
                            children = []
                            for child in iterator:
                                children.append([(Path(path_name) / child.name).as_posix(), depth + 1])
                                if len(children) > limits["max_directory_entries"]:
                                    raise InvalidRequest("max_directory_entries; raise_budget_or_select_narrower_root")
                        children.sort(key=lambda item: (os.path.normcase(item[0]), item[0]))
                        if len(canonical(children + list(queue) + deferred).encode("utf-8")) > MAX_QUEUE_BYTES:
                            raise InvalidRequest("queue_metadata_limit; select_narrower_root")
                        ignored([item[0] for item in children])
                        queue.extendleft(reversed(children))
                        continue
                    if not stat.S_ISREG(info.st_mode):
                        observations.append((path_name, "skipped", "special_file", None, None))
                        continue
                    if options["include"] and not any(_matches(relative_root, pattern) for pattern in options["include"]):
                        observations.append((path_name, "skipped", "not_included", None, None))
                        continue
                    if info.st_size > limits["max_file_bytes"]:
                        raise InvalidRequest("max_file_bytes")
                    if info.st_size > MAX_PAGE_BYTES - total_bytes:
                        queue.appendleft([path_name, depth])
                        break
                    consumed += 1
                    expected_name = _artifact_name(row["source"], os.path.normcase(path_name))
                    expected = store.db.execute("SELECT revision FROM heads WHERE scope=? AND name=?", (scope.key, expected_name)).fetchone()
                    expected_revision = expected[0] if expected else 0
                    content, info = _read_file(path, config.workspace, min(limits["max_file_bytes"], MAX_PAGE_BYTES - total_bytes))
                    info["source_path"] = path_name
                    if total_bytes + len(content) > MAX_PAGE_BYTES:
                        queue.appendleft([path_name, depth])
                        break
                    total_bytes += len(content)
                    staged.append((path_name, content, info, expected_revision))
                except FileNotFoundError:
                    observations.append((path_name, "skipped", "absent_at_observation", None, None))
                except (OSError, InvalidRequest) as exc:
                    reason = str(exc) if isinstance(exc, InvalidRequest) else "source_io_error"
                    # Links swapped during traversal are excluded; no content is read.
                    if reason == "link_or_reparse":
                        observations.append((path_name, "skipped", reason, None, None))
                    else:
                        deferred.append([path_name, depth])
                        observations.append((path_name, "deferred", reason, None, None))
        remaining = list(queue) + deferred
        with store.transaction():
            store._lease(scope, self.lease)
            current = _run(store, scope, row["id"])
            if current["active_run"] != row["id"] or current["step"] != row["step"] or current["phase"] != "partial":
                raise Conflict("Directory cursor advanced while this page was being observed.")
            counts = json.loads(current["counts"])
            for path_name, content, info, expected_revision in staged:
                key = os.path.normcase(path_name)
                old = store.db.execute("SELECT * FROM directory_files WHERE scope=? AND source=? AND path_key=?", (scope.key, row["source"], key)).fetchone()
                name = old["name"] if old else _artifact_name(row["source"], key)
                head = store.db.execute("SELECT revision FROM heads WHERE scope=? AND name=?", (scope.key, name)).fetchone()
                revision = head[0] if head else 0
                if revision != expected_revision:
                    raise Conflict("Source artifact changed while the filesystem page was being observed; retry the same cursor.")
                unchanged = False
                if old and old["active"] and old["revision"] == revision:
                    try:
                        unchanged = store.get(scope, Ref(name, revision)).content == content
                    except RailError:
                        pass
                if unchanged:
                    outcome, version_stat = "unchanged", old["revision_stat"]
                else:
                    media = "text/markdown; charset=utf-8" if Path(path_name).suffix.lower() == ".md" else "text/plain; charset=utf-8"
                    try:
                        content.decode("utf-8")
                    except UnicodeError:
                        media = "application/octet-stream"
                    revision = store._put(scope, name, content, expected_revision=revision, media_type=media).revision
                    outcome, version_stat = "captured", canonical(info)
                store.db.execute("INSERT INTO directory_files VALUES(?,?,?,?,?,?,?,1,?) ON CONFLICT(scope,source,path_key) DO UPDATE SET path=excluded.path,"
                                 "revision=excluded.revision,last_seen=excluded.last_seen,active=1,revision_stat=excluded.revision_stat",
                                 (scope.key, row["source"], key, path_name, name, revision, row["id"], version_stat))
                from .indexing import label_source
                label_source(store, scope.key, name, path_name, content)
                observations.append((path_name, outcome, "", Ref(name, revision), version_stat))
            if not remaining:
                capacity = max(0, limits["max_files"] - len(staged))
                unseen = store.db.execute("SELECT * FROM directory_files WHERE scope=? AND source=? AND active=1 AND last_seen<>? ORDER BY path_key LIMIT ?",
                                         (scope.key, row["source"], row["id"], capacity + 1)).fetchall()
                if len(unseen) > capacity:
                    remaining.append([None, -2])
                for old in unseen[:capacity]:
                    active = store.db.execute("SELECT a.invalidated FROM heads h JOIN artifacts a ON a.scope=h.scope AND a.name=h.name AND a.revision=h.revision "
                                              "WHERE h.scope=? AND h.name=? AND h.revision=?", (scope.key, old["name"], old["revision"])).fetchone()
                    if active and not active[0]:
                        store._invalidate(scope, Ref(old["name"], old["revision"]))
                    store.db.execute("UPDATE directory_files SET active=0 WHERE scope=? AND source=? AND path_key=?", (scope.key, row["source"], old["path_key"]))
                    observations.append((old["path"], "deleted", "not_seen_in_complete_selection", Ref(old["name"], old["revision"]), old["revision_stat"]))
            for path_name, outcome, reason, ref, info in observations:
                store.db.execute("INSERT INTO directory_entries(scope,run,path,outcome,reason,name,revision,stat) VALUES(?,?,?,?,?,?,?,?)",
                                 (scope.key, row["id"], path_name, outcome, reason, ref.name if ref else None, ref.revision if ref else None, info))
                counts[outcome] += 1
            phase = "partial" if remaining else "complete"
            store.db.execute("UPDATE directory_runs SET queue=?,budgets=?,step=step+1,phase=?,counts=?,updated=? WHERE scope=? AND id=?",
                             (canonical(remaining), canonical(limits), phase, canonical(counts), store.clock(), scope.key, row["id"]))
            store._event(scope, "directory.page_committed", run=row["id"], step=row["step"] + 1, phase=phase, files=len(staged), pending=len(remaining))
        return directory_status(store, scope, self.lease.session, row["id"], budget=config.response_bytes)
