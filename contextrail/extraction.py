"""Deterministic read-only projections of saved evidence, with exact source spans."""

from dataclasses import asdict, dataclass
from array import array
import csv
from decimal import Decimal, InvalidOperation, localcontext
import io
import json
import math
import re

from .errors import BudgetExceeded, InvalidRequest
from .models import Ref, canonical, integer, unicode_text


KINDS = ("log", "json", "csv", "git_status", "git_numstat")
MAX_INPUT = 16 * 1024 * 1024
MAX_RECORDS = 50000


class ParseFailure(ValueError):
    pass


class ExtractionLimit(ValueError):
    pass


def _pointer(path):
    if not isinstance(path, str) or (path and not path.startswith("/")):
        raise InvalidRequest("JSON paths must be RFC6901 pointers, empty or beginning with '/'.")
    unicode_text(path)
    if len(path) > 4096 or re.search(r"~(?![01])", path):
        raise InvalidRequest("Invalid or oversized JSON Pointer escape.")
    return [part.replace("~1", "/").replace("~0", "~") for part in path.split("/")[1:]] if path else []


def _options(kind, options):
    allowed = {"log": set(), "json": {"path", "where", "select"}, "csv": {"numeric", "delimiter"},
               "git_status": set(), "git_numstat": set()}
    if kind not in KINDS or not isinstance(options, dict) or options.keys() - allowed[kind]:
        raise InvalidRequest("Unsupported extraction kind or options.")
    if kind == "json":
        _pointer(options.get("path", ""))
        _pointer(options.get("select", ""))
        if "where" in options:
            where = options["where"]
            if not isinstance(where, dict) or set(where) != {"path", "equals"}:
                raise InvalidRequest("JSON filter requires only path and equals.")
            _pointer(where["path"])
            value = where["equals"]
            if type(value) not in (str, int, float, bool, type(None)) or type(value) is float and not math.isfinite(value):
                raise InvalidRequest("JSON equals must be a finite scalar.")
            if isinstance(value, str):
                unicode_text(value)
    if kind == "csv":
        numeric = options.get("numeric", [])
        if (not isinstance(numeric, list) or len(numeric) > 64 or any(not isinstance(name, str) or not name for name in numeric)
                or len(set(numeric)) != len(numeric)):
            raise InvalidRequest("CSV numeric columns must be distinct names (up to 64).")
        delimiter = options.get("delimiter", ",")
        if not isinstance(delimiter, str) or len(delimiter) != 1 or delimiter in '\r\n\x00"':
            raise InvalidRequest("CSV delimiter must be one non-newline, non-quote character.")
        for name in [*numeric, delimiter]:
            unicode_text(name)


@dataclass
class JsonNode:
    value: object
    start: int
    end: int
    children: dict


class JsonSource:
    """Use the stdlib scalar decoder while retaining container/value boundaries."""
    def __init__(self, text):
        self.text, self.nodes = text, 0
        self.decoder = json.JSONDecoder(parse_constant=self.invalid_constant)

    @staticmethod
    def invalid_constant(value):
        raise ParseFailure("Non-finite JSON number.")

    def whitespace(self, pos):
        while pos < len(self.text) and self.text[pos] in " \t\r\n":
            pos += 1
        return pos

    def parse(self, pos=0, depth=0):
        self.nodes += 1
        if depth > 128 or self.nodes > MAX_RECORDS:
            raise ExtractionLimit("JSON depth or node limit exceeded.")
        start = pos = self.whitespace(pos)
        if pos >= len(self.text):
            raise ParseFailure("Incomplete JSON value.")
        token = self.text[pos]
        if token not in "[{":
            value, end = self.decoder.raw_decode(self.text, pos)
            if isinstance(value, str):
                unicode_text(value)
            if isinstance(value, float) and not math.isfinite(value):
                raise ParseFailure("Non-finite JSON number.")
            return JsonNode(value, start, end, {}), end
        is_object = token == "{"
        close = "}" if is_object else "]"
        children, values = {}, {} if is_object else []
        pos = self.whitespace(pos + 1)
        if pos < len(self.text) and self.text[pos] == close:
            return JsonNode(values, start, pos + 1, children), pos + 1
        while True:
            if is_object:
                key, pos = self.decoder.raw_decode(self.text, pos)
                if not isinstance(key, str) or key in children:
                    raise ParseFailure("JSON object keys must be unique strings.")
                unicode_text(key)
                pos = self.whitespace(pos)
                if pos >= len(self.text) or self.text[pos] != ":":
                    raise ParseFailure("Missing JSON object colon.")
                pos += 1
            else:
                key = str(len(values))
            child, pos = self.parse(pos, depth + 1)
            children[key] = child
            if is_object:
                values[key] = child.value
            else:
                values.append(child.value)
            pos = self.whitespace(pos)
            if pos >= len(self.text):
                raise ParseFailure("Unterminated JSON container.")
            if self.text[pos] == close:
                return JsonNode(values, start, pos + 1, children), pos + 1
            if self.text[pos] != ",":
                raise ParseFailure("Missing JSON container comma.")
            pos = self.whitespace(pos + 1)


