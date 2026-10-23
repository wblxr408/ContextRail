"""Bounded independent evidence reads, preserving input identity and settled state."""

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

from .errors import BudgetExceeded, InvalidRequest, RailError
from .models import canonical, identifier, integer
from .store import Store


def read_batch(tools, calls, *, concurrency=2, per_call_bytes=2048, budget=8192):
    from .host import ContextTools
    tools.store.session(tools.scope, tools.session_id)
    if tools.store.db.in_transaction:
        raise InvalidRequest("Batch reads require no active host transaction.")
    integer(concurrency, 1)
    integer(per_call_bytes, 1)
    integer(budget, 1)
    if concurrency > 8 or per_call_bytes > tools.max_read_bytes or budget > tools.max_read_bytes:
        raise InvalidRequest("Batch concurrency or byte budget exceeds the bound limits.")
    if not isinstance(calls, list) or len(calls) > 32:
        raise InvalidRequest("A read batch accepts up to 32 independent calls.")
    identities = set()
    for call in calls:
        if (not isinstance(call, dict) or set(call) != {"id", "tool", "arguments"}
                or call["tool"] not in ("context.get", "context.extract") or not isinstance(call["arguments"], dict)):
            raise InvalidRequest("Only independent get/extract calls with id/tool/arguments are batchable.")
        identifier(call["id"])
        if call["id"] in identities:
            raise InvalidRequest("Batch call IDs must be unique.")
        identities.add(call["id"])
    try:
        frozen = canonical(calls)
        if len(frozen.encode("utf-8")) > 128 * 1024 or any(len(canonical(call["arguments"]).encode("utf-8")) > 16384 for call in calls):
            raise InvalidRequest("Batch input exceeds its total or per-call byte limit.")
        calls = json.loads(frozen)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise InvalidRequest("Batch requests must contain valid JSON data.") from None
    database = next(row["file"] for row in tools.store.db.execute("PRAGMA database_list") if row["name"] == "main")
    if concurrency > 1 and not database:
        raise InvalidRequest("In-memory evidence requires explicit concurrency=1; no second database is copied.")
    effective = min(concurrency, len(calls))
    result = {"schema": "contextrail.read-batch.v1", "consistency": "independent_per_call_read_snapshot",
              "concurrency": effective, "per_call_bytes": per_call_bytes, "results": [],
              "succeeded": 0, "failed": 0, "omitted": 0}
    # Reserve all IDs/requests/statuses and stable error-code space before I/O.
    # Actual results can be omitted, but these settled identities must never be lost.
    result["results"] = [{"id": call["id"], "tool": call["tool"], "request": call["arguments"],
                           "status": "succeeded", "delivery": "omitted", "error": {"code": "x" * 64}} for call in calls]
    result.update(succeeded=32, failed=32, omitted=32)
    if len(canonical(result).encode("utf-8")) > budget:
        raise BudgetExceeded("Batch budget cannot preserve every call identity, request and settled status.")

    def execute(call, store):
        entry = {"id": call["id"], "tool": call["tool"], "request": call["arguments"], "delivery": "full"}
        try:
            with store.read_transaction():
                reader = ContextTools(store, tools.scope, tools.session_id, max_read_bytes=per_call_bytes)
                arguments = dict(call["arguments"])
                if call["tool"] == "context.extract":
                    arguments.setdefault("budget", per_call_bytes)
                output = reader.call(call["tool"], arguments)
                if len(canonical(output).encode("utf-8")) > per_call_bytes:
                    raise BudgetExceeded("Single result exceeds its complete JSON byte budget; retry with a larger per-call budget or narrower read.")
            entry.update(status="succeeded", result=output)
            if call["tool"] == "context.extract" and output["status"] in ("parse_failed", "incomplete"):
                entry.update(status="failed", error={"code": output["error"]["code"], "message": output["error"]["message"]})
        except RailError as exc:
            entry.update(status="failed", error={"code": exc.code, "message": str(exc)[:160]})
        except (sqlite3.Error, OSError):
            entry.update(status="failed", error={"code": "storage_error", "message": "The existing read view is unavailable."})
        return entry

    def worker(call):
        try:
            with Store.open_readonly(database, clock=tools.store.clock) as reader:
                return execute(call, reader)
        except RailError as exc:
            return {"id": call["id"], "tool": call["tool"], "request": call["arguments"], "status": "failed",
                    "delivery": "full", "error": {"code": exc.code, "message": str(exc)[:160]}}
        except (sqlite3.Error, OSError):
            return {"id": call["id"], "tool": call["tool"], "request": call["arguments"], "status": "failed",
                    "delivery": "full", "error": {"code": "storage_error"}}

    if effective > 1:
        with ThreadPoolExecutor(max_workers=effective) as pool:
            result["results"] = list(pool.map(worker, calls))
    else:
        result["results"] = [execute(call, tools.store) for call in calls]
    result["succeeded"] = sum(entry["status"] == "succeeded" for entry in result["results"])
    result["failed"] = len(calls) - result["succeeded"]
    result["omitted"] = 0
    if len(canonical(result).encode("utf-8")) > budget:
        for entry in result["results"]:
            entry.get("error", {}).pop("message", None)
    while len(canonical(result).encode("utf-8")) > budget:
        entry = max((entry for entry in result["results"] if "result" in entry),
                    key=lambda entry: len(canonical(entry["result"]).encode("utf-8")), default=None)
        if entry is None:
            # The pre-dispatch reservation is larger than every valid settled
            # envelope. Reaching this branch indicates a broken local contract.
            raise BudgetExceeded("Settled batch metadata cannot fit the response budget.")
        del entry["result"]
        entry["delivery"] = "omitted"
        result["omitted"] += 1
    return result
