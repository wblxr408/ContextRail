"""Move an explicit Bridge publication and reviewed evidence to another workspace."""

from dataclasses import asdict
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
import unicodedata
from urllib.parse import urlsplit
import zipfile

from .bridge import BridgeEvidence, CapturedBundle, MAX_BYTES, MAX_FILES, _json
from .errors import AccessDenied, Conflict, IntegrityError, InvalidRequest
from .models import Lease, Ref, canonical, digest, identifier
from .sanitization import CaptureError, REDACTED, require_safe_identity, sanitize_capture


SCHEMA = "contextrail.portable-task/v1"
INDEX = "manifest.json"
MAX_MANIFEST = 1024 * 1024
MAX_ARCHIVE = MAX_BYTES + 4 * 1024 * 1024
MAX_MATERIALS = 32
MAX_LINKS = 64
WINDOW = 256 * 1024
SENSITIVE_PATHS = {".git", ".ssh", ".aws", ".gnupg", ".context-rail", ".codex", ".claude",
                   ".netrc", ".npmrc", "credentials", "credentials.json", "secrets.json", "id_rsa", "id_ed25519"}
DEVICE_NAMES = {"aux", "con", "nul", "prn", *(f"{prefix}{n}" for prefix in ("com", "lpt") for n in range(1, 10))}


def _relative(value):
    identifier(value)
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or path.as_posix() != value or any(c in value for c in '\\:<>|?*"'):
        raise InvalidRequest("Portable paths must be normalized relative paths with POSIX separators.")
    for part in path.parts:
        lower = part.casefold()
        if part in (".", "..") or part.endswith((" ", ".")) or lower.split(".", 1)[0] in DEVICE_NAMES:
            raise InvalidRequest("Portable path is incompatible with the destination filesystem.")
        if lower in SENSITIVE_PATHS or lower == ".env" or lower.startswith(".env.") or lower.endswith((".key", ".pem", ".pfx")):
            raise AccessDenied("Credential and host configuration paths are excluded from portable task packages.")
    _safe_identity(value)
    return value


def _safe_identity(value):
    identifier(value)
    try:
        require_safe_identity(value)
    except CaptureError:
        raise AccessDenied("Portable package metadata contains unsafe material.") from None


def _link(value):
    _safe_identity(value)
    try:
        url = urlsplit(value)
    except ValueError:
        raise InvalidRequest("Knowledge links must be explicit memory:// project/note links.") from None
    if (url.scheme != "memory" or not url.netloc.isidentifier() or not url.path.strip("/")
            or url.query or url.fragment or any(part in {"", ".", ".."} for part in url.path[1:].split("/"))):
        raise InvalidRequest("Knowledge links must be explicit memory:// project/note links.")
    return value


def _values(values, parse):
    if not isinstance(values, list) or len(values) > MAX_LINKS:
        raise InvalidRequest("Portable dependency paths and knowledge links are limited to 64 each.")
    return list(dict.fromkeys(parse(value) for value in values))


def _review(raw):
    """Keep original bytes, rejecting recognized secrets and unreviewable binary data."""
    try:
        text = raw.decode("utf-8-sig")
        if "\x00" in text:
            raise UnicodeError()
    except UnicodeError:
        raise AccessDenied("Portable evidence requires reviewed UTF-8 text; retain binary files separately.") from None
    # Structured credentials can use JSON keys rather than textual assignment syntax.
    value = None
    if len(raw) <= 2 * 1024 * 1024 and text.lstrip().startswith(("{", "[")):
        try:
            value = json.loads(text)
        except (ValueError, RecursionError):
            pass
    try:
        if isinstance(value, (dict, list)):
            processed = sanitize_capture(value, "evidence").value
            if canonical(processed).count(REDACTED) > canonical(value).count(REDACTED):
                raise CaptureError()
        else:
            for start in range(0, len(text), WINDOW):
                window = text[max(0, start - 1024):start + WINDOW + 1024]
                if sanitize_capture(window, "evidence").value.count(REDACTED) > window.count(REDACTED):
                    raise CaptureError()
    except (CaptureError, ValueError):
        raise AccessDenied("Portable evidence contains recognized credentials or cannot be reviewed safely.") from None


def _dependencies(workspace, paths):
    rows = []
    for name in paths:
        path = (workspace / name).resolve()
        status = "outside_workspace" if not path.is_relative_to(workspace) else "present" if path.is_file() else "missing"
        rows.append({"path": name, "status": status})
    return rows


def _inventory(content):
    return {"bytes": len(content), "sha256": digest(content)}


def _entry_names(names):
    aliases = set()
    for name in names:
        normal = unicodedata.normalize("NFC", name).casefold()
        if normal in aliases:
            raise InvalidRequest("Portable archive paths collide on a destination filesystem.")
        aliases.add(normal)


