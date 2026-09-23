"""Deterministic evidence ranking for hosts that cannot hand-author Selection.

This module deliberately performs lexical retrieval only.  It makes selection
repeatable and inspectable, but it does not claim semantic recall or replace a
host's explicit must-read evidence.  Callers should evaluate its quality with
the paired A/C gate before adopting a configuration for a task class.
"""

from dataclasses import dataclass
import math
import re

from .errors import AccessDenied, InvalidRequest, Unavailable
from .chunking import EvidenceChunk, StructuralChunker, selections_for_chunks
from .models import Ref, Scope, Selection, integer, unicode_text
from .store import Store


_TERM = re.compile(r"[a-z0-9_./:=+\-]{2,}|[\u4e00-\u9fff]+", re.IGNORECASE)


def _terms(value: str) -> tuple[str, ...]:
    unicode_text(value)
    terms: list[str] = []
    for match in _TERM.finditer(value):
        token = match.group(0).casefold()
        if "\u4e00" <= token[0] <= "\u9fff":
            # Chinese does not have whitespace word boundaries.  Preserve the
            # full phrase for exact matches and add overlapping bigrams for
            # useful partial recall without pretending this is semantic search.
            terms.append(token)
            terms.extend(token[index:index + 2] for index in range(len(token) - 1))
        else:
            terms.append(token)
    return tuple(dict.fromkeys(terms))


@dataclass(frozen=True)
class RankedEvidence:
    ref: Ref
    score: float
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class SelectionPlan:
    """A reviewable, snapshot-ready plan produced from a retrieval query."""

    selections: tuple[Selection, ...]
    ranked: tuple[RankedEvidence, ...]
    unmatched_terms: tuple[str, ...]
    candidate_truncated: bool = False


@dataclass(frozen=True)
class RankedChunk:
    chunk: EvidenceChunk
    score: float
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class ChunkSelectionPlan:
    """Reviewable chunk candidates and their compiler-ready references."""

    selections: tuple[Selection, ...]
    ranked: tuple[RankedChunk, ...]
    unmatched_terms: tuple[str, ...]
    candidate_truncated: bool = False
    recovery_dependencies: tuple[tuple[Ref, tuple[Ref, ...]], ...] = ()


class LexicalEvidenceSelector:
    """Rank current UTF-8 text artifacts with a local BM25-style score.

    Explicit ``must_include`` refs are always emitted as required selections,
    regardless of their score.  Optional results are only candidates; the
    existing Compiler still enforces the final packet budget and keeps omitted
    evidence recoverable through the cold-read tools.
    """

    def __init__(self, store: Store, *, max_artifacts: int = 10_000):
        if not isinstance(store, Store):
            raise InvalidRequest("Lexical selector requires a ContextRail store.")
        integer(max_artifacts, 1)
        if max_artifacts > 10_000:
            raise InvalidRequest("Lexical selector artifact limit exceeds 10000.")
        self.store = store
        self.max_artifacts = max_artifacts

    def select(self, scope: Scope, query: str, *, must_include: tuple[Ref, ...] = (),
               cache_stable: tuple[Ref, ...] = (), max_optional: int = 20,
               min_score: float = 0.01) -> SelectionPlan:
        if not isinstance(scope, Scope):
            raise InvalidRequest("Lexical selector requires a Scope.")
        if not isinstance(query, str) or not query.strip():
            raise InvalidRequest("Retrieval query must be nonempty.")
        query_terms = _terms(query)
        if not query_terms:
            raise InvalidRequest("Retrieval query contains no searchable terms.")
        if not isinstance(must_include, tuple) or any(not isinstance(ref, Ref) for ref in must_include):
            raise InvalidRequest("must_include must be a tuple of evidence references.")
        if not isinstance(cache_stable, tuple) or any(not isinstance(ref, Ref) for ref in cache_stable):
            raise InvalidRequest("cache_stable must be a tuple of evidence references.")
        if len(set(must_include)) != len(must_include) or len(set(cache_stable)) != len(cache_stable):
            raise InvalidRequest("Duplicate mandatory evidence reference.")
        if not set(cache_stable).issubset(must_include):
            raise AccessDenied("Cache-stable evidence must also be explicitly required.")
        integer(max_optional)
        if max_optional > self.max_artifacts:
            raise InvalidRequest("Optional selection limit exceeds selector artifact limit.")
        if not isinstance(min_score, (float, int)) or not math.isfinite(min_score) or min_score < 0:
            raise InvalidRequest("min_score must be a finite nonnegative number.")

        # One consistent read snapshot across enumeration and every page read, so
        # a concurrent invalidate drops a candidate instead of aborting ranking.
        with self.store.read_transaction():
            total_candidates = self.store.current_ref_count(scope)
            refs = self.store.current_refs(scope, limit=self.max_artifacts)
            known = set(refs)
            if not set(must_include).issubset(known):
                raise AccessDenied("Required evidence is not a readable current artifact in this scope.")
            documents = []
            for ref in refs:
                try:
                    artifact = self.store.get(scope, ref)
                except Unavailable:
                    continue
                try:
                    text = artifact.content.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                documents.append((ref, text.casefold(), ref.name.casefold()))
        document_frequency = {term: sum(term in text or term in name for _, text, name in documents)
                              for term in query_terms}
        average_length = sum(max(1, len(text)) for _, text, _ in documents) / max(1, len(documents))
        ranked = []
        for ref, text, name in documents:
            matched = tuple(term for term in query_terms if term in text or term in name)
            if not matched:
                continue
            length = max(1, len(text))
            score = 0.0
            for term in matched:
                frequency = text.count(term)
                # BM25-style tf saturation and length normalization prevent a
                # large log artifact repeating a term from dominating a short,
                # directly relevant evidence page.  Names retain an exact
                # channel for paths, symbols, versions, and error codes.
                tf = frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * length / average_length))
                idf = math.log(1.0 + (len(documents) - document_frequency[term] + 0.5) /
                               (document_frequency[term] + 0.5))
                score += idf * (tf + (4.0 if term in name else 0.0))
            ranked.append(RankedEvidence(ref, score, matched))
        ranked.sort(key=lambda item: (-item.score, item.ref.name, item.ref.revision))
        optional = [item for item in ranked if item.ref not in must_include and item.score >= min_score][:max_optional]
        selections = [Selection(ref, required=True, cache_stable=ref in cache_stable) for ref in must_include]
        selections.extend(Selection(item.ref, priority=max(1, round(item.score * 1_000))) for item in optional)
        matched_terms = {term for item in ranked for term in item.matched_terms}
        return SelectionPlan(tuple(selections), tuple(ranked), tuple(term for term in query_terms if term not in matched_terms),
                             total_candidates > len(refs))


