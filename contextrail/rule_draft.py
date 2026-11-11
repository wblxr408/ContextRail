"""Prepare a small, source-bound project rule patch for explicit review."""

from difflib import unified_diff
from pathlib import Path, PurePosixPath
import re
from hashlib import sha256

from .directory_sync import _read_file
from .errors import BudgetExceeded, Conflict, InvalidRequest
from .experience import experience_for_rule
from .models import canonical
from .retrieval import _terms
from .sanitization import CaptureError, clean_text


POLICY_FILES = {"AGENTS.md", "CLAUDE.md", "SKILL.md"}
MAX_POLICY_BYTES = 65536
HEADING = "## Reviewed project practices"
NEGATIVE = re.compile(r"\b(?:never|forbid(?:den)?|must not|do not|don't|only)\b|禁止|不得|不要|仅限|只能", re.I)
FENCE = re.compile(r"^\s{0,3}(\x60{3,}|~{3,})")


def _target(workspace, supplied):
    path = PurePosixPath(supplied)
    if (path.is_absolute() or "\\" in supplied or ":" in supplied or ".." in path.parts
            or not path.parts or path.name not in POLICY_FILES or len(supplied) > 512
            or any(ord(char) < 32 or ord(char) == 127 for char in supplied)):
        raise InvalidRequest("Target an existing project AGENTS.md, CLAUDE.md or SKILL.md by relative path.")
    return workspace.joinpath(*path.parts)


def _policies(workspace, target):
    paths = [target]
    for parent in (target.parent, *target.parent.parents):
        if not parent.is_relative_to(workspace):
            break
        paths.extend(parent / name for name in ("AGENTS.md", "CLAUDE.md"))
    if len(set(paths)) > 24:
        raise InvalidRequest("Rule draft target exceeds the bounded project instruction depth.")
    for path in dict.fromkeys(paths):
        try:
            data, _ = _read_file(path, workspace, MAX_POLICY_BYTES)
            text = data.decode("utf-8")
        except FileNotFoundError:
            if path == target:
                raise InvalidRequest("Create or select the intended project rule file before drafting.") from None
            continue
        except (OSError, UnicodeError, InvalidRequest):
            raise InvalidRequest("A relevant project rule file could not be read safely.") from None
        yield path.relative_to(workspace).as_posix(), text


def _policy_lines(text):
    fence = None
    comment = False
    for number, line in enumerate(text.splitlines(), 1):
        marker = FENCE.match(line)
        if fence is not None:
            if marker and marker[1].startswith(fence) and not line[marker.end():].strip():
                fence = None
            continue
        if comment or "<!--" in line:
            comment = "-->" not in line
            continue
        if marker:
            fence = marker[1]
            continue
        yield number, line
    if fence is not None or comment:
        raise InvalidRequest("Close the unfinished Markdown block before drafting a rule.")


def _comparable(text):
    return text.lstrip().removeprefix("\ufeff").lstrip("-* ").strip().casefold()


def _conflicts(policies, target, rule):
    terms = set(_terms(rule))
    negative = bool(NEGATIVE.search(rule))
    matches = []
    for path, body in policies:
        for number, line in _policy_lines(body):
            comparable = _comparable(line)
            if not comparable or comparable.startswith("#"):
                continue
            if comparable == _comparable(rule):
                kind = "duplicate"
            else:
                existing = set(_terms(line))
                shared = len(terms & existing)
                if shared < 2 or shared / max(1, min(len(terms), len(existing))) < 0.5:
                    continue
                kind = "possible_conflict" if negative != bool(NEGATIVE.search(line)) else "related"
            matches.append({"path": path, "line": number, "kind": kind, "text": clean_text(line, 400)})
    priority = {"possible_conflict": 0, "duplicate": 1, "related": 2}
    matches.sort(key=lambda item: (priority[item["kind"]], item["path"] != target, item["path"], item["line"]))
    return matches


def _append_rule(text, rule):
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    heading = next((number for number, line in _policy_lines(text) if line.strip() == HEADING), None)
    bullet = f"- {rule}{newline}"
    if heading is not None:
        if not lines[heading - 1].endswith(("\n", "\r")):
            lines[heading - 1] += newline
        while heading < len(lines) and not lines[heading].strip():
            heading += 1
        lines.insert(heading, bullet)
        return "".join(lines)
    separator = "" if not text else newline if text.endswith(("\n", "\r")) else newline * 2
    return text + separator + HEADING + newline + newline + bullet


def _patch(relative, before, after):
    if before == after:
        return ""
    lines = []
    for line in unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                             fromfile="a/" + relative + "\t", tofile="b/" + relative + "\t", n=3):
        lines.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(lines)


def rule_draft(binding, candidate_id, evidence_version, target, rule, *, since=None, until=None,
               reconsider=False, budget=65536):
    if (not isinstance(rule, str) or not rule.strip() or len(rule.encode("utf-8")) > 512
            or any(ord(char) < 32 or ord(char) == 127 for char in rule)
            or rule.lstrip().startswith(("#", "-", "*", "\x60", "~~~", "<!--"))):
        raise InvalidRequest("Provide one short rule sentence without Markdown block markers.")
    rule = rule.strip()
    try:
        if clean_text(rule) != rule:
            raise InvalidRequest("Remove secrets and invisible control characters from the proposed rule.")
    except CaptureError:
        raise InvalidRequest("Invalid proposed rule text.") from None
    candidate = experience_for_rule(binding, candidate_id, evidence_version, since=since, until=until,
                                    reconsider=reconsider)
    workspace = Path(binding.workspace)
    path = _target(workspace, target)
    relative = path.relative_to(workspace).as_posix()
    policies = list(_policies(workspace, path))
    before = policies[0][1]
    conflicts = _conflicts(policies, relative, rule)
    duplicates = any(item["kind"] == "duplicate" for item in conflicts)
    after = before if duplicates else _append_rule(before, rule)
    diff, reverse = _patch(relative, before, after), _patch(relative, after, before)
    # Existing instructions may include secrets. A human can review a location
    # without exporting the surrounding unprocessed policy lines.
    try:
        if any(clean_text(line) != line for patch in (diff, reverse) for line in patch.splitlines()):
            raise Conflict("The patch context contains restricted text. Review the proposed rule locally.")
    except CaptureError:
        raise Conflict("The patch context could not be exported safely.") from None
    result = {
        "schema": "contextrail.experience-rule-draft/v1", "workspace": binding.workspace, "target": relative,
        "status": "already_present" if duplicates else "review_required", "rule": rule,
        "baseline": sha256(before.encode("utf-8")).hexdigest(), "source": candidate,
        "conflicts": conflicts[:12], "conflicts_omitted": max(0, len(conflicts) - 12),
        "conflict_basis": "local_instruction_line_overlap_for_human_review",
        "files_reviewed": [path for path, _ in policies], "diff": diff, "reverse_diff": reverse,
        "apply": "review_and_apply_diff_to_the_same_baseline",
        "undo": "apply_reverse_diff_if_the_adopted_lines_are_unchanged",
    }
    if len(canonical(result).encode("utf-8")) > budget:
        raise BudgetExceeded("Rule draft and source evidence exceed the response budget.")
    return result
