"""Read-only byte-accurate windows around existing lexical candidate occurrences."""

from dataclasses import asdict

from .errors import InvalidRequest
from .models import Ref, integer


MAX_WINDOW_BYTES = 2048
MAX_ANCHORS = 32


def validate_options(context_chars=80, window_limit=3):
    integer(context_chars)
    integer(window_limit, 1)
    if context_chars > 256 or window_limit > 8:
        raise InvalidRequest("Hit context is limited to 256 characters per side and 1-8 windows.")


def _occurrences(text, terms):
    from .retrieval import _TERM
    wanted = set(terms)
    found = set()
    for match in _TERM.finditer(text):
        word = match.group(0).casefold()
        if len(word.encode("utf-8")) <= 512 and word in wanted:
            found.add((match.start(), match.end(), word))
        if "\u4e00" <= word[0] <= "\u9fff":
            for index in range(len(word) - 1):
                term = word[index:index + 2]
                if term in wanted:
                    found.add((match.start() + index, match.start() + index + 2, term))
    return sorted(found)


def _line(content, position):
    # CRLF is one separator whose two bytes both belong to the preceding line;
    # the +1 lookahead prevents counting its CR before the LF was crossed.
    return 1 + content.count(b"\n", 0, position) + content.count(b"\r", 0, position) - content.count(b"\r\n", 0, min(len(content), position + 1))


def _bounds(start, end, offsets, context):
    begin, stop = max(0, start - context), min(len(offsets) - 1, end + context)
    if offsets[end] - offsets[start] > MAX_WINDOW_BYTES:
        raise InvalidRequest("An indexed occurrence exceeds the complete window byte limit; read its source span.")
    while offsets[stop] - offsets[begin] > MAX_WINDOW_BYTES:
        if offsets[start] - offsets[begin] >= offsets[stop] - offsets[end] and begin < start:
            begin += 1
        elif stop > end:
            stop -= 1
        else:
            begin += 1
    return begin, stop


def project_windows(content, ref, path, terms, *, context_chars=80, window_limit=3):
    """Project one verified full artifact; all returned offsets stay absolute."""
    validate_options(context_chars, window_limit)
    end = len(content) if ref.end is None else ref.end
    raw = content[ref.start:end]
    text = raw.decode("utf-8")
    offsets = [0]
    for character in text:
        offsets.append(offsets[-1] + len(character.encode("utf-8")))
    occurrences = _occurrences(text, terms)
    ranges = []
    for start, stop, term in occurrences:
        begin, finish = _bounds(start, stop, offsets, context_chars)
        if ranges and begin <= ranges[-1][1] and offsets[max(finish, ranges[-1][1])] - offsets[ranges[-1][0]] <= MAX_WINDOW_BYTES:
            ranges[-1] = (ranges[-1][0], max(finish, ranges[-1][1]))
        else:
            ranges.append((begin, finish))
    if not ranges:
        ranges = [(0, min(len(text), max(1, context_chars * 2)))]
    base_line = _line(content, ref.start)
    def line_at(byte):
        return base_line + _line(raw, byte) - 1
    windows, shown = [], set()
    for begin, finish in ranges[:window_limit]:
        byte_start, byte_end = offsets[begin], offsets[finish]
        contained = [(start, stop, term) for start, stop, term in occurrences if begin <= start and stop <= finish]
        anchors = []
        for start, stop, term in contained[:MAX_ANCHORS]:
            anchor = Ref(ref.name, ref.revision, ref.start + offsets[start], ref.start + offsets[stop])
            anchors.append({"term": term, "ref": asdict(anchor), "line": line_at(offsets[start])})
            shown.add((start, stop, term))
        window_ref = Ref(ref.name, ref.revision, ref.start + byte_start, ref.start + byte_end)
        windows.append({"ref": asdict(window_ref), "text": text[begin:finish], "line_start": line_at(byte_start),
                        "line_end": line_at(max(byte_start, byte_end - 1)), "matches": anchors,
                        "matches_total": len(contained), "matches_omitted": max(0, len(contained) - len(anchors))})
    if path.startswith(("paragraph:", "log_event:")) or path == "<raw_gap>":
        position = occurrences[0][0] if occurrences else 0
        start = max(text.rfind("\r", 0, position), text.rfind("\n", 0, position)) + 1
        stops = [value for value in (text.find("\r", position), text.find("\n", position)) if value >= 0]
        title = text[start:min(stops) if stops else len(text)]
    else:
        title = path
    first = windows[0]
    return {"title": title[:120], "title_truncated": len(title) > 120,
            "match_origin": "content" if occurrences else "source_label",
            "windows": windows, "windows_total": len(ranges), "windows_omitted": max(0, len(ranges) - len(windows)),
            "matches_total": len(occurrences), "matches_omitted": len(occurrences) - len(shown),
            "snippet": first["text"], "snippet_ref": first["ref"],
            "snippet_truncated": first["ref"]["start"] != ref.start or first["ref"]["end"] != end,
            "window_policy": {"context_chars": context_chars, "window_limit": window_limit,
                              "max_window_bytes": MAX_WINDOW_BYTES, "max_anchors_per_window": MAX_ANCHORS}}