def _resolve(node, tokens):
    for token in tokens:
        if isinstance(node.value, list) and (not token.isascii() or not token.isdecimal() or str(int(token)) != token):
            return None
        node = node.children.get(token)
        if node is None:
            return None
    return node


def _number(value):
    text = format(value, "f")
    return (text.rstrip("0").rstrip(".") if "." in text else text) if value else "0"


class Source:
    def __init__(self, text, ref):
        self.text, self.ref = text, ref
        self.bytes = array("I", [0])
        for character in text:
            self.bytes.append(self.bytes[-1] + len(character.encode("utf-8")))

    def span(self, start, end, **locator):
        return {"ref": asdict(Ref(self.ref.name, self.ref.revision, self.bytes[start], self.bytes[end])), **locator}


def _json(source, options):
    parser = JsonSource(source.text)
    root, end = parser.parse()
    if parser.whitespace(end) != len(source.text):
        raise ParseFailure("Unexpected data after JSON value.")
    path = options.get("path", "")
    node = _resolve(root, _pointer(path))
    if node is None:
        return [], 0, {}
    candidates = list(node.children.items()) if isinstance(node.value, list) else [(None, node)]
    items = []
    for index, candidate in candidates:
        where = options.get("where")
        if where:
            field = _resolve(candidate, _pointer(where["path"]))
            if field is None or type(field.value) is not type(where["equals"]) or field.value != where["equals"]:
                continue
        selected = _resolve(candidate, _pointer(options.get("select", "")))
        if selected is None:
            continue
        pointer = path + ("/" + index if index is not None else "") + options.get("select", "")
        items.append({"value": selected.value, "source": source.span(selected.start, selected.end, pointer=pointer)})
    return items, len(candidates), {}


def _log(source):
    groups, pos, records, count = {}, 0, 0, 0
    marker = re.compile(r"\b(?:ERROR|FATAL|CRITICAL|Traceback|[A-Za-z_]*Exception)\b", re.IGNORECASE)
    for line in io.StringIO(source.text, newline=""):
        records += 1
        if records > MAX_RECORDS:
            raise ExtractionLimit("Log record limit exceeded.")
        message = line.rstrip("\r\n")
        if marker.search(message):
            count += 1
            if message not in groups:
                groups[message] = {"message": message, "count": 0, "source": source.span(pos, pos + len(line), line_start=records, line_end=records)}
            groups[message]["count"] += 1
        pos += len(line)
    return list(groups.values()), records, {"error_lines": count, "groups": len(groups)}


def _csv(source, options):
    lines = io.StringIO(source.text, newline="").readlines()
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    reader = csv.reader(io.StringIO(source.text, newline=""), delimiter=options.get("delimiter", ","), strict=True)
    try:
        headers = next(reader)
    except StopIteration:
        return [], 0, {"numeric": {}}
    if not headers or any(not header for header in headers) or len(set(headers)) != len(headers):
        raise ParseFailure("CSV needs distinct nonempty header names.")
    numeric = options.get("numeric", [])
    if any(name not in headers for name in numeric):
        raise ParseFailure("Requested numeric column is absent from CSV header.")
    stats = {name: {"valid": 0, "invalid": 0, "empty": 0, "sum": Decimal(0), "min": None, "max": None} for name in numeric}
    items, prior = [], reader.line_num
    with localcontext() as arithmetic:
        # Up to 2000 integer places and 1000 fractional places, plus carry
        # from at most 50000 rows. Keep the sum exact across that full range.
        arithmetic.prec = 3100
        for values in reader:
            if len(items) >= MAX_RECORDS:
                raise ExtractionLimit("CSV record limit exceeded.")
            if len(values) != len(headers):
                raise ParseFailure("CSV record width differs from header.")
            record = dict(zip(headers, values))
            for name, stat in stats.items():
                text = record[name].strip()
                if not text:
                    stat["empty"] += 1
                    continue
                try:
                    number = Decimal(text)
                    if not number.is_finite() or len(number.as_tuple().digits) > 1000 or abs(number.as_tuple().exponent) > 1000:
                        raise InvalidOperation()
                except InvalidOperation:
                    stat["invalid"] += 1
                    continue
                stat["valid"] += 1
                stat["sum"] += number
                stat["min"] = number if stat["min"] is None else min(stat["min"], number)
                stat["max"] = number if stat["max"] is None else max(stat["max"], number)
            items.append({"record": record, "source": source.span(offsets[prior], offsets[reader.line_num],
                                                                  record=len(items) + 1, line_start=prior + 1, line_end=reader.line_num)})
            prior = reader.line_num
        for stat in stats.values():
            with localcontext() as average:
                average.prec = 34
                stat["mean"] = _number(stat["sum"] / stat["valid"]) if stat["valid"] else None
            for field in ("sum", "min", "max"):
                stat[field] = _number(stat[field]) if stat[field] is not None else None
    return items, len(items), {"numeric": stats, "mean_precision": "34 significant decimal digits, ROUND_HALF_EVEN"}


