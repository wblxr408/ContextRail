"""Measure an actual selected task and explicitly scoped native evidence locally."""

from contextlib import closing, contextmanager
from dataclasses import asdict, replace
import json
from pathlib import Path
import sqlite3
import tempfile
from time import perf_counter_ns

from ..bridge import BridgeEvidence
from ..errors import InvalidRequest, NotFound
from ..host import ContextTools
from ..intent import declared
from ..models import Ref, canonical, identifier, integer, unicode_text
from ..project import load_project
from ..store import Store


def _object(path, limit=1024 * 1024):
    with Path(path).open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise InvalidRequest("Calibration source exceeds its declared size limit.")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise InvalidRequest("Calibration source requires a JSON object.")
    return value, len(raw)


def _config(case):
    for field in ("task", "session", "snapshot"):
        identifier(case[field])
    return load_project(case["workspace"], overrides={"task": case["task"], "store": case.get("store")})


@contextmanager
def _read_copy(path):
    # Normal Store opening can apply migrations. Benchmark on a consistent
    # private copy so source schema, events and write activity remain untouched.
    with tempfile.TemporaryDirectory(prefix="rail-calibration-") as temp:
        target = Path(temp) / "state.sqlite3"
        with (closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as source,
              closing(sqlite3.connect(target)) as dest):
            source.backup(dest)
        with Store(target) as store:
            yield store


def _milliseconds(start):
    return round((perf_counter_ns() - start) / 1_000_000, 3)


def _score_next_action(actual, expected):
    return None if expected is None else actual == expected


def _ref_key(ref):
    return ref.name, ref.revision


def _measure(store, config, case):
    oracle = case["oracle"]
    next_step = oracle["next_action"]
    if next_step is not None:
        unicode_text(next_step)
        if not next_step.strip() or len(next_step.encode("utf-8")) > 8192:
            raise InvalidRequest("Expected next action must be nonempty text within 8 KiB.")
    ids = oracle["required_ids"]
    if not isinstance(ids, list) or len(ids) > 100:
        raise InvalidRequest("Select up to 100 expected requirement IDs.")
    for requirement in ids:
        identifier(requirement)
    cold = Ref(**oracle["cold_ref"])
    expected_cold = oracle["cold_text"]
    if not isinstance(expected_cold, str) or not expected_cold or len(expected_cold.encode("utf-8")) > config.read_bytes:
        raise InvalidRequest("Select a nonempty expected cold text window within the read budget.")
    budget = case.get("budget", config.context_bytes)
    integer(budget, 1)
    if budget > config.read_bytes:
        raise InvalidRequest("Calibration packet budget exceeds the reader limit.")

    bridge = BridgeEvidence(store, config.scope, case["session"])
    start = perf_counter_ns()
    with store.read_transaction():
        manifest, bundle, _ = bridge._resolve(case["snapshot"])
        selections = store.load_snapshot(config.scope, case["snapshot"])["evidence"]
        cold_selected = any(_ref_key(Ref(**row["ref"])) == _ref_key(cold) for row in selections)
        if not cold_selected:
            raise InvalidRequest("Cold evidence must be selected in the exact calibration snapshot.")
        source = store.get(config.scope, Ref(cold.name, cold.revision)).content
        baseline = len(bundle.raw) + sum(map(len, bundle.sources.values()))
        if _ref_key(cold) not in {_ref_key(Ref(**ref)) for ref in manifest["sources"].values()}:
            baseline += len(source)
        anchors = declared(bundle)
        raw_next = anchors["latest_request"]["statement"] if anchors else None
        raw_ids = {row["id"] for row in bundle.ledger["requirements"] if row["status"] == "active"}
        raw = {"delivered_bytes": baseline, "elapsed_ms": _milliseconds(start),
               "next_action_correct": _score_next_action(raw_next, next_step),
               "missing_required_ids": sorted(set(ids) - raw_ids),
               "cold_text_exact": source[cold.start:cold.end] == expected_cold.encode("utf-8"),
               "cold_body_bytes": len(source)}

    start = perf_counter_ns()
    packet = bridge.packet(case["snapshot"], budget=budget)
    reader = ContextTools(store, config.scope, case["session"], max_read_bytes=config.read_bytes)
    read = reader.call("context.get", asdict(cold))
    elapsed = _milliseconds(start)
    refs = {_ref_key(Ref(**row["ref"])) for key in ("evidence", "stable_evidence")
            for row in packet["context"][key] if "text" in row}
    inline = {name for name, req in manifest["requirements"].items() if _ref_key(Ref(**req["ref"])) in refs}
    delivered = len(canonical(packet).encode("utf-8")) + len(canonical(read).encode("utf-8"))
    exact = read["encoding"] == "utf-8" and read["content"] == expected_cold
    recovered = {"delivered_bytes": delivered, "elapsed_ms": elapsed,
                 "next_action_correct": _score_next_action(packet["resume_intent"].get("next_action"), next_step),
                 "missing_required_ids": sorted(set(ids) - inline), "cold_text_exact": exact,
                 "cold_body_bytes": len(read["content"].encode("utf-8")) if read["encoding"] == "utf-8" else None,
                 "cold_page_was_inline": _ref_key(cold) in refs, "cold_reads": 1,
                 "reported_loss": packet["loss"]}
    return {"full_source": raw, "packet_with_cold_read": recovered}


