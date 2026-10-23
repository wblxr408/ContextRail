"""Read-only intent retrieval restricted to a saved result's original streams."""

from collections import Counter
from dataclasses import asdict
import json

from .errors import BudgetExceeded, InvalidRequest
from .models import Ref, canonical, identifier, integer, unicode_text


SAMPLING = "first_4_indexed_spans_per_stream"


def validate_query(query):
    if not isinstance(query, str) or not query.strip() or len(query) > 1024:
        raise InvalidRequest("Intent must contain 1-1024 characters.")
    unicode_text(query)


def _ref(value):
    if (not isinstance(value, dict) or not {"name", "revision"} <= value.keys()
            or value.keys() - {"name", "revision", "start", "end"}):
        raise InvalidRequest("Expected a complete original result reference.")
    ref = Ref(**value)
    if ref.start != 0 or ref.end is not None:
        raise InvalidRequest("Result and stream references must name a complete revision.")
    return ref


def _manifest(store, scope, value):
    ref = _ref(value)
    artifact = store.get(scope, ref)
    if len(artifact.content) > 2 * 1024 * 1024:
        raise InvalidRequest("Saved result manifest exceeds 2 MiB.")
    try:
        manifest = json.loads(artifact.content)
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidRequest("Saved result manifest is invalid JSON.") from None
    if not isinstance(manifest, dict) or manifest.get("schema") != "contextrail.tool-result.v1" or manifest.get("scope") != asdict(scope):
        raise InvalidRequest("Manifest must be a saved result in exactly the bound scope.")
    if manifest.get("status") not in ("succeeded", "failed", "timed_out", "partial", "unknown", "no_match"):
        raise InvalidRequest("Saved result manifest requires a recognized execution status.")
    identifier(manifest.get("call_id"))
    identifier(manifest.get("tool"))
    prefix = "tool-result/" + manifest["call_id"]
    if ref.name != prefix + "/result":
        raise InvalidRequest("Result reference does not match its saved call identity.")
    sources = []
    for stream in ("stdout", "stderr"):
        entry = manifest.get(stream)
        if not isinstance(entry, dict) or set(entry) != {"ref", "bytes"}:
            raise InvalidRequest("Saved result requires both original stream descriptors.")
        source = _ref(entry["ref"])
        integer(entry["bytes"])
        if source.name != prefix + "/" + stream:
            raise InvalidRequest("Stream reference does not match the saved call identity.")
        actual = store.db.execute("SELECT size FROM artifacts WHERE scope=? AND name=? AND revision=?", (scope.key, source.name, source.revision)).fetchone()
        if actual is None or actual["size"] != entry["bytes"]:
            raise InvalidRequest("Original stream descriptor does not match saved evidence.")
        sources.append({"stream": stream, "ref": asdict(source), "bytes": entry["bytes"]})
    return ref, manifest, sources


def _hints(store, scope, sources):
    from .indexing import INDEXER
    counts = Counter()
    for source in sources:
        ref = Ref(**source["ref"])
        rows = store.db.execute("SELECT f.terms FROM evidence_fts f JOIN heads h "
            "ON h.scope=f.scope AND h.name=f.name AND h.revision=f.revision "
            "JOIN evidence_index_state s ON s.scope=h.scope AND s.name=h.name AND s.revision=h.revision "
            "JOIN artifacts a ON a.scope=h.scope AND a.name=h.name AND a.revision=h.revision "
            "WHERE f.scope=? AND f.name=? AND f.revision=? AND s.status='indexed' AND s.indexer=? "
            "AND s.source_sha256=a.sha256 AND f.source_sha256=a.sha256 AND a.invalidated=0 "
            "AND (a.expires IS NULL OR a.expires>?) ORDER BY f.start_byte LIMIT 4",
            (scope.key, ref.name, ref.revision, INDEXER, store.clock()))
        for row in rows:
            counts.update(row["terms"].split())
    result = []
    for encoded, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        try:
            token = bytes.fromhex(encoded[1:]).decode("utf-8")
        except (ValueError, UnicodeError):
            continue
        if 2 <= len(token) <= 64:
            result.append(token)
        if len(result) == 12:
            break
    return result


def search_result(store, scope, session, result_ref, query, *, limit=5, offset=0, budget=8192, context_chars=80, window_limit=3):
    validate_query(query)
    integer(limit, 1)
    integer(budget, 1)
    if limit > 20:
        raise InvalidRequest("Result search limit cannot exceed 20.")
    with store.read_transaction():
        store.session(scope, session)
        result_ref, manifest, sources = _manifest(store, scope, result_ref)
        indexed = store.search_indexed(scope, query, limit=limit, offset=offset, budget=max(budget, 16384),
                                      within=tuple(Ref(**source["ref"]) for source in sources), context_chars=context_chars, window_limit=window_limit)
        hits = []
        for hit in indexed["hits"]:
            stream = next(source["stream"] for source in sources if source["ref"]["name"] == hit["ref"]["name"])
            hits.append({**hit, "stream": stream})
        hints = _hints(store, scope, sources)
    result = {"schema": "contextrail.result-search.v1", "result_ref": asdict(result_ref), "call_id": manifest["call_id"],
              "execution_status": manifest["status"], "sources": sources, "query": query, "status": indexed["status"],
              "index": indexed["index"], "total": indexed["total"], "offset": offset, "hits": hits,
              "searchable_terms": hints, "hints_sampling": SAMPLING, "ranking": indexed["ranking"],
              "match": "lexical_candidates_not_an_anomaly_verdict", "trust": "untrusted_original_streams",
              "read_tool": "context.get", "continue_tool": "context.result_search"}
    while True:
        result.update(returned=len(hits), omitted=max(0, result["total"] - offset - len(hits)),
                      next_offset=offset + len(hits) if hits and offset + len(hits) < result["total"] else None)
        if len(canonical(result).encode("utf-8")) <= budget:
            return result
        if hints:
            hints.pop()
        elif hits:
            hits.pop()
        else:
            raise BudgetExceeded("Result search metadata and original stream references cannot fit the response budget.")


def compact_intent(query, body=None, *, error=None, reserve=False):
    status = body["status"] if body is not None else ("unsupported_query" if error and error.code == "invalid_request" else
             "budget_limited" if error and error.code == "budget_exceeded" else "unavailable" if error and error.code == "unavailable" else "retrieval_failed")
    result = {"query": query, "status": status, "total": body["total"] if body else None,
              "returned": len(body["hits"]) if body else 0, "omitted": body["omitted"] if body else None,
              "index_complete": body["index"]["complete"] if body else False,
              "pending_sources": body["index"]["pending"] if body else None,
              "excluded_sources": body["index"]["excluded"] if body else None,
              "hits": body["hits"] if body else [], "searchable_terms": body["searchable_terms"] if body else [],
              "hints_sampling": SAMPLING, "continuation_tool": "context.result_search", "error_code": error.code if error else None}
    if reserve:
        result.update(status="x" * 32, error_code="x" * 64)
        for key in ("total", "returned", "omitted", "pending_sources", "excluded_sources"):
            result[key] = 2**63 - 1
    return result
