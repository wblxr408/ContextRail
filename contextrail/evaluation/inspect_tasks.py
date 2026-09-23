"""Inspect AI entry point for the frozen stress-task catalog.

Run with an Inspect-supported model after installing the optional `eval` extra:
`inspect eval contextrail.evaluation.inspect_tasks --model <provider/model>`.
The task is intentionally diagnostic: ContextRail's full A/B/C tool loop and
its authoritative trace/report are run by `python -m contextrail.evaluation`.
"""

from __future__ import annotations

import json

try:  # Keep the core library importable without evaluation extras.
    from inspect_ai import Task, task
    from inspect_ai.dataset import Sample
    from inspect_ai.scorer import includes
    from inspect_ai.solver import generate, system_message
except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
    raise ImportError("Install ContextRail's evaluation extra: uv sync --extra eval") from exc

from contextrail.evaluation.tasks import STRESS_TASKS


@task
def contextrail_stress_diagnostic() -> Task:
    """A shared Inspect log for baseline long-evidence fact recovery.

    This diagnostic never replaces the A/B/C runner: it gives Inspect users a
    standard task list, model log and scorer alongside the host-level report.
    """
    dataset = [
        Sample(
            input=json.dumps({"task_id": item.id, "strategy": "A", "objective": item.objective,
                              "evidence": [evidence.text for evidence in item.evidence]}, ensure_ascii=False),
            target="\n".join(item.expected_facts),
            id=item.id,
        )
        for item in STRESS_TASKS
    ]
    return Task(dataset=dataset, solver=[system_message("Return the exact authoritative facts only."), generate()],
                scorer=includes())
