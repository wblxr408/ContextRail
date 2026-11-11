"""Derived current-revision FTS5 spans; original artifacts remain authoritative."""

from dataclasses import asdict
from contextlib import nullcontext
from difflib import SequenceMatcher
import re
import sqlite3

from .chunking import StructuralChunker, uncovered_spans
from .errors import BudgetExceeded, InvalidRequest, NotFound, Unavailable
from .models import Ref, canonical, integer, unicode_text


INDEXER = "lexical-spans-v3"
LEGACY_INDEXER = "lexical-spans-v2"
MAX_INPUT = 16 * 1024 * 1024
MAX_SPANS = 4096
SPAN_BYTES = 8192
FTS_SCHEMA = """CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(
 scope UNINDEXED, name UNINDEXED, revision UNINDEXED,
 start_byte UNINDEXED, end_byte UNINDEXED, source_sha256 UNINDEXED, path UNINDEXED,
 title, terms, tokenize='ascii')"""


def initialize(store):
    try:
        store.db.execute(FTS_SCHEMA)
        store.fts_available = True
    except sqlite3.OperationalError as exc:
        if "no such module: fts5" not in str(exc).lower():
            raise
        store.fts_available = False
    # Only metadata is backfilled at migration/open. Never silently scan all blobs.
    store.db.execute("INSERT OR IGNORE INTO evidence_index_state "
                     "SELECT h.scope,h.name,h.revision,a.sha256,?,'pending','not_backfilled',0 "
                     "FROM heads h JOIN artifacts a ON a.scope=h.scope AND a.name=h.name AND a.revision=h.revision", (INDEXER,))


_FUZZY_WORD = re.compile(r"[a-z][a-z_]{3,63}")


def _term(term, prefix="t"):
    return prefix + term.encode("utf-8").hex()


def _grams(word):
    return dict.fromkeys(word[index:index + 3] for index in range(len(word) - 2))


def _encoded(text):
    from .retrieval import han_term, lexical_tokens

    terms, grams = [], {}
    for start, end, word in lexical_tokens(text):
        if len(word.encode("utf-8")) <= 512:
            terms.append(_term(word))
        if "/" in word:
            terms.extend(_term(part) for part in word.split("/") if len(part) > 1)
        if han_term(word) and len(word) > 1:
            if len(word) > 2:
                terms.extend(_term(word[index:index + 2]) for index in range(len(word) - 1))
            terms.extend(_term(character) for character in dict.fromkeys(word))
        elif len(grams) < 384 and _FUZZY_WORD.fullmatch(word) and not text[start:end].isupper():
            grams.update(_grams(word))
    return " ".join((*terms, *(_term(gram, "g") for gram in list(grams)[:384])))


def _lookup_expression(query, spans, used):
    from .retrieval import han_term

    expressions, literals = [], []
    for word in used:
        exact = f'"{_term(word)}"'
        if han_term(word) and 2 < len(word) <= 64:
            bigrams = " ".join(_term(word[index:index + 2]) for index in range(len(word) - 1))
            expressions.append(f'({exact} OR "{bigrams}")')
        else:
            expressions.append(exact)
        if not han_term(word) and (
            any(char in word for char in "/.=:") or any(char.isdigit() for char in word)
            or any(end - start >= 3 and query[start:end].isupper() for start, end, value in spans if value == word)
        ):
            literals.append(exact)
    if literals:
        return " AND ".join(literals), "all_explicit_literals"
    if 1 < len(expressions) <= 6 and all(not item.startswith("(") for item in expressions):
        expressions.insert(0, "NEAR(" + " ".join(expressions) + ", 8)")
    return " OR ".join(expressions), "lexical_terms_with_proximity"


