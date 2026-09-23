"""A frozen, API-agent-friendly SWE-bench Verified bridge.

This module intentionally does not reimplement SWE-bench's Docker verifier.
It freezes a diverse eight-instance slice and writes the prediction JSONL that
the official verifier consumes.  The official verifier remains the authority
for FAIL_TO_PASS and PASS_TO_PASS results.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from importlib.util import find_spec
import json
from pathlib import Path
import shutil
import subprocess
from typing import Iterable


@dataclass(frozen=True)
class SWEbenchInstance:
    instance_id: str
    repo: str
    base_commit: str
    version: str
    environment_setup_commit: str
    difficulty: str
    selection_reason: str


@dataclass(frozen=True)
class SWEbenchTaskInput:
    """Public, agent-visible fields from one official Verified row.

    Gold `patch`, `test_patch`, `FAIL_TO_PASS`, and `PASS_TO_PASS` are
    deliberately excluded.  They belong only to the official verifier.
    """

    instance_id: str
    repo: str
    base_commit: str
    version: str
    problem_statement: str
    problem_sha256: str


SWE_BENCH_VERIFIED_SUBSET: tuple[SWEbenchInstance, ...] = (
    SWEbenchInstance("astropy__astropy-12907", "astropy/astropy", "d16bfe05a744909de4b27f5875fe0d4ed41ce607", "4.3", "298ccb478e6bf092953bca67a3d29dc6c35f6752", "15 min - 1 hour", "scientific library; medium task"),
    SWEbenchInstance("django__django-10973", "django/django", "ddb293685235fd09e932805771ae97f72e817181", "3.0", "419a78300f7cd27611196e1e464d50fd0385ff27", "15 min - 1 hour", "web framework; cross-module task"),
    SWEbenchInstance("matplotlib__matplotlib-14623", "matplotlib/matplotlib", "d65c9ca20ddf81ef91199e6d819f9d3506ef477c", "3.1", "42259bb9715bbacbbb2abc8005df836f3a7fd080", "15 min - 1 hour", "visualization library; regression-sensitive task"),
    SWEbenchInstance("mwaskom__seaborn-3069", "mwaskom/seaborn", "54cab15bdacfaa05a88fbc5502a5b322d99f148e", "0.12", "d25872b0fc99dbf7e666a91f59bd4ed125186aa1", "15 min - 1 hour", "small repository control"),
    SWEbenchInstance("psf__requests-2931", "psf/requests", "5f7a3a74aab1625c2bb65f643197ee885e3da576", "2.9", "bbeb0001cdc657ac8c7fef98e154229bc392db0e", "15 min - 1 hour", "HTTP client; compatibility task"),
    SWEbenchInstance("pydata__xarray-2905", "pydata/xarray", "7c4e2ac83f7b4306296ff9b7b51aaf016e5ad614", "0.12", "1c198a191127c601d091213c4b3292a8bb3054e1", "15 min - 1 hour", "data library; cross-file task"),
    SWEbenchInstance("pytest-dev__pytest-10051", "pytest-dev/pytest", "aa55975c7d3f6c9f6d7f68accc41bb7cadf0eb9a", "7.2", "572b5657d7ca557593418ce0319fabff88800c73", "15 min - 1 hour", "test framework; self-hosted regression task"),
    SWEbenchInstance("sympy__sympy-11618", "sympy/sympy", "360290c4c401e386db60723ddb0109ed499c9f6e", "1.0", "50b81f9f6be151014501ffac44e5dc6b2416938f", "15 min - 1 hour", "symbolic math; longer codebase"),
)


def write_subset_manifest(path: Path) -> None:
    """Write the fixed task selection before an API run begins."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(item) for item in SWE_BENCH_VERIFIED_SUBSET], ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def write_predictions(path: Path, patches: Iterable[tuple[str, str]], *, model_name: str) -> None:
    """Emit the official SWE-bench prediction wire format without gold patches."""
    allowed = {item.instance_id for item in SWE_BENCH_VERIFIED_SUBSET}
    seen: set[str] = set()
    rows = []
    for instance_id, patch in patches:
        if instance_id not in allowed:
            raise ValueError(f"Instance is outside the frozen subset: {instance_id}")
        if instance_id in seen:
            raise ValueError(f"Duplicate SWE-bench prediction: {instance_id}")
        seen.add(instance_id)
        rows.append({"instance_id": instance_id, "model_name_or_path": model_name, "model_patch": patch})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def load_verified_subset() -> tuple[SWEbenchTaskInput, ...]:
    """Load and validate the eight selected rows from the official dataset.

    Importing datasets lazily keeps ContextRail's core package free of the
    heavyweight benchmark dependency.  The validation prevents an upstream
    dataset revision from silently changing the pinned benchmark inputs.
    """
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise RuntimeError("Install the `swebench` extra before loading official task inputs.") from exc
    rows = {str(row["instance_id"]): row for row in load_dataset("SWE-bench/SWE-bench_Verified", split="test")}
    selected: list[SWEbenchTaskInput] = []
    for frozen in SWE_BENCH_VERIFIED_SUBSET:
        row = rows.get(frozen.instance_id)
        if row is None:
            raise RuntimeError(f"Official SWE-bench dataset is missing {frozen.instance_id}.")
        for field, expected in (("repo", frozen.repo), ("base_commit", frozen.base_commit),
                                ("version", frozen.version)):
            actual = str(row.get(field, ""))
            if actual != expected:
                raise RuntimeError(f"Official row drift for {frozen.instance_id}: {field}={actual!r}, expected {expected!r}.")
        problem = str(row.get("problem_statement", ""))
        if not problem:
            raise RuntimeError(f"Official row {frozen.instance_id} has no problem statement.")
        selected.append(SWEbenchTaskInput(frozen.instance_id, frozen.repo, frozen.base_commit, frozen.version,
                                          problem, sha256(problem.encode("utf-8")).hexdigest()))
    return tuple(selected)


def write_official_task_inputs(path: Path, inputs: Iterable[SWEbenchTaskInput]) -> None:
    """Persist only agent-safe task inputs and their integrity hashes."""
    rows = [asdict(item) for item in inputs]
    expected = [item.instance_id for item in SWE_BENCH_VERIFIED_SUBSET]
    if [row["instance_id"] for row in rows] != expected:
        raise ValueError("Task inputs must be the complete frozen subset in its declared order.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"dataset": "SWE-bench/SWE-bench_Verified", "split": "test",
                                "gold_fields_excluded": ["patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS"],
                                "tasks": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verifier_command(predictions: Path, *, run_id: str, report_dir: Path) -> list[str]:
    """Build the current official CLI command restricted to this frozen slice."""
    if not run_id or any(character.isspace() for character in run_id):
        raise ValueError("run_id must be nonempty and contain no whitespace.")
    command = ["swebench", "eval", "verified", "--predictions", str(predictions), "--run-id", run_id,
               "--report-dir", str(report_dir)]
    for item in SWE_BENCH_VERIFIED_SUBSET:
        command.extend(("--instance", item.instance_id))
    return command


def environment_status() -> dict[str, bool]:
    """Report the verifier prerequisites, including whether Docker can actually run containers."""
    docker_cli = shutil.which("docker") is not None
    docker_daemon = False
    if docker_cli:
        try:
            probe = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                                   capture_output=True, text=True, timeout=10, check=False)
            docker_daemon = probe.returncode == 0 and bool(probe.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            docker_daemon = False
    return {"docker_cli": docker_cli, "docker_daemon": docker_daemon,
            "swebench_installed": find_spec("swebench") is not None}