def _source_ref(value):
    try:
        if set(value) - {"name", "revision", "start", "end"}:
            raise TypeError()
        ref = Ref(**value)
    except (TypeError, ValueError):
        raise InvalidRequest("Portable evidence requires an exact Rail reference.") from None
    _relative(ref.name)
    return ref


def _write_archive(path, manifest, contents):
    path = path.absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise Conflict("Choose a new portable archive path.")
    with tempfile.TemporaryDirectory(dir=path.parent, prefix="rail-portable-write-") as stage:
        temporary = Path(stage) / "task.zip"
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr(INDEX, manifest)
            for name, content in contents.items():
                archive.writestr(name, content)
        try:
            if os.name == "nt":
                os.rename(temporary, path)
            else:
                os.link(temporary, path)
        except FileExistsError:
            raise Conflict("Choose a new portable archive path.") from None


def export_bundle(store, config, args):
    identifier(args.snapshot)
    if len(args.evidence) > MAX_MATERIALS:
        raise InvalidRequest("Select up to 32 exact Rail evidence references.")
    dependencies = _values(args.require_path, _relative)
    links = _values(args.knowledge_link, _link)
    refs = tuple(dict.fromkeys(_source_ref(_json(value)) for value in args.evidence))
    bridge = BridgeEvidence(store, config.scope, args.session)
    with store.read_transaction():
        publication, bundle, _ = bridge._resolve(args.snapshot)
        extras = [store.get(config.scope, ref) for ref in refs]
    _safe_identity(publication["task_id"])
    _safe_identity(publication["snapshot_id"])
    _safe_identity(publication["bridge_scope"])
    sources = {f"bridge/{_relative(name)}": raw for name, raw in bundle.sources.items()}
    if "bridge/requirements.json" in sources:
        raise InvalidRequest("The Bridge ledger cannot also be a source file in a portable bundle.")
    contents = {"bridge/requirements.json": bundle.raw, **sources}
    materials = []
    for index, artifact in enumerate(extras):
        member = f"evidence/{index}"
        _safe_identity(artifact.media_type)
        contents[member] = artifact.content
        materials.append({"member": member, "source_ref": asdict(artifact.ref), "media_type": artifact.media_type})
    if sum(map(len, contents.values())) > MAX_BYTES:
        raise InvalidRequest("Portable evidence exceeds the 64 MiB total limit.")
    _entry_names(contents)
    for content in contents.values():
        _review(content)
    info = {"schema": SCHEMA, "task_id": publication["task_id"], "bridge_snapshot": publication["snapshot_id"],
            "bridge_scope": publication["bridge_scope"], "source_snapshot": args.snapshot, "dependencies": dependencies,
            "knowledge_links": links, "materials": materials,
            "inventory": {name: _inventory(raw) for name, raw in contents.items()}}
    raw = canonical(info).encode("utf-8")
    if len(raw) > MAX_MANIFEST:
        raise InvalidRequest("Portable archive manifest exceeds 1 MiB.")
    _write_archive(args.archive, raw, contents)
    return {"archive": str(args.archive.absolute()), "archive_id": digest(raw), "task_id": info["task_id"],
            "bridge_snapshot": info["bridge_snapshot"], "source_snapshot": info["source_snapshot"],
            "dependencies": _dependencies(config.workspace, dependencies), "knowledge_links": links,
            "evidence_count": len(materials)}


def _read_archive(path):
    try:
        with path.open("rb") as stream:
            if os.fstat(stream.fileno()).st_size > MAX_ARCHIVE:
                raise IntegrityError("Portable archive exceeds its size limit.")
            with zipfile.ZipFile(stream) as archive:
                entries = archive.infolist()
                names = [entry.filename for entry in entries]
                if INDEX not in names or len(names) > MAX_FILES + MAX_MATERIALS + 2 or len(set(names)) != len(names):
                    raise IntegrityError("Portable archive has an invalid member list.")
                if any(entry.compress_type != zipfile.ZIP_STORED or entry.flag_bits & 1 or entry.is_dir() for entry in entries):
                    raise IntegrityError("Portable archive members must be plain stored files.")
                if archive.getinfo(INDEX).file_size > MAX_MANIFEST or sum(entry.file_size for entry in entries) > MAX_BYTES + MAX_MANIFEST:
                    raise IntegrityError("Portable archive member sizes exceed their limits.")
                raw = archive.read(INDEX)
                info = _json(raw)
                keys = {"schema", "task_id", "bridge_snapshot", "bridge_scope", "source_snapshot",
                        "dependencies", "knowledge_links", "materials", "inventory"}
                if not isinstance(info, dict) or set(info) != keys or info["schema"] != SCHEMA:
                    raise IntegrityError("Unsupported portable task manifest.")
                inventory = info["inventory"]
                if not isinstance(inventory, dict) or set(inventory) != set(names) - {INDEX}:
                    raise IntegrityError("Portable archive contents differ from the inventory.")
                contents = {name: archive.read(name) for name in inventory}
                if any(inventory[name] != _inventory(data) for name, data in contents.items()):
                    raise IntegrityError("Portable archive contains changed evidence.")
    except (zipfile.BadZipFile, OSError, UnicodeError, EOFError, RuntimeError):
        raise IntegrityError("Cannot read the portable task archive.") from None
    return digest(raw), info, contents


