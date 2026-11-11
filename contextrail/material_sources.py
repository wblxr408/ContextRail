"""Read-only views of immutable imports and explicitly bound client memory."""

from collections import Counter
from contextlib import closing
from hashlib import sha256
import json
import sqlite3
import time

from .directory_sync import _read_file
from .document_memory import NAMESPACE, SCHEMA, open_memory
from .errors import AccessDenied, BudgetExceeded, InvalidRequest, NotFound, StaleSnapshot, Unavailable
from .models import canonical, identifier, integer
from .query_sources import SourceFailure
from .retrieval import _terms
from .sanitization import sanitize_capture
from .source_events import workspace_key

DOCUMENT_LIMIT = 48
NATIVE_BYTES = 512 * 1024
NATIVE_LINES = 5000
READER = "context.material_get"

SELECT_DOCUMENTS = """
    SELECT id, content FROM source_events
    WHERE status='container' AND json_extract(coordinates,'$.coordinates.namespace')=?
        AND json_extract(coordinates,'$.coordinates.workspace')=?
"""


def _document_value(row):
    value = json.loads(row["content"])["final"]
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ValueError("Unsupported archived document representation")
    return value


def _native_file(path):
    raw, _ = _read_file(path, path.parent, NATIVE_BYTES)
    text = raw.decode("utf-8-sig")
    original = text.splitlines()
    processed = sanitize_capture(text, "document").value.splitlines()
    basis = "source_lines" if len(original) == len(processed) else "processed_lines"
    version = "native-memory-v1:" + sha256(raw).hexdigest()
    return version, processed, basis


def _hit(origin, identity, title, text, locator, ref, role, term_score, version, client=None):
    return {
        "origin": origin, "id": identity, "title": title, "preview": text[:520],
        "preview_truncated": len(text) > 520, "task": None, "source_session": None,
        "version": {"kind": "immutable_import" if origin == "documents" else "native_file_revision",
                    "value": version, "state": "available"},
        "time": {"occurred_at": None, "state": "unknown", "basis": "document_occurrence_unknown"},
        "ref": {"reader": READER, "origin": origin, **ref}, "locator": locator,
        "current_task_membership": False, "evidence_role": role, "source_score": term_score,
        "client": client,
    }


def _score(text, terms):
    folded = text.casefold()
    return sum(term in folded for term in terms)


def _query_terms(query):
    terms = [term for term in _terms(query) if len(term.encode("utf-8")) <= 512][:16]
    if not terms:
        raise SourceFailure("unsupported_query_terms")
    return terms


class DocumentsView:
    def __init__(self, binding, deadline):
        if not binding or not binding.document_imports:
            raise SourceFailure("not_configured")
        try:
            with closing(open_memory(binding.memory_home)) as db:
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                rows = db.execute(SELECT_DOCUMENTS + " ORDER BY first_seen DESC, id LIMIT ?",
                                  (NAMESPACE, workspace_key(binding.workspace), DOCUMENT_LIMIT + 1)).fetchall()
        except (OSError, sqlite3.Error):
            raise SourceFailure("document_source_unavailable") from None
        self.catalog_truncated = len(rows) > DOCUMENT_LIMIT
        self.documents = [(row["id"], _document_value(row)) for row in rows[:DOCUMENT_LIMIT]]
        self.revision = tuple(identity for identity, _ in self.documents), self.catalog_truncated
        self.complete = True

    def search(self, query, limit, offset, deadline):
        terms = _query_terms(query)
        ranked = []
        loss = Counter()
        for position, (identity, value) in enumerate(self.documents):
            if time.monotonic() > deadline:
                raise SourceFailure("deadline_exceeded")
            loss.update(value["loss"]["counts"])
            for index, unit in enumerate(value["units"]):
                if score := _score(unit["text"], terms):
                    ranked.append((-score, position, index, identity, value, unit))
        ranked.sort(key=lambda item: item[:3])
        hits = []
        for score, _, index, identity, value, unit in ranked[offset:offset + limit]:
            hits.append(_hit("documents", f"{identity}:{index}", value["file"], unit["text"],
                             unit["locator"], {"document_id": identity, "unit": index},
                             "explicitly_imported_document", -score, identity))
        coverage = {"scope": "bound_workspace_original_queue", "catalog_limit": DOCUMENT_LIMIT,
                    "catalog_truncated": self.catalog_truncated, "documents_scanned": len(self.documents),
                    "parse_losses": dict(loss), "complete": not self.catalog_truncated and not loss}
        return hits, len(ranked), coverage


