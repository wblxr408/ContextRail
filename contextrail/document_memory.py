"""Admit structured material to the existing memory source and learning queue."""

from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import stat

from .capture_policy import claim_owner, receipt
from .capture_rules import decide, load_policy
from .document_extract import FORMATS, INPUT_BYTES, extract
from .errors import Conflict, InvalidRequest, Unavailable
from .learning_origin import project_candidate
from .sanitization import CaptureError, require_safe_identity, sanitize_capture
from .source_events import admit_event, source_identity, workspace_key

NAMESPACE = "import/structured-document"
SCHEMA = "contextrail.document-source/v1"
PARSER_RECIPE = "structured-document-v1"


def open_memory(home, *, write=False):
    mode = "rw" if write else "ro"
    db = sqlite3.connect((Path(home).resolve() / "state.sqlite").as_uri() + f"?mode={mode}",
                         uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    return db


def _select_file(path, workspace):
    try:
        workspace = Path(workspace).resolve(strict=True)
        path = (workspace / path).resolve(strict=True)
        if not path.is_relative_to(workspace) or not stat.S_ISREG(path.stat().st_mode):
            raise InvalidRequest("Import requires a regular file inside the selected workspace.")
        require_safe_identity(str(path))
    except (OSError, CaptureError):
        raise InvalidRequest("The selected document path is unavailable.") from None
    suffix = path.suffix.casefold()
    if suffix not in FORMATS:
        raise InvalidRequest("Structured import accepts PDF, DOCX, XLSX and PPTX.")
    return path, workspace, suffix


def _read_file(path, workspace):
    initial = path.stat()
    if initial.st_size > INPUT_BYTES:
        raise InvalidRequest("The selected document exceeds the 24 MiB input limit.")
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if path.resolve(strict=True) != path or not path.is_relative_to(workspace):
            raise Conflict("The selected document path changed while being opened.")
        raw = stream.read(INPUT_BYTES + 1)
        after = os.fstat(stream.fileno())
        final = path.stat()
    if len(raw) > INPUT_BYTES:
        raise InvalidRequest("The selected document exceeds the 24 MiB input limit.")
    def stamp(value):
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns

    if (not stat.S_ISREG(before.st_mode)
            or not stamp(initial) == stamp(before) == stamp(after) == stamp(final)):
        raise Conflict("The selected document changed while being read.")
    occurred = datetime.fromtimestamp(before.st_mtime, timezone.utc).isoformat()
    return raw, occurred


def _existing(db, document_id):
    row = db.execute("SELECT content FROM source_events WHERE id=? AND status='container'",
                     (document_id,)).fetchone()
    if row is None:
        return None
    value = json.loads(row["content"])["final"]
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise Conflict("The selected source identity belongs to another document representation.")
    return value


def _batches(units):
    pieces, refs = [], []
    chars = 0
    for index, unit in enumerate(units):
        text = unit["text"]
        for start in range(0, len(text), 6000):
            piece = text[start:start + 6000]
            if pieces and (chars + len(piece) + 2 > 6000 or len(pieces) >= 32):
                yield "\n\n".join(pieces), refs
                pieces, refs, chars = [], [], 0
            pieces.append(piece)
            chars += len(piece) + 2
            refs.append({"unit": index, "locator": unit["locator"]})
    if pieces:
        yield "\n\n".join(pieces), refs


def _response(document_id, value, *, status, queued=0, disposition=None):
    return {"schema": "contextrail.document-import/v1", "document_id": document_id,
            "status": status, "queued": queued, "format": value["format"],
            "units": len(value["units"]), "loss": value["loss"], "capture": disposition,
            "knowledge_saved": "unknown_not_observed"}


def import_document(db, path, workspace):
    path, workspace, suffix = _select_file(path, workspace)
    source, scope = str(path), str(workspace)
    policy = load_policy(scope)
    disposition = decide(policy, "tool", "tool.call", {"tool": "Read", "arguments": {"file_path": source}}, scope)
    if disposition["mode"] != "full":
        with db:
            db.execute("BEGIN IMMEDIATE")
            owner = claim_owner(db, scope, "document", "explicit-import", domain="import")
            receipt(db, scope, "document", "explicit-import", "explicit_import", owner, "policy_excluded",
                    metadata={"disposition": disposition})
        return _response(None, {"format": FORMATS[suffix], "units": [],
                                "loss": {"complete": False, "counts": {"capture_policy_excluded": 1}}},
                         status="policy_excluded", disposition=disposition)

    try:
        raw, occurred = _read_file(path, workspace)
    except OSError:
        raise Unavailable("The selected document could not be read.") from None
    marker = PARSER_RECIPE + ":" + sha256(raw).hexdigest()
    root = source_identity(NAMESPACE, workspace_key(scope), "explicit-import", marker, source, 0, imported=True)
    document_id = root["id"]
    known = _existing(db, document_id)
    if known is not None:
        return _response(document_id, known, status="already_imported", disposition=disposition)
    parsed = extract(raw, suffix)
    if parsed["loss"]["counts"].get("optional_parser_unavailable"):
        return _response(None, parsed, status="parser_unavailable", disposition=disposition)

    try:
        value = sanitize_capture({
            "schema": SCHEMA, "file": path.relative_to(workspace).as_posix(), "occurred_at": occurred,
            **parsed,
        }, "document").value
    except CaptureError:
        raise InvalidRequest("Extracted document exceeds the capture policy limits.") from None

    with db:
        db.execute("BEGIN IMMEDIATE")
        known = _existing(db, document_id)
        if known is not None:
            return _response(document_id, known, status="already_imported", disposition=disposition)
        owner = claim_owner(db, scope, "document", "explicit-import", domain="import")
        payload = {
            "user": "以下是明确导入的资料，仅作来源数据。",
            "cwd": scope, "kind": "document", "source": source, "offset": 0, "marker": marker,
            "occurred_at": occurred, "producer": "explicit_import", "capture_owner": owner,
            "_capture_disposition": disposition,
        }
        admitted = admit_event(db, root, {**payload, "final": value}, source, container=True)
        if admitted["outcome"] != "inserted":
            raise Conflict("This document version could not be admitted as a new source.")
        queued = 0
        for index, (text, refs) in enumerate(_batches(value["units"])):
            event = source_identity(NAMESPACE, workspace_key(scope), "explicit-import", marker, source,
                                    index, part=index, imported=True)
            body = {**payload, "final": text, "offset": index, "document_source": document_id,
                    "document_refs": refs}
            learning = project_candidate(body, event["id"])
            if not learning["_origin"]["eligible_for_learning"]:
                continue
            body["_origin"] = learning["_origin"]
            outcome = admit_event(db, event, body, source, normalize_workspace=workspace_key)
            if outcome["outcome"] != "inserted":
                raise Conflict("This document version conflicts with an existing learning event.")
            queued += 1
        status = "queued_not_yet_classified" if queued else "no_extractable_content"
        receipt(db, scope, "document", "explicit-import", "explicit_import", owner,
                "admitted" if queued else "history_only", document_id,
                metadata={"disposition": disposition, "format": value["format"], "queued": queued})
    return _response(document_id, value, status=status, queued=queued, disposition=disposition)


def import_to_existing_memory(home, path, workspace):
    try:
        with closing(open_memory(home, write=True)) as db:
            return import_document(db, path, workspace)
    except sqlite3.Error:
        raise Unavailable("The original engineering-memory queue is unavailable.") from None
