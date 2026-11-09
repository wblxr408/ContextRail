"""Runner-only task cards and a machine acceptance command for synthetic tasks."""

from dataclasses import dataclass
import json
import re
import subprocess
import sys

from ..models import canonical


_CHECK = ("python", "-m", "contextrail.evaluation", "oracle-check")


@dataclass(frozen=True)
class MustRead:
    artifact: str
    revision: int
    reason: str = "Source of a fact required by the task."


@dataclass(frozen=True)
class Acceptance:
    commands: tuple[tuple[str, ...], ...] = (_CHECK,)
    expected_exit_code: int = 0


@dataclass(frozen=True)
class Oracle:
    required_facts: tuple[str, ...]
    forbidden_actions: tuple[str, ...]
    must_read: tuple[MustRead, ...]
    acceptance: Acceptance = Acceptance()


class _JsonObject(tuple):
    """Preserve duplicate object keys so conflicting answers can be rejected."""


def facts_present(answer: str, expected_facts) -> bool:
    """Accept JSON or plaintext fact declarations with exact, unambiguous values."""
    if not answer.strip():
        return False
    text = answer.strip()
    lines = text.splitlines()
    if len(lines) >= 3 and lines[0] in ("\x60\x60\x60json", "\x60\x60\x60") and lines[-1] == "\x60\x60\x60":
        text = "\n".join(lines[1:-1])
    bindings: dict[str, set[str]] = {}
    literals: set[str] = set()

    def bind(key, value):
        if isinstance(value, (str, int, float, bool)):
            literal = value if isinstance(value, str) else canonical(value)
            bindings.setdefault(key, set()).add(literal)

    def plain(value):
        for line in re.split(r"[;\n]", value):
            line = re.sub(r"^\s*(?:[-*]|\d+[.)])\s+", "", line).strip().strip("\x60")
            literals.add(line)
            match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(.+)", line)
            if match:
                key, literal = match.groups()
                bind(key, literal.strip().strip('"').strip("\x60"))

    def visit(value):
        if isinstance(value, _JsonObject):
            keys = [item for key, item in value if key == "key" and isinstance(item, str)]
            for key, item in value:
                if key == "value":
                    for label in keys:
                        bind(label, item)
                bind(key, item)
                if isinstance(item, (list, _JsonObject)):
                    visit(item)
                elif key == "fact" and isinstance(item, str):
                    plain(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str):
            plain(value)

    try:
        parsed = json.loads(text, object_pairs_hook=_JsonObject)
    except (ValueError, RecursionError):
        plain(text)
    else:
        try:
            visit(parsed)
        except (ValueError, RecursionError):
            return False
    for fact in expected_facts:
        for clause in fact.split(";"):
            clause = clause.strip()
            if not clause:
                continue
            key, separator, value = clause.partition("=")
            if separator:
                if bindings.get(key.strip()) != {value.strip()}:
                    return False
            elif clause not in literals:
                return False
    return True


def run_acceptance(oracle: Oracle, observations: dict) -> tuple[dict, ...]:
    """Execute only the bundled verifier. Evidence and replies go via stdin."""
    outcomes = []
    payload = canonical({**observations, "required_facts": oracle.required_facts})
    for command in oracle.acceptance.commands:
        if command != _CHECK:
            raise ValueError("Synthetic evaluation accepts only the bundled verifier command.")
        try:
            completed = subprocess.run(
                [sys.executable, *command[1:]], input=payload, encoding="utf-8",
                capture_output=True, check=False, timeout=15,
            )
            outcomes.append({"command": command, "exit_code": completed.returncode,
                             "passed": completed.returncode == oracle.acceptance.expected_exit_code})
        except (OSError, subprocess.TimeoutExpired):
            outcomes.append({"command": command, "exit_code": None, "passed": False})
    return tuple(outcomes)


def check_stdin() -> int:
    result = json.load(sys.stdin.buffer)
    return 0 if (
        facts_present(result["answer"], result["required_facts"])
        and not result["missing_reads"]
        and not result["wrong_revision_reads"]
        and not result["forbidden_actions_attempted"]
        and result["protocol_passed"]
        and not result["request_error"]
    ) else 1
