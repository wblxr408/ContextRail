"""Explicit frozen-document adapter to the existing engineering-memory jobs queue."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from .directory_sync import _run
from .errors import BudgetExceeded, InvalidRequest, RailError, Unavailable
from .models import Ref, canonical, integer
from .loss import report


# A fixed child isolates installed service modules (listener/canonical/etc.) from
# Rail's interpreter. Only the existing queue is opened; no worker/model starts.
_ENQUEUE = """
import json,sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0,sys.argv[1])
from listener import Store
from history_import import enqueue
request=json.loads(sys.stdin.buffer.read())
home=Path(sys.argv[2])
if not (home/'state.sqlite').is_file():
    raise RuntimeError('Existing memory queue is required')
store=Store(home)
try:
    queued=enqueue(store,request['source'],request['scope'],request['event_key'],request['content'],request['occurred_at'],'document')
    print(json.dumps({'queued':queued},ensure_ascii=False))
finally:
    store.db.close()
"""


def enqueue_directory(store, config, lease, run, home, *, code=None, offset=0, limit=20, budget=8192):
    integer(offset)
    integer(limit, 1)
    integer(budget, 1)
    if limit > 100 or offset > 1000000:
        raise InvalidRequest("Memory enqueue page limit exceeds 100 or offset exceeds 1000000.")
    if budget < 1536:
        raise BudgetExceeded("Explicit memory enqueue requires at least 1536 response bytes.")
    home = Path(home).resolve(strict=True)
    app = Path(code).resolve(strict=True) if code is not None else home / "app"
    # An explicit fixture/data home still reuses the installed application code.
    if code is None and not (app / "history_import.py").is_file():
        app = Path.home() / ".codex/services/engineering-memory/app"
    if not (home / "state.sqlite").is_file() or not (app / "history_import.py").is_file():
        raise Unavailable("An existing memory queue and its trusted document importer are required.")
    entries, queued, failures = [], 0, 0
    # Keep ownership/freeze stable while committing to the independent existing
    # queue. Queue and Rail are not a cross-database transaction; stable IDs make
    # a retry safe after the worker succeeded but transport lost its receipt.
    with store.read_transaction():
        store._lease(config.scope, lease)
        row = _run(store, config.scope, run)
        total = store.db.execute("SELECT COUNT(*) FROM directory_entries WHERE scope=? AND run=?", (config.scope.key, run)).fetchone()[0]
        page = store.db.execute("SELECT * FROM directory_entries WHERE scope=? AND run=? ORDER BY sequence LIMIT ? OFFSET ?", (config.scope.key, run, limit, offset)).fetchall()
        for entry in page:
            receipt = {"path": entry["path"], "status": "not_captured", "queued": 0}
            entries.append(receipt)
            if entry["outcome"] not in {"captured", "unchanged"}:
                continue
            if Path(entry["path"]).suffix.casefold() not in {".md", ".txt", ".json"}:
                receipt["status"] = "unsupported"
                continue
            try:
                ref = Ref(entry["name"], entry["revision"])
                receipt["ref"] = {"name": ref.name, "revision": ref.revision}
                content = store.get(config.scope, ref).content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
                version_stat = json.loads(entry["stat"])
                occurred = datetime.fromtimestamp(version_stat["mtime_ns"] / 1e9, timezone.utc).isoformat()
                request = {"source": str(config.workspace / version_stat.get("source_path", entry["path"])), "scope": str(config.workspace),
                           "event_key": "rail-directory:" + config.scope.key + ":" + row["source"] + ":" + ref.name + ":" + str(ref.revision),
                           "content": content, "occurred_at": occurred}
                process = subprocess.run([sys.executable, "-c", _ENQUEUE, str(app), str(home)],
                    input=canonical(request).encode("utf-8"), capture_output=True, timeout=30,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if process.returncode:
                    receipt.update(status="failed", error="existing_memory_queue_failed; retry_same_run_and_offset")
                    failures += 1
                    continue
                count = json.loads(process.stdout)["queued"]
                if type(count) is not int or count < 0:
                    raise ValueError("Invalid queue receipt")
                receipt.update(status="queued_not_yet_classified", queued=count)
                queued += count
            except RailError:
                receipt.update(status="unavailable", error="saved_revision_unavailable")
                failures += 1
            except UnicodeError:
                receipt.update(status="unsupported", error="non_utf8_document")
            except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
                receipt.update(status="failed", error="queue_receipt_unavailable; retry_same_run_and_offset")
                failures += 1
    processed = len(entries)
    accepted=sum(e['status']=='queued_not_yet_classified' for e in entries)
    unsupported=sum(e['status']=='unsupported' for e in entries)
    result = {"schema": "contextrail.directory-memory.v1", "run": run, "status": "partial_failure" if failures else "queued_not_yet_classified",
              "queued": queued, "failed": failures, "offset": offset, "total": total, "processed": processed, "entries": entries,
              "returned": len(entries), "next_offset": offset + processed if offset + processed < total else None,
              "continuation": "same_run_next_offset; retry_same_offset_is_idempotent", "identity": "scope/source/saved_revision; not_global_cross_importer_dedup"}
    result['knowledge_saved']='unknown_not_observed'
    result['loss']={'memory_import':report('memory_import','files',total=total,selected=accepted,unsupported=unsupported,unknown=total-processed,
        scope='this_page_of_exact_directory_run',reasons={'unsupported_in_page':unsupported,'unobserved_outside_page':total-processed,'failed_in_page':failures},
        recover={'command':'directory-memory-enqueue','run':run,'offset':offset,'limit':limit}),
        'import_receipts':report('import_receipts','receipts',total=processed,selected=len(entries),unknown=0,
            scope='this_committed_page',recover={'command':'directory-memory-enqueue','run':run,'offset':offset,'limit':limit})}
    while len(canonical(result).encode("utf-8")) > budget:
        if not entries:
            raise BudgetExceeded("Memory enqueue metadata exceeds the response budget; effects may have committed, retry exact page safely.")
        entries.pop()
        result['loss']['import_receipts'].update(selected=len(entries),omitted=processed-len(entries),truncated=processed-len(entries))
        result.update(returned=len(entries), next_offset=offset + len(entries),
                      continuation="increase_budget_or_retry_same_page; some_receipts_omitted")
    return result