def _two_tasks(store, config, case):
    other = case.get("secondary")
    if other is None:
        return None
    for field in ("task", "session", "snapshot"):
        identifier(other[field])
    if other["task"] == config.scope.task:
        raise InvalidRequest("The coexistence scenario needs a distinct second task.")
    other_scope = replace(config.scope, task=other["task"])
    start = perf_counter_ns()
    primary = BridgeEvidence(store, config.scope, case["session"])
    secondary = BridgeEvidence(store, other_scope, other["session"])
    first = primary.packet(case["snapshot"], budget=case.get("budget", config.context_bytes))
    second = secondary.packet(other["snapshot"], budget=case.get("budget", config.context_bytes))
    try:
        primary.packet(other["snapshot"], budget=case.get("budget", config.context_bytes))
    except NotFound:
        cross_read_rejected = True
    else:
        cross_read_rejected = False
    return {"task_ids": [first["task_id"], second["task_id"]], "cross_read_rejected": cross_read_rejected,
            "secondary_objective_correct": second["context"]["task"]["objective"] == other["objective"],
            "delivered_bytes": sum(len(canonical(packet).encode("utf-8")) for packet in (first, second)),
            "elapsed_ms": _milliseconds(start)}


def _interruption(case, workspace):
    selected = case.get("interruption")
    if selected is None:
        return None
    start = perf_counter_ns()
    report, size = _object(selected["report"])
    if (report.get("schema") != "contextrail.native-history.v1"
            or Path(report["workspace"]).resolve() != workspace
            or report.get("client") != selected["client"] or report.get("native_session") != selected["native_session"]):
        raise InvalidRequest("Interruption report does not identify the selected workspace and native session.")
    gaps = sorted({row["reason"] for row in report["gaps"]})
    return {"client": report["client"], "expected_gap_observed": selected["expected_gap"] in gaps, "gaps": gaps,
            "pending_calls": len(report["pending_calls"]), "delivered_bytes": size, "review_elapsed_ms": _milliseconds(start),
            "native_elapsed_ms": None, "model_next_action_correct": None}


def _outage(case):
    selected = case.get("outage")
    if selected is None:
        return None
    report, _ = _object(selected["report"])
    receipt = report.get("managed_receipt")
    config = _config(selected)
    if (not isinstance(receipt, dict) or receipt.get("task") != config.scope.task
            or receipt.get("snapshot") != selected["snapshot"] or receipt.get("client") != selected["client"]):
        raise InvalidRequest("Native outage receipt does not identify the selected fallback task and snapshot.")
    start = perf_counter_ns()
    with _read_copy(config.store) as store:
        bridge = BridgeEvidence(store, config.scope, selected["session"])
        packet = bridge.packet(selected["snapshot"], budget=selected.get("budget", config.context_bytes))
        if not any(row["metadata"] == receipt for row in store.events(config.scope)
                   if row["kind"] == "native_managed.finished"):
            raise InvalidRequest("Native outage receipt was not recorded by the selected task.")
    return {"client": receipt["client"], "state": receipt["state"], "rate_limited": report.get("rate_limited"),
            "model_packet_read": report.get("packet_wire_exact"), "native_elapsed_seconds": report.get("elapsed_seconds"),
            "model_next_action_correct": None,
            "offline_objective_correct": packet["context"]["task"]["objective"] == selected["objective"],
            "offline_delivered_bytes": len(canonical(packet).encode("utf-8")), "offline_elapsed_ms": _milliseconds(start)}


def calibrate(path):
    case, _ = _object(path, limit=65536)
    if case.get("schema") != 1:
        raise InvalidRequest("Unsupported self-use calibration case schema.")
    config = _config(case)
    with _read_copy(config.store) as store:
        comparison = _measure(store, config, case)
        two_tasks = _two_tasks(store, config, case)
    recovered = comparison["packet_with_cold_read"]
    return {"schema": "contextrail.self-use-calibration/v1", "task": config.scope.task, "snapshot": case["snapshot"],
            "comparison": comparison,
            "scenarios": {"large_log": {"source_bytes": comparison["full_source"]["cold_body_bytes"]},
                          "late_chinese_correction": recovered["next_action_correct"],
                          "two_tasks": two_tasks, "client_interruption": _interruption(case, config.workspace),
                          "cold_read": {"exact": recovered["cold_text_exact"], "calls": recovered["cold_reads"]},
                          "model_unavailable": _outage(case)},
            "measure": "local_source_delivery_and_observed_native_receipts", "provider_tokens": None,
            "model_task_success": None}
