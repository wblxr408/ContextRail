"""Optional semantic planning layer over the immutable evidence store.

The semantic layer proposes *structure* (nodes bound to exact byte ranges),
*relations* (typed dependencies between those ranges), and *candidate groups*
(atomic, dependency-closed units of evidence).  It never rewrites, summarizes,
or invents source bytes: every node is a reference into an immutable artifact
revision, exactly like ``EvidenceChunk``.

Two boundaries are deliberate and load-bearing:

* An analyzer/ranker is *host-run and untrusted*.  It only proposes; the core
  validates every reference, hash, and byte range through :func:`validate_index`
  and discards anything that does not resolve.  A model confidence score is a
  diagnostic, never a trusted fact.
* Reference validity proves *provenance*, not *sufficiency*.  A structurally
  complete group can still omit a safety-critical condition, which is why the
  planner records diagnostics and the evaluation keeps deterministic structure
  checks separate from task-quality checks.

This module performs no network or model calls.  The concrete
:class:`StructuralSemanticAnalyzer` and :class:`LexicalCohesionRanker` are
deterministic, local, and reproducible; a host may substitute a model-backed
adapter behind the same protocols.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Protocol

from .chunking import CHUNKER_VERSION, EvidenceChunk, StructuralChunker, uncovered_spans
from .errors import InvalidRequest
from .models import Ref, canonical, digest, identifier, integer, unicode_text


POLICY_VERSION = "semantic-plan-v1"
RELATION_KINDS = frozenset({"requires", "condition", "exception", "definition", "elaboration"})
ENFORCEMENT_LEVELS = frozenset({"hard", "advisory"})
_TERM = re.compile(r"[a-z0-9_./:=+\-]{2,}|[一-鿿]+", re.IGNORECASE)


def _ref_end(ref: Ref, size: int) -> int:
    """Resolve an open-ended ref to a concrete byte end within ``size``."""
    return size if ref.end is None else ref.end


def _terms(value: str) -> tuple[str, ...]:
    """Tokenize like the lexical selector: whole CJK phrase plus overlap bigrams."""
    unicode_text(value)
    terms: list[str] = []
    for match in _TERM.finditer(value):
        token = match.group(0).casefold()
        if "一" <= token[0] <= "鿿":
            terms.append(token)
            terms.extend(token[index:index + 2] for index in range(len(token) - 1))
        else:
            terms.append(token)
    return tuple(dict.fromkeys(terms))


@dataclass(frozen=True)
class SemanticNode:
    """One contiguous source range plus derived navigation metadata.

    ``ref`` is the authoritative byte span; ``label`` and ``kind`` are derived
    and never override the original bytes.  ``id`` is content-addressed so a
    node is stable across re-analysis of the same revision.
    """

    id: str
    ref: Ref
    source_sha256: str
    kind: str
    label: str
    parent_id: str | None = None

    def __post_init__(self) -> None:
        identifier(self.id)
        if not isinstance(self.ref, Ref):
            raise InvalidRequest("Semantic node requires an evidence reference.")
        if not isinstance(self.source_sha256, str) or len(self.source_sha256) != 64:
            raise InvalidRequest("Semantic node source hash must be a SHA-256 digest.")
        try:
            int(self.source_sha256, 16)
        except ValueError:
            raise InvalidRequest("Semantic node source hash must be a SHA-256 digest.") from None
        identifier(self.kind)
        unicode_text(self.label)
        if self.parent_id is not None:
            identifier(self.parent_id)


@dataclass(frozen=True)
class EvidenceRelation:
    """A typed dependency or discourse relation between two nodes.

    ``enforcement`` is ``hard`` only when a host declared it or a deterministic
    parse confirmed it (e.g. a Python import a symbol actually needs); model
    guesses stay ``advisory``.  ``provenance`` records who asserted it.
    """

    source_id: str
    target_id: str
    kind: str
    enforcement: str = "advisory"
    provenance: str = "analyzer"

    def __post_init__(self) -> None:
        identifier(self.source_id)
        identifier(self.target_id)
        if self.source_id == self.target_id:
            raise InvalidRequest("A relation cannot point a node at itself.")
        if self.kind not in RELATION_KINDS:
            raise InvalidRequest(f"Unsupported relation kind {self.kind!r}.")
        if self.enforcement not in ENFORCEMENT_LEVELS:
            raise InvalidRequest("Relation enforcement must be hard or advisory.")
        identifier(self.provenance)


@dataclass(frozen=True)
class SemanticIndex:
    """An immutable, validated structure/relation index for one source manifest.

    ``uncovered_spans`` records byte ranges no node covers, so a caller cannot
    mistake "the ranker returned nothing" for "there was nothing to return".
    """

    scope_key: str
    source_manifest: tuple[tuple[str, int, str], ...]
    nodes: tuple[SemanticNode, ...]
    relations: tuple[EvidenceRelation, ...]
    uncovered_spans: tuple[tuple[str, int, int, int], ...] = ()
    analyzer_version: str = CHUNKER_VERSION
    config_digest: str = ""

    def __post_init__(self) -> None:
        identifier(self.scope_key)
        ids = [node.id for node in self.nodes]
        if len(set(ids)) != len(ids):
            raise InvalidRequest("Semantic node ids must be unique within an index.")
        known = set(ids)
        for relation in self.relations:
            if relation.source_id not in known or relation.target_id not in known:
                raise InvalidRequest("Relation references a node outside the index.")

    @property
    def digest(self) -> str:
        return digest(canonical({
            "scope": self.scope_key,
            "manifest": [list(item) for item in self.source_manifest],
            "nodes": [{"id": n.id, "ref": _ref_dict(n.ref), "sha": n.source_sha256,
                       "kind": n.kind, "label": n.label, "parent": n.parent_id} for n in self.nodes],
            "relations": [{"s": r.source_id, "t": r.target_id, "k": r.kind,
                           "e": r.enforcement, "p": r.provenance} for r in self.relations],
            "analyzer": self.analyzer_version,
            "config": self.config_digest,
        }).encode("utf-8"))

    def node(self, node_id: str) -> SemanticNode:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise InvalidRequest(f"Unknown semantic node {node_id!r}.")

    def hard_out_edges(self, node_id: str) -> tuple[EvidenceRelation, ...]:
        return tuple(r for r in self.relations if r.source_id == node_id and r.enforcement == "hard")


@dataclass(frozen=True)
class EvidenceGroup:
    """An atomic load unit: a root plus its full hard-dependency closure.

    ``member_refs`` is the deduplicated byte-range union used for budgeting, so
    a shared dependency is never double-charged.  ``required`` groups must load
    whole or fail; optional groups load whole or go entirely cold.
    """

    id: str
    root_ids: tuple[str, ...]
    member_refs: tuple[Ref, ...]
    required: bool = False
    priority: int = 0

    def __post_init__(self) -> None:
        identifier(self.id)
        if not self.root_ids:
            raise InvalidRequest("An evidence group needs at least one root.")
        for root in self.root_ids:
            identifier(root)
        if not self.member_refs or any(not isinstance(ref, Ref) for ref in self.member_refs):
            raise InvalidRequest("Evidence group members must be references.")
        if len(set(self.member_refs)) != len(self.member_refs):
            raise InvalidRequest("Evidence group members must be unique.")
        integer(self.priority)


@dataclass(frozen=True)
class SemanticPlan:
    """A frozen planning result: which groups to load, which refs stay cold.

    ``digest`` is content-addressed over the task/index/query inputs and the
    resulting groups, so the same inputs reproduce the same plan id even though
    re-invoking a model would not.
    """

    task_version: int
    index_digest: str
    query_digest: str
    groups: tuple[EvidenceGroup, ...]
    cold_refs: tuple[Ref, ...] = ()
    policy_version: str = POLICY_VERSION
    diagnostics: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        integer(self.task_version)
        identifier(self.index_digest)
        identifier(self.query_digest)
        ids = [group.id for group in self.groups]
        if len(set(ids)) != len(ids):
            raise InvalidRequest("Plan group ids must be unique.")

    @property
    def digest(self) -> str:
        return digest(canonical({
            "task_version": self.task_version,
            "index": self.index_digest,
            "query": self.query_digest,
            "policy": self.policy_version,
            "groups": [{"id": g.id, "roots": list(g.root_ids),
                        "members": [_ref_dict(r) for r in g.member_refs],
                        "required": g.required, "priority": g.priority} for g in self.groups],
            "cold": [_ref_dict(r) for r in self.cold_refs],
        }).encode("utf-8"))

    @property
    def required_groups(self) -> tuple[EvidenceGroup, ...]:
        return tuple(group for group in self.groups if group.required)

    @property
    def optional_groups(self) -> tuple[EvidenceGroup, ...]:
        return tuple(group for group in self.groups if not group.required)


def _ref_dict(ref: Ref) -> dict:
    return {"name": ref.name, "revision": ref.revision, "start": ref.start, "end": ref.end}


@dataclass(frozen=True)
class SourceDocument:
    """One source artifact revision handed to a semantic analyzer."""

    ref: Ref
    content: bytes
    source_sha256: str
    media_type: str = "text/plain"

    def __post_init__(self) -> None:
        if not isinstance(self.ref, Ref) or not isinstance(self.content, bytes):
            raise InvalidRequest("A source document needs a reference and byte content.")
        if digest(self.content) != self.source_sha256:
            raise InvalidRequest("Source document hash does not match its content.")


@dataclass(frozen=True)
class SemanticIndexDraft:
    """Untrusted analyzer output before core validation binds it to real bytes."""

    nodes: tuple[SemanticNode, ...]
    relations: tuple[EvidenceRelation, ...]
    uncovered_spans: tuple[tuple[str, int, int, int], ...] = ()
    analyzer_version: str = CHUNKER_VERSION
    config_digest: str = ""


@dataclass(frozen=True)
class RankedCandidate:
    node_id: str
    score: float
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class RankingResult:
    ranked: tuple[RankedCandidate, ...]
    unmatched_terms: tuple[str, ...] = ()


class SemanticAnalyzer(Protocol):
    def analyze(self, sources: tuple[SourceDocument, ...], *, config: dict) -> SemanticIndexDraft: ...


class SemanticRanker(Protocol):
    def rank(self, query: str, index: SemanticIndex, texts: dict[str, str], *, config: dict) -> RankingResult: ...


def validate_index(scope_key: str, draft: SemanticIndexDraft, sources: tuple[SourceDocument, ...]) -> SemanticIndex:
    """Bind an untrusted draft to real bytes; discard anything that does not fit.

    Every node must reference a source document present in this bundle, carry
    that document's real hash, and stay inside its byte range.  A node that
    fails any check is dropped, and a relation whose endpoints did not both
    survive is dropped with it — the analyzer proposes, the core disposes.
    """
    identifier(scope_key)
    by_ref = {(doc.ref.name, doc.ref.revision): doc for doc in sources}
    kept: list[SemanticNode] = []
    for node in draft.nodes:
        doc = by_ref.get((node.ref.name, node.ref.revision))
        if doc is None or node.source_sha256 != doc.source_sha256:
            continue
        end = _ref_end(node.ref, len(doc.content))
        if node.ref.start < 0 or end > len(doc.content) or node.ref.start > end:
            continue
        kept.append(node)
    survivors = {node.id for node in kept}
    relations = tuple(r for r in draft.relations if r.source_id in survivors and r.target_id in survivors)
    manifest = tuple(sorted((doc.ref.name, doc.ref.revision, doc.source_sha256) for doc in sources))
    return SemanticIndex(scope_key, manifest, tuple(kept), relations, draft.uncovered_spans,
                         draft.analyzer_version, draft.config_digest)


class StructuralSemanticAnalyzer:
    """Deterministic, local analyzer built on the existing structural chunker.

    It never calls a model.  Structural containment (a Python function inside a
    module's imports) yields a ``hard`` ``requires`` relation, because a
    deterministic parse confirmed the dependency.  Adjacent prose paragraphs get
    an ``advisory`` ``elaboration`` relation only: a lexical guess about
    discourse flow that must never be promoted to a mandatory dependency.
    """

    def __init__(self, chunker: StructuralChunker | None = None):
        self.chunker = chunker or StructuralChunker()

    @property
    def version(self) -> str:
        return f"structural-semantic/{CHUNKER_VERSION}"

    def analyze(self, sources: tuple[SourceDocument, ...], *, config: dict | None = None) -> SemanticIndexDraft:
        config = config or {}
        nodes: list[SemanticNode] = []
        relations: list[EvidenceRelation] = []
        uncovered: list[tuple[str, int, int, int]] = []
        for doc in sources:
            chunks = self.chunker.chunk(doc.ref, doc.content, source_sha256=doc.source_sha256,
                                        media_type=doc.media_type)
            uncovered.extend(uncovered_spans(doc.ref, len(doc.content), chunks))
            ref_to_id: dict[Ref, str] = {}
            for chunk in chunks:
                node_id = self._node_id(doc, chunk)
                ref_to_id[chunk.ref] = node_id
                nodes.append(SemanticNode(node_id, chunk.ref, doc.source_sha256, chunk.kind, chunk.path))
            # Hard structural dependencies: a chunk that declares an import
            # dependency requires that import span.  This is confirmed by the
            # AST walk in the chunker, so it is enforcement="hard".
            for chunk in chunks:
                child = ref_to_id.get(chunk.ref)
                for dependency in chunk.dependencies:
                    parent = ref_to_id.get(dependency)
                    if parent is not None and parent != child:
                        relations.append(EvidenceRelation(child, parent, "requires", "hard", "deterministic_parse"))
            # Advisory prose adjacency: consecutive paragraph/section chunks in
            # the same artifact are *likely* related, but we never assert that
            # as hard.  A host or model may later confirm and upgrade it.
            prose = [chunk for chunk in chunks if chunk.kind in {"paragraph", "markdown_section", "log_event"}]
            for earlier, later in zip(prose, prose[1:]):
                relations.append(EvidenceRelation(ref_to_id[later.ref], ref_to_id[earlier.ref],
                                                  "elaboration", "advisory", "lexical_adjacency"))
        config_digest = digest(canonical({"analyzer": self.version, **config}).encode("utf-8"))
        return SemanticIndexDraft(tuple(nodes), tuple(relations), tuple(uncovered), self.version, config_digest)

    @staticmethod
    def _node_id(doc: SourceDocument, chunk: EvidenceChunk) -> str:
        end = _ref_end(chunk.ref, len(doc.content))
        raw = digest(canonical({"name": chunk.ref.name, "revision": chunk.ref.revision,
                                "start": chunk.ref.start, "end": end, "sha": doc.source_sha256}).encode("utf-8"))
        return f"n_{raw[:24]}"


class LexicalCohesionRanker:
    """Deterministic BM25-style ranker over node text, with rank fusion.

    It scores each node's own bytes plus a small boost for its structural path,
    exactly like the lexical selector, so semantic ranking never silently loses
    the exact path/symbol/version channel.  It returns a rank, not a raw score
    that could be added across incompatible models.
    """

    def rank(self, query: str, index: SemanticIndex, texts: dict[str, str], *, config: dict | None = None) -> RankingResult:
        config = config or {}
        terms = _terms(query)
        if not terms:
            raise InvalidRequest("Semantic ranking query contains no searchable terms.")
        documents = [(node.id, texts.get(node.id, "").casefold(), node.label.casefold()) for node in index.nodes]
        frequency = {term: sum(term in text or term in label for _, text, label in documents) for term in terms}
        average = sum(max(1, len(text)) for _, text, _ in documents) / max(1, len(documents))
        ranked: list[RankedCandidate] = []
        matched_any: set[str] = set()
        for node_id, text, label in documents:
            matched = tuple(term for term in terms if term in text or term in label)
            if not matched:
                continue
            score = 0.0
            for term in matched:
                count = text.count(term)
                tf = count * 2.2 / (count + 1.2 * (0.25 + 0.75 * max(1, len(text)) / average))
                idf = math.log(1.0 + (len(documents) - frequency[term] + 0.5) / (frequency[term] + 0.5))
                score += idf * (tf + (4.0 if term in label else 0.0))
            matched_any.update(matched)
            ranked.append(RankedCandidate(node_id, score, matched))
        ranked.sort(key=lambda item: (-item.score, item.node_id))
        return RankingResult(tuple(ranked), tuple(term for term in terms if term not in matched_any))


class SemanticPlanner:
    """Turn ranked candidates and must-reads into atomic, closed evidence groups.

    The planner expands each root along ``hard`` relations with a visited set
    (so a cycle terminates), unions the byte ranges (so a shared dependency is
    charged once), and marks must-read roots' closures as required groups and
    optional roots' closures as optional atomic groups.  It never fabricates a
    root fragment when a hard dependency is missing from the index.
    """

    def plan(self, *, scope_key: str, task_version: int, index: SemanticIndex, query: str,
             ranking: RankingResult, must_include: tuple[str, ...] = (),
             max_optional_groups: int = 20, min_score: float = 0.0) -> SemanticPlan:
        identifier(scope_key)
        integer(task_version)
        integer(max_optional_groups)
        for node_id in must_include:
            index.node(node_id)  # raises if unknown; must-reads must exist
        query_digest = digest(canonical({"query": query, "terms": _terms(query)}).encode("utf-8"))
        diagnostics: dict = {"unmatched_terms": list(ranking.unmatched_terms),
                             "uncovered_spans": len(index.uncovered_spans),
                             "empty_recall": not ranking.ranked}
        required_roots = tuple(dict.fromkeys(must_include))
        ranked_ids = [c.node_id for c in ranking.ranked if c.score >= min_score and c.node_id not in required_roots]
        groups: list[EvidenceGroup] = []
        covered_refs: set[Ref] = set()
        # Required groups first: each must-read root becomes a required closure.
        for index_pos, root in enumerate(required_roots):
            members, missing = self._closure(index, root)
            if missing:
                diagnostics.setdefault("required_missing_dependencies", []).append({"root": root, "missing": missing})
            groups.append(EvidenceGroup(f"g_req_{index_pos}", (root,), members, required=True, priority=1_000_000))
            covered_refs.update(members)
        truncated = len(ranked_ids) > max_optional_groups
        for index_pos, root in enumerate(ranked_ids[:max_optional_groups]):
            members, missing = self._closure(index, root)
            if missing:
                diagnostics.setdefault("optional_missing_dependencies", []).append({"root": root, "missing": missing})
                # A hard dependency the index cannot cover must not surface as a
                # partial root; drop the whole optional group and record it.
                continue
            score = next((c.score for c in ranking.ranked if c.node_id == root), 0.0)
            groups.append(EvidenceGroup(f"g_opt_{index_pos}", (root,), members, required=False,
                                        priority=max(1, round(score * 1_000))))
            covered_refs.update(members)
        diagnostics["candidate_truncated"] = truncated
        cold_refs = tuple(sorted((node.ref for node in index.nodes if node.ref not in covered_refs),
                                 key=lambda r: (r.name, r.revision, r.start)))
        return SemanticPlan(task_version, index.digest, query_digest, tuple(groups), cold_refs,
                            POLICY_VERSION, diagnostics)

    @staticmethod
    def _closure(index: SemanticIndex, root: str) -> tuple[tuple[Ref, ...], list[str]]:
        """Transitive hard-dependency closure with cycle-safe traversal.

        Returns the deduplicated byte-range union and the list of hard targets
        that the index does not contain (so the caller can refuse to emit a
        deceptively complete root).
        """
        visited: set[str] = set()
        order: list[str] = []
        missing: list[str] = []
        stack = [root]
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            order.append(current)
            for relation in index.hard_out_edges(current):
                try:
                    index.node(relation.target_id)
                except InvalidRequest:
                    missing.append(relation.target_id)
                    continue
                if relation.target_id not in visited:
                    stack.append(relation.target_id)
        refs = tuple(dict.fromkeys(index.node(node_id).ref for node_id in order))
        return refs, missing


def build_index(scope_key: str, sources: tuple[SourceDocument, ...], *,
                analyzer: SemanticAnalyzer | None = None, config: dict | None = None) -> SemanticIndex:
    """Analyze sources and validate the draft into a bound, immutable index."""
    analyzer = analyzer or StructuralSemanticAnalyzer()
    draft = analyzer.analyze(sources, config=config or {})
    return validate_index(scope_key, draft, sources)


def node_texts(index: SemanticIndex, sources: tuple[SourceDocument, ...]) -> dict[str, str]:
    """Decode each node's exact bytes for ranking; skip undecodable ranges."""
    by_ref = {(doc.ref.name, doc.ref.revision): doc for doc in sources}
    texts: dict[str, str] = {}
    for node in index.nodes:
        doc = by_ref.get((node.ref.name, node.ref.revision))
        if doc is None:
            continue
        end = _ref_end(node.ref, len(doc.content))
        texts[node.id] = doc.content[node.ref.start:end].decode("utf-8", errors="replace")
    return texts