def _suggestions(store, scope, word, joins, args):
    from .retrieval import lexical_tokens

    # These candidates never populate exact hits. Hex tokens are built here,
    # with no caller-supplied MATCH syntax or unrestricted vocabulary reads.
    grams = _grams(word)
    match = " OR ".join([f'"{_term(word)}"*', *(f'"{_term(gram, "g")}"' for gram in grams)])
    rows = store.db.execute(
        "SELECT evidence_fts.*" + joins +
        " ORDER BY bm25(evidence_fts),evidence_fts.name,evidence_fts.start_byte LIMIT 24",
        (*args[:2], match, *args[3:]),
    ).fetchall()
    candidates, compared = [], set()
    for row in rows:
        source = Ref(row["name"], row["revision"], row["start_byte"], row["end_byte"])
        text = store.get(scope, source).content.decode("utf-8")
        for start, end, token in lexical_tokens(text):
            spelling = text[start:end]
            if token in compared or token == word or not _FUZZY_WORD.fullmatch(token) or spelling.isupper():
                continue
            prefix = token.startswith(word)
            if not prefix and (abs(len(word) - len(token)) > 2 or not any(gram in token for gram in grams)):
                continue
            if len(compared) == 512:
                break
            compared.add(token)
            ratio = SequenceMatcher(None, word, token, autojunk=False).ratio()
            if not prefix and ratio < 0.75:
                continue
            begin = source.start + len(text[:start].encode("utf-8"))
            ref = Ref(source.name, source.revision, begin, begin + len(spelling.encode("utf-8")))
            candidates.append((not prefix, -ratio, token, {
                "text": spelling, "reason": "identifier_prefix" if prefix else "similar_spelling",
                "ref": asdict(ref),
            }))
        if len(compared) == 512:
            break
    return [item[-1] for item in sorted(candidates)[:5]]


def _spans(name, revision, content, sha, media_type):
    ref = Ref(name, revision)
    chunks = StructuralChunker().chunk(ref, content, source_sha256=sha, media_type=media_type)
    ranges = {(chunk.ref.start, chunk.ref.end if chunk.ref.end is not None else len(content)): chunk.path for chunk in chunks}
    for _, _, start, end in uncovered_spans(ref, len(content), chunks):
        ranges.setdefault((start, end), "<raw_gap>")
    if not ranges:
        return [(0, 0, name, "")]
    result = []
    seen = set()
    for (start, end), path in sorted(ranges.items()):
        while start < end:
            stop = min(end, start + SPAN_BYTES)
            while stop < end and content[stop] & 0xC0 == 0x80:
                stop -= 1
            if stop < end:
                line = content.rfind(b"\n", start, stop)
                if line >= start + SPAN_BYTES // 2:
                    stop = line + 1
            text = content[start:stop].decode("utf-8")
            if (start, stop) not in seen:
                result.append((start, stop, path, _encoded(text)))
                seen.add((start, stop))
            if len(result) > MAX_SPANS:
                return None
            start = stop
    return result


def _state(store, scope_key, name, revision, sha, status, reason, spans=0):
    store.db.execute("INSERT INTO evidence_index_state VALUES(?,?,?,?,?,?,?,?) "
                     "ON CONFLICT(scope,name) DO UPDATE SET revision=excluded.revision,source_sha256=excluded.source_sha256,"
                     "indexer=excluded.indexer,status=excluded.status,reason=excluded.reason,spans=excluded.spans",
                     (scope_key, name, revision, sha, INDEXER, status, reason, spans))


def update(store, scope_key, name, revision, sha, content, media_type):
    if store.fts_available:
        store.db.execute("DELETE FROM evidence_fts WHERE scope=? AND name=?", (scope_key, name))
    if not store.fts_available:
        return _state(store, scope_key, name, revision, sha, "pending", "fts5_unavailable")
    if len(content) > MAX_INPUT:
        return _state(store, scope_key, name, revision, sha, "pending", "source_over_16mib")
    try:
        text = content.decode("utf-8")
        unicode_text(text)
    except (UnicodeError, InvalidRequest):
        return _state(store, scope_key, name, revision, sha, "excluded", "non_utf8")
    spans = _spans(name, revision, content, sha, media_type)
    if spans is None:
        return _state(store, scope_key, name, revision, sha, "pending", "span_limit")
    source = store.db.execute("SELECT path FROM directory_files WHERE scope=? AND name=? LIMIT 1", (scope_key, name)).fetchone()
    if source:
        spans = [span for span in spans if span[0] == span[1] or content[span[0]:span[1]].strip()]
    title = _source_title(name, source[0]) if source else _encoded(name)
    store.db.executemany("INSERT INTO evidence_fts(scope,name,revision,start_byte,end_byte,source_sha256,path,title,terms) "
                         "VALUES(?,?,?,?,?,?,?,?,?)",
                         [(scope_key, name, revision, start, end, sha, path, title, terms) for start, end, path, terms in spans])
    _state(store, scope_key, name, revision, sha, "indexed", "", len(spans))