def _git(source, kind):
    text, items, pos = source.text, [], 0
    stats = {"added": 0, "deleted": 0, "binary_files": 0} if kind == "git_numstat" else {}
    def field(start):
        end = text.find("\x00", start)
        if end < 0:
            raise ParseFailure("Git -z record is missing a NUL terminator.")
        return text[start:end], end + 1
    while pos < len(text):
        if len(items) >= MAX_RECORDS:
            raise ExtractionLimit("Git record limit exceeded.")
        start = pos
        head, pos = field(pos)
        if kind == "git_status":
            if (len(head) < 4 or head[2] != " " or any(code not in " MADRCUT?!" for code in head[:2])
                    or any(code in head[:2] for code in "?!") and head[:2] not in ("??", "!!")):
                raise ParseFailure("Expected git status --porcelain=v1 -z record.")
            item = {"index": head[0], "worktree": head[1], "path": head[3:]}
            if "R" in head[:2] or "C" in head[:2]:
                item["original_path"], pos = field(pos)
        else:
            columns = head.split("\t", 2)
            if len(columns) != 3:
                raise ParseFailure("Expected git diff --numstat -z record.")
            added, deleted, path = columns
            if added == deleted == "-":
                item = {"added": None, "deleted": None, "path": path, "binary": True}
                stats["binary_files"] += 1
            elif added.isascii() and added.isdecimal() and deleted.isascii() and deleted.isdecimal():
                item = {"added": int(added), "deleted": int(deleted), "path": path, "binary": False}
                stats["added"] += item["added"]
                stats["deleted"] += item["deleted"]
            else:
                raise ParseFailure("Invalid Git numstat counts.")
            if not path:
                item["original_path"], pos = field(pos)
                item["path"], pos = field(pos)
        if not item["path"] or "original_path" in item and not item["original_path"]:
            raise ParseFailure("Git record has an empty path.")
        item["source"] = source.span(start, pos, record=len(items) + 1)
        items.append(item)
    return items, len(items), stats


def extract_evidence(store, scope, session, source, kind, *, options=None, limit=20, budget=8192):
    store.session(scope, session)
    options = {} if options is None else options
    _options(kind, options)
    integer(limit, 1)
    integer(budget, 1)
    if limit > 100:
        raise InvalidRequest("Extraction display limit cannot exceed 100.")
    if not isinstance(source, dict) or set(source) - {"name", "revision", "start", "end"}:
        raise InvalidRequest("Source must be an exact evidence reference.")
    if not {"name", "revision"} <= source.keys():
        raise InvalidRequest("Source requires name and revision.")
    ref = Ref(**source)
    if ref.start != 0 or ref.end is not None:
        raise InvalidRequest("Extraction reads a complete explicit revision, not an arbitrary slice.")
    artifact = store.get(scope, ref)
    result = {"schema": "contextrail.extraction.v1", "kind": kind, "source": asdict(ref),
              "trust": "derived_extraction_with_source_spans", "range_unit": "bytes", "filter": options,
              "sampling": "none; complete source scan", "scan": {"bytes": len(artifact.content), "records": 0, "complete": True},
              "status": "succeeded", "statistics": {}, "items": [], "matched": 0, "omitted": 0}
    try:
        if len(artifact.content) > MAX_INPUT:
            raise ExtractionLimit("Evidence exceeds the 16 MiB extraction limit.")
        text = artifact.content.decode("utf-8")
        unicode_text(text)
        indexed = Source(text, ref)
        if kind == "json":
            items, records, statistics = _json(indexed, options)
        elif kind == "csv":
            items, records, statistics = _csv(indexed, options)
        elif kind == "log":
            items, records, statistics = _log(indexed)
        else:
            items, records, statistics = _git(indexed, kind)
        result.update(items=items[:limit], statistics=statistics, matched=len(items), status="succeeded" if items else "no_match")
        result["scan"]["records"] = records
    except (ExtractionLimit, ParseFailure, csv.Error, ValueError, UnicodeError, RecursionError, InvalidRequest) as exc:
        result.update(status="incomplete" if isinstance(exc, ExtractionLimit) else "parse_failed",
                      error={"code": "resource_limit" if isinstance(exc, ExtractionLimit) else "invalid_format", "message": str(exc)[:200]},
                      sampling="not_completed; original source preserved")
        result["scan"]["complete"] = False
    while True:
        result["returned"] = len(result["items"])
        result["omitted"] = result["matched"] - result["returned"]
        if len(canonical(result).encode("utf-8")) <= budget:
            return result
        if not result["items"]:
            raise BudgetExceeded("Extraction metadata cannot fit the complete JSON byte budget.")
        result["items"].pop()
