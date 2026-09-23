"""Reproducible API-agent evaluation components for ContextRail.

The package keeps the agent loop intentionally small so that A/B/C context
strategy is the measured variable.  It does not contain provider credentials.
"""

from .agent import CodingRun, MinimalApiAgent, RunResult, facts_present, run_stress_suite, summarize_comparison
from .models import DeterministicEvidenceModel, OpenAICompatibleModel
from .tasks import ALL_TASKS, PRESSURE_TASKS, STRESS_TASKS, StressTask

__all__ = [
    "DeterministicEvidenceModel",
    "ALL_TASKS",
    "CodingRun",
    "MinimalApiAgent",
    "OpenAICompatibleModel",
    "PRESSURE_TASKS",
    "RunResult",
    "STRESS_TASKS",
    "StressTask",
    "facts_present",
    "run_stress_suite",
    "summarize_comparison",
]