def label_source(store, scope_key, name, path, content):
    if store.fts_available:
        store.db.execute("UPDATE evidence_fts SET title=? WHERE scope=? AND name=?", (_source_title(name, path), scope_key, name))
        rows = store.db.execute("SELECT rowid,start_byte,end_byte FROM evidence_fts WHERE scope=? AND name=?", (scope_key, name)).fetchall()
        blank = [row[0] for row in rows if row[1] != row[2] and not content[row[1]:row[2]].strip()]
        store.db.executemany("DELETE FROM evidence_fts WHERE rowid=?", [(rowid,) for rowid in blank])
        store.db.execute("UPDATE evidence_index_state SET spans=? WHERE scope=? AND name=? AND status='indexed'", (len(rows) - len(blank), scope_key, name))


def _source_title(name, path):
    return _encoded(name + " " + path + " " + " ".join(path.replace("\\", "/").split("/")))


def invalidate(store, scope_key, name, revision):
    if store.fts_available:
        store.db.execute("DELETE FROM evidence_fts WHERE scope=? AND name=? AND revision=?", (scope_key, name, revision))
    store.db.execute("UPDATE evidence_index_state SET status='unavailable',reason='invalidated',spans=0 "
                     "WHERE scope=? AND name=? AND revision=?", (scope_key, name, revision))


def purge(store, scope_key):
    rows = store.db.execute("SELECT s.name,s.revision FROM evidence_index_state s JOIN artifacts a "
                            "ON a.scope=s.scope AND a.name=s.name AND a.revision=s.revision "
                            "WHERE s.scope=? AND (a.invalidated=1 OR a.expires<=?)", (scope_key, store.clock())).fetchall()
    for row in rows:
        invalidate(store, scope_key, row["name"], row["revision"])


def status(store, scope):
    store._task(scope)
    now = store.clock()
    rows = store.db.execute("SELECT a.revision,a.sha256,a.invalidated,a.expires,s.revision AS indexed_revision,"
                            "s.source_sha256,s.indexer,s.status,s.reason,s.spans FROM heads h JOIN artifacts a "
                            "ON a.scope=h.scope AND a.name=h.name AND a.revision=h.revision "
                            "LEFT JOIN evidence_index_state s ON s.scope=h.scope AND s.name=h.name WHERE h.scope=?", (scope.key,))
    counts = dict(indexed=0, pending=0, excluded=0, unavailable=0, spans=0)
    reasons = {}
    for row in rows:
        if row["invalidated"] or row["expires"] is not None and row["expires"] <= now:
            state, reason = "unavailable", "invalidated_or_expired"
        elif (row["indexed_revision"] != row["revision"] or row["source_sha256"] != row["sha256"]):
            state, reason = "pending", "source_not_indexed"
        elif row["indexer"] != INDEXER:
            state, reason = "pending", "lexical_upgrade_pending" if row["indexer"] == LEGACY_INDEXER else "source_not_indexed"
        elif not store.fts_available:
            state, reason = "pending", "fts5_unavailable"
        else:
            state, reason = row["status"], row["reason"]
        counts[state] += 1
        if state == "indexed":
            counts["spans"] += row["spans"]
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
    directory_pending = store.db.execute("SELECT COUNT(*) FROM directory_sources s JOIN directory_runs r ON r.scope=s.scope AND r.id=s.active_run "
                                         "WHERE s.scope=? AND r.phase<>'complete'", (scope.key,)).fetchone()[0]
    return {"backend": "fts5" if store.fts_available else "unavailable", "indexer": INDEXER,
            **counts, "directory_pending": directory_pending,
            "complete": store.fts_available and counts["pending"] == 0 and directory_pending == 0,
            "coverage": "current_readable_utf8_within_declared_limits", "reasons": reasons}


