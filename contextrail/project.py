"""Project-local configuration and repository/worktree identity (host-side only)."""

from dataclasses import asdict, dataclass, field
import os
from pathlib import Path
import subprocess
import tomllib
import uuid

from .errors import InvalidRequest
from .models import Scope, identifier, integer


def _path(value, anchor):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise InvalidRequest("Expected a nonempty filesystem path.")
    return (anchor / value).resolve()


def _key(path):
    return uuid.uuid5(uuid.NAMESPACE_URL, os.path.normcase(str(path.resolve()))).hex


def repository_identity(workspace):
    def git(*args):
        result = subprocess.run(["git", "-C", str(workspace), "rev-parse", *args],
                                capture_output=True, timeout=10)
        return result.stdout.decode("utf-8").strip() if result.returncode == 0 else None
    try:
        top = git("--show-toplevel")
        common = git("--path-format=absolute", "--git-common-dir") if top else None
        branch = git("--abbrev-ref", "HEAD") if top else None
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        top = common = branch = None
    root = Path(top).resolve() if top else workspace
    repository = Path(common).resolve() if common else root
    return {"repository_id": _key(repository), "worktree_id": _key(root),
            "root": str(root), "git_common_dir": str(repository) if common else None,
            "branch_label": branch, "kind": "git" if common else "directory"}


@dataclass(frozen=True)
class ProjectConfig:
    workspace: Path
    store: Path
    scope: Scope
    identity: dict
    required: tuple[str, ...]
    ignore: tuple[str, ...]
    response_bytes: int
    read_bytes: int
    sources: dict
    config: Path | None
    directory: dict = field(default_factory=dict)
    capture: dict = field(default_factory=dict)

    def describe(self):
        return {"workspace": str(self.workspace), "store": str(self.store), "scope": asdict(self.scope),
                "identity": self.identity, "required": list(self.required), "capture": self.capture or {"ignore": list(self.ignore)},
                "response_bytes": self.response_bytes, "read_bytes": self.read_bytes,
                "sources": self.sources, "directory": self.directory, "config": str(self.config) if self.config else None}


def load_project(start, *, config_path=None, overrides=None):
    requested_workspace = str(start)
    start = Path(start).resolve()
    if not start.is_dir():
        raise InvalidRequest("Workspace must be an existing directory.")
    explicit = {key: value for key, value in (overrides or {}).items() if value is not None}
    marker = _path(str(config_path), start) if config_path is not None else next(
        (parent / ".contextrail.toml" for parent in (start, *start.parents)
         if (parent / ".contextrail.toml").is_file()), None)
    try:
        data = tomllib.loads(marker.read_text(encoding="utf-8")) if marker else {}
    except (OSError, ValueError) as exc:
        raise InvalidRequest("Cannot read valid project TOML.") from exc
    allowed = {"project": {"id", "tenant", "task", "workspace"}, "storage": {"path"},
               "capture": {"ignore","allow","metadata_only","user_mode","assistant_mode","default_mode","unknown_tools","tool_modes"}, "context": {"required", "response_bytes", "read_bytes"},
               "directory": {"include", "exclude", "respect_gitignore", "max_files", "max_depth", "max_file_bytes", "max_directory_entries"}}
    if set(data) - allowed.keys():
        raise InvalidRequest("Unknown project configuration table.")
    for table, values in data.items():
        if not isinstance(values, dict) or set(values) - allowed[table]:
            raise InvalidRequest("Unknown project configuration field.")
    anchor = marker.parent if marker else start
    project, storage, context, capture = (data.get(key, {}) for key in ("project", "storage", "context", "capture"))
    workspace = _path(project.get("workspace", "."), anchor)
    if not workspace.is_dir():
        raise InvalidRequest("Configured workspace must be an existing directory.")
    identity = repository_identity(workspace)
    identity["requested_workspace"] = requested_workspace
    sources = {}
    def pick(name, table, key, default):
        sources[name] = "explicit" if name in explicit else "config" if key in table else "default"
        return explicit.get(name, table.get(key, default))
    task = pick("task", project, "task", "default")
    project_id = pick("project_id", project, "id", identity["repository_id"])
    tenant = pick("tenant", project, "tenant", "local")
    store = _path(pick("store", storage, "path", ".context-rail/state.sqlite3"), anchor)
    response_bytes = pick("response_bytes", context, "response_bytes", 4096)
    read_bytes = pick("read_bytes", context, "read_bytes", 65536)
    sources.update(workspace="config" if "workspace" in project else "marker" if marker else "default",
                   required="config" if "required" in context else "default",
                   ignore="config" if "ignore" in capture else "default")
    for value in (response_bytes, read_bytes):
        integer(value, 1)
        if value > 16 * 1024 * 1024:
            raise InvalidRequest("Context byte limits cannot exceed 16 MiB.")
    def paths(table, name):
        values = table.get(name, [])
        if not isinstance(values, list):
            raise InvalidRequest("Expected a list of relative paths or patterns.")
        for value in values:
            identifier(value)
            if Path(value).is_absolute() or ".." in Path(value).parts:
                raise InvalidRequest("Project paths and patterns must stay relative to the workspace.")
        return tuple(values)
    directory = dict(data.get("directory", {}))
    for name in ("include", "exclude"):
        directory[name] = list(paths(directory, name))
    from .directory_sync import directory_options
    directory = directory_options(directory)
    from .capture_rules import validate_capture
    try:capture_config=validate_capture(capture)
    except ValueError:raise InvalidRequest('Invalid project capture policy') from None
    return ProjectConfig(workspace, store, Scope(tenant, project_id, identity["worktree_id"], task), identity,
                         paths(context, "required"), paths(capture, "ignore"), response_bytes, read_bytes, sources, marker, directory,capture_config)