def _portable_materials(info, contents):
    rows = info["materials"]
    if not isinstance(rows, list) or len(rows) > MAX_MATERIALS:
        raise IntegrityError("Portable archive material list is invalid.")
    results = []
    for index, row in enumerate(rows):
        expected = f"evidence/{index}"
        if (not isinstance(row, dict) or set(row) != {"member", "source_ref", "media_type"}
                or row["member"] != expected or expected not in contents):
            raise IntegrityError("Portable archive material does not match its member.")
        ref = _source_ref(row["source_ref"])
        _safe_identity(row["media_type"])
        results.append(({"source_ref": asdict(ref), "media_type": row["media_type"]}, contents.pop(expected)))
    return results


def _restore_bundle(workspace, archive_id, contents, task, revision):
    ledger = contents.pop("bridge/requirements.json", None)
    if ledger is None:
        raise IntegrityError("Portable archive lacks its Bridge ledger.")
    data = _json(ledger)
    if not isinstance(data, dict) or data.get("task_id") != task or data.get("snapshot_id") != revision:
        raise IntegrityError("Portable manifest and Bridge checkpoint identify different versions.")
    sources = {}
    for name, content in contents.items():
        if not name.startswith("bridge/"):
            raise IntegrityError("Portable archive contains an undeclared material.")
        sources[_relative(name.removeprefix("bridge/"))] = content
    _entry_names(["requirements.json", *sources])
    parent = (workspace / ".context-rail" / "portable").resolve()
    if not parent.is_relative_to(workspace):
        raise AccessDenied("Portable storage resolves outside the selected receiver workspace.")
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / archive_id
    if not destination.exists():
        with tempfile.TemporaryDirectory(dir=parent, prefix="rail-portable-import-") as stage:
            pending = Path(stage) / "bundle"
            CapturedBundle(pending, ledger, data, sources).materialize(pending)
            captured = CapturedBundle.capture(pending)
            if captured.sources != sources:
                raise IntegrityError("Portable Bridge sources differ from the requirement references.")
            try:
                os.rename(pending, destination)
            except OSError:
                if not destination.is_dir():
                    raise
    if destination.resolve() != destination:
        raise AccessDenied("Portable storage path cannot be redirected.")
    captured = CapturedBundle.capture(destination)
    if captured.raw != ledger or captured.sources != sources:
        raise Conflict("The restored Bridge material changed after an earlier import.")
    return destination


def import_bundle(store, config, args):
    lease = Lease(args.session, args.epoch)
    with store.read_transaction():
        store._lease(config.scope, lease)
    archive_id, info, contents = _read_archive(args.archive)
    for key in ("task_id", "bridge_snapshot", "bridge_scope", "source_snapshot"):
        _safe_identity(info[key])
    if info["task_id"] != config.scope.task:
        raise AccessDenied("Select the exact archive task in the destination workspace.")
    dependencies = _values(info["dependencies"], _relative)
    links = _values(info["knowledge_links"], _link)
    for content in contents.values():
        _review(content)
    materials = _portable_materials(info, contents)
    bridge = BridgeEvidence(store, config.scope, args.session, validator=args.bridge_validator)
    root = _restore_bundle(config.workspace, archive_id, contents, info["task_id"], info["bridge_snapshot"])
    selected = {"archive_id": archive_id, "source_snapshot": info["source_snapshot"],
                "dependency_paths": dependencies, "knowledge_links": links, "materials": materials}
    result = bridge.import_bundle(root, info["bridge_scope"], lease, portable=selected)
    return {**result, "archive_id": archive_id, "source_snapshot": info["source_snapshot"],
            "dependencies": _dependencies(config.workspace, dependencies), "knowledge_links": links,
            "materials": result["portable"]["materials"], "workspace": str(config.workspace),
            "coverage": "selected_task_requirements_and_evidence",
            "omitted": ["workspace_files", "local_credentials", "ownership", "native_sessions", "memory_contents"]}