def rebuild_contents(store, scope):
    """Rebuild one partition inside its caller's authorized write transaction."""
    if not store.fts_available:
        raise Unavailable("SQLite FTS5 is unavailable; original evidence remains readable.")
    store.db.execute("DELETE FROM evidence_fts WHERE scope=?", (scope.key,))
    store.db.execute("DELETE FROM evidence_index_state WHERE scope=?", (scope.key,))
    rows = store.db.execute("SELECT a.* FROM heads h JOIN artifacts a "
                            "ON a.scope=h.scope AND a.name=h.name AND a.revision=h.revision WHERE h.scope=? ORDER BY a.name",
                            (scope.key,))
    now = store.clock()
    for row in rows:
        if row["invalidated"] or row["expires"] is not None and row["expires"] <= now:
            _state(store, scope.key, row["name"], row["revision"], row["sha256"], "unavailable", "invalidated_or_expired")
            continue
        artifact = store.get(scope, Ref(row["name"], row["revision"]))
        update(store, scope.key, row["name"], row["revision"], row["sha256"], artifact.content, row["media_type"])


def rebuild(store, scope, lease):
    with store.transaction():
        store._lease(scope, lease)
        rebuild_contents(store, scope)
        result = status(store, scope)
        store._event(scope, "evidence_index.rebuilt", indexed=result["indexed"], pending=result["pending"], indexer=INDEXER)
        return result


def source_status(store, scope, refs):
    """Coverage for explicit original stream revisions, not the whole task."""
    counts = dict(indexed=0, pending=0, excluded=0, unavailable=0, spans=0)
    reasons = {}
    now = store.clock()
    for ref in refs:
        row = store.db.execute("SELECT a.*,h.revision AS head_revision,s.revision AS indexed_revision,"
            "s.source_sha256,s.indexer,s.status,s.reason,s.spans,b.content IS NOT NULL AS payload_present "
            "FROM artifacts a LEFT JOIN heads h ON h.scope=a.scope AND h.name=a.name "
            "LEFT JOIN evidence_index_state s ON s.scope=a.scope AND s.name=a.name "
            "LEFT JOIN blobs b ON b.scope=a.scope AND b.sha256=a.sha256 "
            "WHERE a.scope=? AND a.name=? AND a.revision=?", (scope.key, ref.name, ref.revision)).fetchone()
        if row is None:
            raise NotFound("An original result stream is missing in the bound task.")
        if row["invalidated"] or not row["payload_present"] or row["expires"] is not None and row["expires"] <= now:
            state, reason = "unavailable", "source_unavailable"
        elif row["head_revision"] != ref.revision:
            state, reason = "pending", "historical_revision_not_indexed"
        elif (row["indexed_revision"] != ref.revision or row["source_sha256"] != row["sha256"]):
            state, reason = "pending", "source_not_indexed"
        elif row["indexer"] != INDEXER:
            state, reason = "pending", "lexical_upgrade_pending" if row["indexer"] == LEGACY_INDEXER else "source_not_indexed"
        elif not store.fts_available:
            state, reason = "pending", "fts5_unavailable"
        else:
            state, reason = row["status"], row["reason"]
        counts[state] += 1
        if state == "indexed":
            counts["spans"] += row["spans"]
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
    return {"backend": "fts5" if store.fts_available else "unavailable", "indexer": INDEXER, **counts,
            "complete": store.fts_available and counts["pending"] == 0 and counts["unavailable"] == 0,
            "coverage": "explicit_original_result_streams", "reasons": reasons}


