"""Read-only S0 readiness checks for a real provider evaluation run."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from .pricing import PriceBook
from .swebench import environment_status


def api_preflight(*, price_file: Path | None = None) -> dict:
    """Return machine-readable readiness without exposing credentials.

    The MinimalApiAgent owns every outbound message, so it can attest to final
    request capture and ContextRail-slot replacement.  Credentials and a
    reachable provider remain environment-owned and are never written here.
    """
    base_url = os.environ.get("CONTEXT_RAIL_EVAL_API_BASE_URL", "").strip()
    api_key = os.environ.get("CONTEXT_RAIL_EVAL_API_KEY", "").strip()
    model = os.environ.get("CONTEXT_RAIL_EVAL_MODEL", "").strip()
    parsed = urlparse(base_url)
    endpoint_valid = bool(parsed.scheme in {"http", "https"} and parsed.netloc)
    price_valid = None
    price_error = None
    if price_file is not None:
        try:
            PriceBook.from_path(price_file)
            price_valid = True
        except (OSError, KeyError, TypeError, ValueError) as exc:
            price_valid, price_error = False, str(exc)
    checks = {
        "api_base_url_configured": bool(base_url),
        "api_base_url_valid": endpoint_valid,
        "api_key_configured": bool(api_key),
        "model_configured": bool(model),
        "final_request_capture": True,
        "context_slot_replaced_each_turn": True,
        "request_usage_ledger": True,
        "price_file_valid": price_valid,
        "swebench": environment_status(),
    }
    ready = endpoint_valid and bool(api_key) and bool(model)
    return {"schema": "contextrail.evaluation-preflight/v1", "provider_api_ready": ready,
            "checks": checks, "price_error": price_error}
