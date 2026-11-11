"""Deterministic task brief with a read-only view of existing learning jobs."""

from contextlib import closing
import sqlite3
import time

from .errors import BudgetExceeded, InvalidRequest
from .federation import bound_sources
from .models import canonical, integer
from .source_events import workspace_key


_PENDING = ("queued", "retry", "processing", "ready_to_save", "awaiting_index")


def learning_queue(binding, offset, limit):
    result = {"state": "not_configured", "scope": "workspace_jobs_not_task", "pending": None,
              "entries": [], "offset": offset, "next_offset": None}
    if binding is None or binding.memory_home is None:
        return result
    path = binding.memory_home / "state.sqlite"
    deadline = time.monotonic() + min(binding.timeout_seconds, 4)
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            db.row_factory = sqlite3.Row
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            db.create_function("capture_workspace", 1, workspace_key)
            db.execute("BEGIN")
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE name IN ('job_control','delivery_inbox')")}
            where = (" WHERE capture_workspace(json_extract(j.payload,'$.cwd'))=? "
                     "AND j.state IN (" + ",".join("?" for _ in _PENDING) + ")")
            workspace = workspace_key(binding.workspace)
            args = (workspace, *_PENDING)
            states = dict(db.execute("SELECT j.state,count(*) FROM jobs j" + where + " GROUP BY j.state", args).fetchall())
            if "job_control" in tables:
                control = ",c.reason,c.next_retry,c.stage,c.cancel_requested FROM jobs j LEFT JOIN job_control c ON j.id=c.job_id"
            else:
                control = ",NULL AS reason,NULL AS next_retry,NULL AS stage,NULL AS cancel_requested FROM jobs j"
            entries = [dict(row) for row in db.execute(
                "SELECT j.id,j.state,j.attempts,j.created,j.available" + control + where +
                " ORDER BY j.created,j.id LIMIT ? OFFSET ?", (*args, limit, offset))]
            deliveries = None
            if "delivery_inbox" in tables:
                deliveries = dict(db.execute(
                    "SELECT state,count(*) FROM delivery_inbox WHERE workspace=? AND state IN ('queued','retry') GROUP BY state",
                    (workspace,)).fetchall())
    except (OSError, sqlite3.Error, ValueError):
        return {**result, "state": "unavailable", "error": "existing_memory_state_unavailable"}
    return {**result, "state": "observed", "pending": sum(states.values()), "states": states,
            "entries": entries, "deliveries": deliveries, "source": "existing_memory.state.sqlite",
            "continuation": "original_learning_queue_retains_ownership"}


def offline_brief(tools, options, *, budget, queue_offset=0, queue_limit=10):
    integer(budget, 1)
    integer(queue_offset)
    integer(queue_limit, 1)
    if queue_limit > 50 or queue_offset > 1_000_000:
        raise InvalidRequest("Offline learning queue limits exceeded.")
    if budget > tools.max_read_bytes:
        raise InvalidRequest("Offline brief budget exceeds the bound reader limit.")
    learning = learning_queue(bound_sources(tools), queue_offset, queue_limit)
    offline = {"mode": "deterministic_local_read", "model_calls": 0, "learning": learning,
               "model_consumption": "unknown", "resume": "explicit_snapshot", "automatic_retry": False}
    original_entries = len(learning["entries"])

    def reserve():
        consumed = queue_offset + len(learning["entries"])
        learning["next_offset"] = consumed if learning["pending"] is not None and consumed < learning["pending"] else None
        learning["omitted_by_budget"] = original_entries - len(learning["entries"])
        return len(canonical({"offline": offline}).encode("utf-8")) + 16

    while learning["entries"] and reserve() > min(2048, budget // 3):
        learning["entries"].pop()

    try:
        available = budget - reserve()
        if available <= 0:
            raise BudgetExceeded("Offline brief metadata exceeds the response budget.")
        brief = tools.call("context.brief", {**options, "budget": available})
    except BudgetExceeded:
        if not learning["entries"]:
            raise
        learning["entries"].clear()
        available = budget - reserve()
        if available <= 0:
            raise
        brief = tools.call("context.brief", {**options, "budget": available})
    return {**brief, "budget": budget, "offline": offline}
