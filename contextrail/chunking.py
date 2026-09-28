"""Structure-aware, byte-accurate evidence chunking for deterministic retrieval.

Chunks are references into immutable artifact revisions; this module never
copies, rewrites, or summarizes source evidence.  Hosts can therefore compile
selected chunks with the existing ``Selection`` and recover the exact original
bytes through ``context.get``.
"""

from __future__ import annotations

from dataclasses import dataclass
import ast
import re

from .errors import InvalidRequest
from .models import Ref, Selection, integer, unicode_text


CHUNKER_VERSION = "structural-v1"
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$")


@dataclass(frozen=True)
class EvidenceChunk:
    """A structured, source-verifiable section of one artifact revision."""

    ref: Ref
    source_sha256: str
    kind: str
    path: str
    dependencies: tuple[Ref, ...] = ()
    chunker_version: str = CHUNKER_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.ref, Ref) or not isinstance(self.dependencies, tuple) or any(
                not isinstance(ref, Ref) for ref in self.dependencies):
            raise InvalidRequest("Chunk references must be evidence references.")
        if not isinstance(self.source_sha256, str) or len(self.source_sha256) != 64:
            raise InvalidRequest("Chunk source hash must be a SHA-256 digest.")
        try:
            int(self.source_sha256, 16)
        except ValueError:
            raise InvalidRequest("Chunk source hash must be a SHA-256 digest.") from None
        if not self.kind or not self.path or not self.chunker_version:
            raise InvalidRequest("Chunk metadata must be nonempty.")


class StructuralChunker:
    """Produce semantic spans without cutting through UTF-8 characters.

    Python files split into imports and top-level classes/functions; Markdown
    splits at headings; other text splits at blank-line event/paragraph
    boundaries.  A structure that cannot be parsed is kept as a single exact
    fallback chunk rather than being cut at an arbitrary character count.
    """

    def chunk(self, ref: Ref, content: bytes, *, source_sha256: str, media_type: str = "text/plain") -> tuple[EvidenceChunk, ...]:
        if not isinstance(ref, Ref) or not isinstance(content, bytes):
            raise InvalidRequest("Chunking requires a reference and byte content.")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return ()
        unicode_text(text)
        if not text:
            return ()
        lower_name = ref.name.casefold()
        if lower_name.endswith(".py") or media_type in {"text/x-python", "application/x-python"}:
            chunks = self._python(ref, text, source_sha256)
            if chunks:
                return chunks
        if lower_name.endswith((".md", ".markdown", ".mdx")) or media_type in {"text/markdown", "text/x-markdown"}:
            chunks = self._markdown(ref, text, source_sha256)
            if chunks:
                return chunks
        return self._paragraphs(ref, text, source_sha256, "log_event" if "log" in lower_name else "paragraph")

    @staticmethod
    def merge(chunks: tuple[EvidenceChunk, ...]) -> tuple[EvidenceChunk, ...]:
        """Coalesce overlapping or adjacent selections from the same revision."""
        if any(not isinstance(chunk, EvidenceChunk) for chunk in chunks):
            raise InvalidRequest("Expected evidence chunks.")
        ordered = sorted(chunks, key=lambda item: (item.ref.name, item.ref.revision, item.ref.start,
                                                    item.ref.end if item.ref.end is not None else 2**63 - 1))
        merged: list[EvidenceChunk] = []
        for chunk in ordered:
            if not merged:
                merged.append(chunk)
                continue
            prior = merged[-1]
            prior_end = prior.ref.end if prior.ref.end is not None else 2**63 - 1
            end = chunk.ref.end if chunk.ref.end is not None else 2**63 - 1
            if (prior.ref.name, prior.ref.revision, prior.source_sha256) == (chunk.ref.name, chunk.ref.revision, chunk.source_sha256) and chunk.ref.start <= prior_end:
                merged[-1] = EvidenceChunk(Ref(prior.ref.name, prior.ref.revision, prior.ref.start,
                                                None if prior_end == 2**63 - 1 or end == 2**63 - 1 else max(prior_end, end)),
                                           prior.source_sha256, "merged", prior.path,
                                           tuple(dict.fromkeys(prior.dependencies + chunk.dependencies)))
            else:
                merged.append(chunk)
        return tuple(merged)

    def _python(self, ref: Ref, text: str, sha: str) -> tuple[EvidenceChunk, ...]:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return ()
        offsets = self._line_offsets(text)
        chunks: list[EvidenceChunk] = []
        import_end = 0
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                import_end = max(import_end, offsets[node.end_lineno - 1] + len(text.splitlines(keepends=True)[node.end_lineno - 1].encode("utf-8")))
        imports = Ref(ref.name, ref.revision, 0, import_end) if import_end else None
        if imports is not None:
            chunks.append(EvidenceChunk(imports, sha, "imports", "<module.imports>"))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            start = offsets[node.lineno - 1]
            end = offsets[node.end_lineno - 1] + len(text.splitlines(keepends=True)[node.end_lineno - 1].encode("utf-8"))
            deps = () if imports is None or start == 0 else (imports,)
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            chunks.append(EvidenceChunk(Ref(ref.name, ref.revision, start, end), sha, kind, node.name, deps))
        return tuple(chunks)

    def _markdown(self, ref: Ref, text: str, sha: str) -> tuple[EvidenceChunk, ...]:
        lines = text.splitlines(keepends=True)
        starts = [index for index, line in enumerate(lines) if _HEADING.match(line)]
        if not starts:
            return ()
        offsets = self._line_offsets(text)
        chunks = []
        for index, start_line in enumerate(starts):
            end_line = starts[index + 1] if index + 1 < len(starts) else len(lines)
            title = _HEADING.match(lines[start_line]).group(1)  # type: ignore[union-attr]
            start = offsets[start_line]
            end = offsets[end_line] if end_line < len(lines) else len(text.encode("utf-8"))
            chunks.append(EvidenceChunk(Ref(ref.name, ref.revision, start, end), sha, "markdown_section", title))
        return tuple(chunks)

    def _paragraphs(self, ref: Ref, text: str, sha: str, kind: str) -> tuple[EvidenceChunk, ...]:
        lines = text.splitlines(keepends=True)
        offsets = self._line_offsets(text)
        chunks: list[EvidenceChunk] = []
        start_line: int | None = None
        for index, line in enumerate(lines + ["\n"]):
            if line.strip():
                start_line = index if start_line is None else start_line
                continue
            if start_line is not None:
                end_line = index
                start = offsets[start_line]
                end = offsets[end_line] if end_line < len(lines) else len(text.encode("utf-8"))
                chunks.append(EvidenceChunk(Ref(ref.name, ref.revision, start, end), sha, kind,
                                            f"{kind}:{len(chunks) + 1}"))
                start_line = None
        return tuple(chunks)

    @staticmethod
    def _line_offsets(text: str) -> list[int]:
        offsets: list[int] = []
        total = 0
        for line in text.splitlines(keepends=True):
            offsets.append(total)
            total += len(line.encode("utf-8"))
        return offsets