def search(store, scope, query, *, limit=20, offset=0, budget=8192, within=None, context_chars=80, window_limit=3):
    from .hit_windows import project_windows, validate_options
    validate_options(context_chars, window_limit)
    if not isinstance(query, str) or not query.strip() or len(query) > 1024:
        raise InvalidRequest("Indexed query must contain 1-1024 characters.")
    unicode_text(query)
    integer(limit, 1)
    integer(offset)
    integer(budget, 1)
    if limit > 100 or offset > 10000:
        raise InvalidRequest("Indexed search limit exceeds 100 or offset exceeds 10000; refine the query.")
    if within is not None and (not isinstance(within, tuple) or not 1 <= len(within) <= 2 or
            any(not isinstance(ref, Ref) or ref.start != 0 or ref.end is not None for ref in within)):
        raise InvalidRequest("Result search requires one or two complete explicit source revisions.")
    from .retrieval import han_term, lexical_tokens
    spans = tuple(lexical_tokens(query))
    tokens = tuple(dict.fromkeys(word for _, _, word in spans if len(word.encode("utf-8")) <= 512))
    if not tokens:
        raise InvalidRequest("Query has no supported lexical terms; use literal search or exact read.")
    used = tokens[:16]
    # Encoded complete words make MATCH an expression created by this module,
    # never a SQL/FTS expression supplied by the model or evidence text.
    match, match_policy = _lookup_expression(query, spans, used)
    joins = (" FROM evidence_fts JOIN heads h ON h.scope=evidence_fts.scope AND h.name=evidence_fts.name AND h.revision=evidence_fts.revision "
             "JOIN artifacts a ON a.scope=h.scope AND a.name=h.name AND a.revision=h.revision "
             "JOIN evidence_index_state s ON s.scope=h.scope AND s.name=h.name AND s.revision=h.revision "
             "AND s.source_sha256=a.sha256 AND s.indexer IN (?,?) AND s.status='indexed' "
             "WHERE evidence_fts MATCH ? AND h.scope=? AND a.sha256=evidence_fts.source_sha256 "
             "AND a.invalidated=0 AND (a.expires IS NULL OR a.expires>?)")
    with (nullcontext() if within is not None and store.db.in_transaction else store.read_transaction()):
        coverage = status(store, scope) if within is None else source_status(store, scope, within)
        if not store.fts_available:
            raise Unavailable("SQLite FTS5 is unavailable; use literal search or exact evidence reads.")
        args = (INDEXER, LEGACY_INDEXER, match, scope.key, store.clock())
        if within is not None:
            joins += " AND (" + " OR ".join("(evidence_fts.name=? AND evidence_fts.revision=?)" for ref in within) + ")"
            args += tuple(value for ref in within for value in (ref.name, ref.revision))
        total = store.db.execute("SELECT COUNT(*)" + joins, args).fetchone()[0]
        rows = store.db.execute("SELECT evidence_fts.*,bm25(evidence_fts,0,0,0,0,0,0,0,4,1) AS score" + joins +
                                " ORDER BY score,evidence_fts.name,evidence_fts.start_byte LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
        hits = []
        cached_ref, cached_content = None, None
        for row in rows:
            ref = Ref(row["name"], row["revision"], row["start_byte"], row["end_byte"])
            source_ref = Ref(ref.name, ref.revision)
            if source_ref != cached_ref:
                cached_content = store.get(scope, source_ref).content
                cached_ref = source_ref
            available = set((row["title"] + " " + row["terms"]).split())
            matched = [term for term in used if _term(term) in available or (
                2 < len(term) <= 64 and han_term(term) and
                all(_term(term[i:i + 2]) in available for i in range(len(term) - 1)))]
            hits.append({"ref": asdict(ref), "path": row["path"], "score": -row["score"], "matched_terms": matched,
                         **project_windows(cached_content, ref, row["path"], matched, context_chars=context_chars, window_limit=window_limit)})
            source = store.db.execute("SELECT path FROM directory_files WHERE scope=? AND name=? LIMIT 1", (scope.key, row["name"])).fetchone()
            if source:
                hits[-1]["source_path"] = source[0]
        suggestions = []
        exact_input = query.strip()
        if (total == 0 and offset == 0 and len(used) == 1 and _FUZZY_WORD.fullmatch(used[0])
                and exact_input.casefold() == used[0] and not exact_input.isupper()):
            suggestions = _suggestions(store, scope, used[0], joins, args)
    result = {"schema": "contextrail.indexed-search.v1", "query": query, "terms": list(used), "unused_terms": list(tokens[16:]),
              "ranking": "sqlite_fts5_bm25", "ranking_statistics": "shared_index; compare_only_within_this_query",
              "match": match_policy, "range_unit": "bytes", "trust": "untrusted_evidence_candidates",
              "suggestions": suggestions, "index": coverage,
              "status": "matched" if total else "no_match" if coverage["complete"] else "index_pending",
              "total": total, "offset": offset, "hits": hits}
    while True:
        result.update(returned=len(hits), omitted=max(0, total - offset - len(hits)),
                      next_offset=offset + len(hits) if hits and offset + len(hits) < total and offset + len(hits) <= 10000 else None,
                      continuation="increase_budget_or_refine_query" if total > offset and not hits else "page_or_exact_read")
        if len(canonical(result).encode("utf-8")) <= budget:
            return result
        if suggestions:
            suggestions.pop()
            continue
        if not hits:
            raise BudgetExceeded("Indexed search metadata cannot fit the complete response byte budget.")
        hits.pop()
