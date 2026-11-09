"""Measurements derived from request records and evidence tool observations."""

from collections import defaultdict
import math


def distribution(values) -> dict:
    samples = sorted(value for value in values if value is not None)
    return {"n": len(samples), **{
        name: samples[math.ceil(q * len(samples)) - 1] if samples else None
        for name, q in (("p50", 0.5), ("p95", 0.95))
    }}


def ratio(numerator, denominator):
    return numerator / denominator if numerator is not None and denominator else None


def common_prefix_length(left, right) -> int:
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b:
            return index
    return min(len(left), len(right))


def summarize_telemetry(results) -> dict:
    grouped = defaultdict(list)
    for result in results:
        grouped[result.strategy].append(result)
    summary = {}
    for strategy, runs in grouped.items():
        requests = [record for run in runs for record in run.requests]
        observed = [record for record in requests if record["cached_input_units"] is not None]
        by_status = {
            "hot": [record for record in observed if record["cached_input_units"] > 0],
            "cold": [record for record in observed if record["cached_input_units"] == 0],
            "unknown": [record for record in requests if record["cached_input_units"] is None],
        }
        cold_pages = [event.details for run in runs for event in run.trace
                      if event.event == "tool.result" and event.details.get("cold_page")]
        comparable_cache = [r for r in observed if r["input_units"] is not None]
        writes = [r["cache_write_units"] for r in requests if r["cache_write_units"] is not None]
        summary[strategy] = {
            "requests": len(requests),
            "cache": {
                "observed_requests": len(observed),
                "unknown_requests": len(requests) - len(observed),
                "observed_read_tokens": sum(r["cached_input_units"] for r in observed) if observed else None,
                "write_observations": len(writes),
                "observed_write_tokens": sum(writes) if writes else None,
                "hit_rate": ratio(len(by_status["hot"]), len(observed)),
                "cached_token_ratio": ratio(sum(r["cached_input_units"] for r in comparable_cache),
                                             sum(r["input_units"] for r in comparable_cache)),
            },
            "request_latency": {
                status: {field: distribution(r[field] for r in records)
                         for field in ("ttft_ms", "completion_ms")}
                for status, records in by_status.items()
            },
            "run_latency": {
                status: {field: distribution(getattr(run, field) for run in runs if run.cache_state == status)
                         for field in ("ttft_ms", "latency_ms")}
                for status in ("cold", "hot", "unknown")
            },
            "repeated_prefix_token_ratio": distribution(r["repeated_prefix_token_ratio"] for r in requests),
            "packet_bytes_per_input_token": distribution(r["packet_bytes_per_input_token"] for r in requests),
            "cold_pages": {
                "requests": len(cold_pages),
                "successful_reads": sum(page["ok"] for page in cold_pages),
                "bytes": sum(page.get("source_bytes", 0) for page in cold_pages),
                "round_trip_ms": distribution(page["latency_ms"] for page in cold_pages),
                "followup_model_requests": sum(bool(r["cold_page_followup"]) for r in requests),
                "followup_completion_ms": distribution(r["completion_ms"] for r in requests if r["cold_page_followup"]),
                "repeated_read_rate": ratio(sum(page["repeat"] for page in cold_pages if page["ok"]),
                                            sum(page["ok"] for page in cold_pages)),
            },
            "summary_requests": sum(r["phase"] == "summary" for r in requests),
        }
    return summary