def selections_for_chunks(chunks: tuple[EvidenceChunk, ...], *, required: bool = False, priority: int = 0) -> tuple[Selection, ...]:
    """Convert chunks to compiler-ready selections, including direct imports."""
    integer(priority)
    refs = []
    for chunk in chunks:
        refs.extend((chunk.ref, *chunk.dependencies))
    unique = tuple(dict.fromkeys(refs))
    return tuple(Selection(ref, required=required, priority=priority) for ref in unique)


def uncovered_spans(ref: Ref, size: int, chunks: tuple[EvidenceChunk, ...]) -> tuple[tuple[str, int, int, int], ...]:
    """Report byte ranges of ``ref`` that no chunk covers.

    Semantic ranking can only recall candidates that exist; a leading module
    docstring, a decorator, or heading preamble that the structural chunker did
    not emit would otherwise be invisible.  Recording the gap as
    ``(name, revision, start, end)`` lets the index expose a raw fallback entry
    instead of silently dropping the range.  Only same-revision chunks of this
    artifact are considered.
    """
    intervals = sorted((chunk.ref.start, chunk.ref.end if chunk.ref.end is not None else size)
                       for chunk in chunks
                       if chunk.ref.name == ref.name and chunk.ref.revision == ref.revision)
    gaps: list[tuple[str, int, int, int]] = []
    cursor = 0
    for start, end in intervals:
        if start > cursor:
            gaps.append((ref.name, ref.revision, cursor, start))
        cursor = max(cursor, end)
    if cursor < size:
        gaps.append((ref.name, ref.revision, cursor, size))
    return tuple(gaps)
