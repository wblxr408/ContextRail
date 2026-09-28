"""Reproducible API-agent evaluation components for ContextRail.

The package keeps the agent loop intentionally small so that A/B/C context
strategy is the measured variable.  It does not contain provider credentials.
"""

from .agent import CodingRun, MinimalApiAgent, RunResult, facts_present, run_stress_suite, summarize_comparison
from .models import DeterministicEvidenceModel, OpenAICompatibleModel
from .semantic_ablation import ABLATION_TASKS, AblationTask, SemanticAblation, TaskAblation, run_ablation_suite
from .tasks import ALL_TASKS, PRESSURE_TASKS, STRESS_TASKS, StressTask

__all__ = [
    "DeterministicEvidenceModel",
    "ABLATION_TASKS",
    "AblationTask",
    "ALL_TASKS",
    "CodingRun",
    "MinimalApiAgent",
    "OpenAICompatibleModel",
    "PRESSURE_TASKS",
    "RunResult",
    "STRESS_TASKS",
    "SemanticAblation",
    "StressTask",
    "TaskAblation",
    "facts_present",
    "run_ablation_suite",
    "run_stress_suite",
    "summarize_comparison",
]