class ChunkedEvidenceSelector:
    """Rank structure-aware spans while retaining exact artifact provenance."""

    def __init__(self, store: Store, *, chunker: StructuralChunker | None = None, max_artifacts: int = 10_000):
        if not isinstance(store, Store):
            raise InvalidRequest("Chunked selector requires a ContextRail store.")
        integer(max_artifacts, 1)
        if max_artifacts > 10_000:
            raise InvalidRequest("Chunked selector artifact limit exceeds 10000.")
        self.store, self.chunker, self.max_artifacts = store, chunker or StructuralChunker(), max_artifacts

    def select(self, scope: Scope, query: str, *, must_include: tuple[EvidenceChunk, ...] = (),
               max_optional: int = 20, min_score: float = 0.01) -> ChunkSelectionPlan:
        if not isinstance(scope, Scope) or not isinstance(query, str) or not query.strip():
            raise InvalidRequest("Chunked retrieval requires a scope and nonempty query.")
        if not isinstance(must_include, tuple) or any(not isinstance(chunk, EvidenceChunk) for chunk in must_include):
            raise InvalidRequest("must_include must contain evidence chunks.")
        integer(max_optional)
        if max_optional > self.max_artifacts:
            raise InvalidRequest("Optional selection limit exceeds selector artifact limit.")
        if not isinstance(min_score, (float, int)) or not math.isfinite(min_score) or min_score < 0:
            raise InvalidRequest("min_score must be a finite nonnegative number.")
        terms = _terms(query)
        if not terms:
            raise InvalidRequest("Retrieval query contains no searchable terms.")
        # One consistent read snapshot across enumeration, chunking and every span
        # read, so a concurrent invalidate cannot abort the whole selection.
        with self.store.read_transaction():
            total = self.store.current_ref_count(scope)
            refs = self.store.current_refs(scope, limit=self.max_artifacts)
            chunks: list[tuple[EvidenceChunk, str]] = []
            for ref in refs:
                try:
                    artifact = self.store.get(scope, ref)
                    chunk_specs = self.chunker.chunk(ref, artifact.content, source_sha256=artifact.sha256,
                                                     media_type=artifact.media_type)
                    for chunk in chunk_specs:
                        text = self.store.get(scope, chunk.ref).content.decode("utf-8", errors="replace").casefold()
                        chunks.append((chunk, text))
                except Unavailable:
                    continue
        frequencies = {term: sum(term in text or term in chunk.path.casefold() for chunk, text in chunks) for term in terms}
        average = sum(max(1, len(text)) for _, text in chunks) / max(1, len(chunks))
        ranked: list[RankedChunk] = []
        for chunk, text in chunks:
            path = chunk.path.casefold()
            matched = tuple(term for term in terms if term in text or term in path)
            if not matched:
                continue
            score = 0.0
            for term in matched:
                count = text.count(term)
                tf = count * 2.2 / (count + 1.2 * (0.25 + 0.75 * max(1, len(text)) / average))
                idf = math.log(1.0 + (len(chunks) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
                score += idf * (tf + (4.0 if term in path else 0.0))
            ranked.append(RankedChunk(chunk, score, matched))
        ranked.sort(key=lambda item: (-item.score, item.chunk.ref.name, item.chunk.ref.start))
        mandatory = StructuralChunker.merge(must_include)
        optional = [item.chunk for item in ranked if item.chunk not in mandatory and item.score >= min_score][:max_optional]
        selected_chunks = StructuralChunker.merge(mandatory + tuple(optional))
        matched_terms = {term for item in ranked for term in item.matched_terms}
        dependencies = tuple((chunk.ref, chunk.dependencies) for chunk in selected_chunks if chunk.dependencies)
        return ChunkSelectionPlan(selections_for_chunks(selected_chunks), tuple(ranked),
                                  tuple(term for term in terms if term not in matched_terms), total > len(refs), dependencies)