class NativeMemoryView:
    def __init__(self, binding, deadline):
        if not binding or not binding.native_memory_files:
            raise SourceFailure("not_configured")
        self.files = []
        self.errors = []
        for identity, client, path in binding.native_memory_files:
            if time.monotonic() > deadline:
                raise SourceFailure("deadline_exceeded")
            try:
                version, lines, basis = _native_file(path)
                self.files.append((identity, client, path.name, version, lines, basis))
            except (OSError, ValueError, InvalidRequest):
                self.errors.append({"binding_id": identity, "code": "native_memory_file_unavailable"})
        self.revision = tuple((identity, version) for identity, _, _, version, _, _ in self.files)
        self.complete = not self.errors

    def search(self, query, limit, offset, deadline):
        terms = _query_terms(query)
        ranked = []
        clipped = 0
        for position, (identity, client, title, version, lines, basis) in enumerate(self.files):
            if time.monotonic() > deadline:
                raise SourceFailure("deadline_exceeded")
            clipped += max(0, len(lines) - NATIVE_LINES)
            for number, line in enumerate(lines[:NATIVE_LINES], 1):
                if score := _score(line, terms):
                    ranked.append((-score, position, number, identity, client, title, version, line, basis))
        ranked.sort(key=lambda item: item[:3])
        hits = []
        for score, _, number, identity, client, title, version, line, basis in ranked[offset:offset + limit]:
            locator = {"kind": "markdown_lines", "start_line": number, "end_line": number, "basis": basis}
            hits.append(_hit("native_memory", f"{identity}:{version}:{number}", title, line, locator,
                             {"binding_id": identity, "revision": version, "line": number},
                             "client_auto_memory", -score, version, client))
        coverage = {"scope": "explicit_client_files", "files_read": len(self.files),
                    "source_errors": self.errors, "lines_after_limit": clipped,
                    "complete": not self.errors and not clipped}
        return hits, len(ranked), coverage


def material_get(binding, ref, *, start=0, end=None, budget=65536):
    if not isinstance(ref, dict) or ref.get("reader") != READER:
        raise InvalidRequest("Use a material reference returned by context.query.")
    origin = ref.get("origin")
    if origin == "documents":
        if ref.keys() != {"reader", "origin", "document_id", "unit"}:
            raise InvalidRequest("An imported document reference requires an exact document ID and unit.")
        if binding is None or not binding.document_imports:
            raise AccessDenied("Imported documents are not bound for this task.")
        identifier(ref["document_id"])
        index = ref["unit"]
        integer(index)
        try:
            with closing(open_memory(binding.memory_home)) as db:
                row = db.execute(SELECT_DOCUMENTS + " AND id=?",
                                 (NAMESPACE, workspace_key(binding.workspace), ref["document_id"])).fetchone()
        except (OSError, sqlite3.Error):
            raise Unavailable("The original document archive is unavailable.") from None
        if row is None:
            raise NotFound("Document revision is unavailable in this workspace.")
        value = _document_value(row)
        if index >= len(value["units"]):
            raise NotFound("Document unit is unavailable in this revision.")
        unit = value["units"][index]
        locator, text = unit["locator"], unit["text"]
        role, title = "explicitly_imported_document", value["file"]
    elif origin == "native_memory":
        if ref.keys() != {"reader", "origin", "binding_id", "revision", "line"}:
            raise InvalidRequest("A native memory reference requires an exact binding, revision and line.")
        identifier(ref["binding_id"])
        identifier(ref["revision"])
        number = ref["line"]
        integer(number, 1)
        selected = next((item for item in binding.native_memory_files if item[0] == ref["binding_id"]), None) if binding else None
        if selected is None:
            raise AccessDenied("The native memory file is outside the trusted binding.")
        _, _, path = selected
        try:
            version, lines, basis = _native_file(path)
        except (OSError, ValueError, InvalidRequest):
            raise Unavailable("The bound native memory file is unavailable.") from None
        if version != ref["revision"]:
            raise StaleSnapshot("The native memory file changed. Query the current version again.")
        if number > min(NATIVE_LINES, len(lines)):
            raise NotFound("Native memory line is unavailable in this revision.")
        locator = {"kind": "markdown_lines", "start_line": number, "end_line": number, "basis": basis}
        text, role, title = lines[number - 1], "client_auto_memory", path.name
    else:
        raise InvalidRequest("Unsupported material source.")

    integer(start)
    raw = text.encode("utf-8")
    end = len(raw) if end is None else end
    integer(end)
    if not start <= end <= len(raw):
        raise InvalidRequest("Material byte range is outside the processed source.")
    try:
        content = raw[start:end].decode("utf-8")
    except UnicodeError:
        raise InvalidRequest("Material byte range must align to UTF-8 characters.") from None
    result = {"schema": "contextrail.material-read/v1", "ref": ref, "locator": locator,
              "title": title, "evidence_role": role, "trust": "untrusted_source_data",
              "range": {"start": start, "end": end, "total_bytes": len(raw)}, "content": content}
    if len(canonical(result).encode("utf-8")) > budget:
        raise BudgetExceeded("Material exceeds the response limit. Read a narrower byte range.")
    return result
