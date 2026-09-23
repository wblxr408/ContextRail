"""Direct, small adapters around the upstream SWE-agent and OpenHands runtimes.

The adapters deliberately do not reproduce either project's agent loop.  They
only translate the dashboard request into the public CLI / REST contracts,
stream observable progress, and preserve non-secret run artifacts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SWE_AGENT_REVISION = "3ea751c087f32b16e039a2233dd6eefecef325d5"
OPENHANDS_SDK_REVISION = "b5c8ab950401996f171b29c076900fa22fc80e21"
OPENHANDS_API_PREFIX = "/api"
Log = Callable[[str], None]


@dataclass(frozen=True)
class ExternalModelProfile:
    base_url: str
    api_key: str
    model: str


@dataclass(frozen=True)
class ExternalResult:
    agent: str
    status: str
    artifacts: list[str]
    details: dict[str, Any]


class UpstreamAgentError(RuntimeError):
    """A concise, redaction-safe failure from an upstream runtime."""


def sweagent_executable() -> str | None:
    """Prefer the dedicated project venv, then an activated user installation."""
    candidate = Path.cwd() / ".agent-venv" / "Scripts" / "sweagent.exe"
    if candidate.is_file():
        return str(candidate)
    return shutil.which("sweagent")


def run_sweagent(
    *,
    profile: ExternalModelProfile,
    repository_url: str,
    issue_url: str,
    output_dir: Path,
    cost_limit: float,
    log: Log,
    executable: str | None = None,
) -> ExternalResult:
    """Run the official ``sweagent run`` command and retain its own artifacts."""
    command_path = executable or sweagent_executable()
    if not command_path:
        raise UpstreamAgentError(
            "SWE-agent is not installed. Run the documented .agent-venv install command first."
        )
    if not repository_url.startswith("https://github.com/"):
        raise ValueError("SWE-agent repository must be a https://github.com/ URL.")
    if not issue_url.startswith(repository_url.rstrip("/") + "/issues/"):
        raise ValueError("SWE-agent issue URL must belong to the selected repository.")
    if not 0.01 <= cost_limit <= 100:
        raise ValueError("SWE-agent per-instance cost limit must be between 0.01 and 100 USD.")

    output_dir.mkdir(parents=True, exist_ok=False)
    command = [
        command_path,
        "run",
        f"--agent.model.name={profile.model}",
        f"--agent.model.per_instance_cost_limit={cost_limit:.2f}",
        f"--env.repo.github_url={repository_url}",
        f"--problem_statement.github_url={issue_url}",
    ]
    environment = os.environ.copy()
    # LiteLLM-backed upstream versions read these names.  The key only exists in
    # the child process environment and is never written to the result folder.
    environment.update({
        "OPENAI_API_KEY": profile.api_key,
        "OPENAI_API_BASE": profile.base_url,
        "OPENAI_BASE_URL": profile.base_url,
        # The upstream CLI uses Rich output containing emoji.  Force UTF-8 for
        # its isolated child process on legacy Windows code pages.
        "PYTHONUTF8": "1",
    })
    log("SWE-agent: starting official CLI (Docker/cloud runtime setup follows upstream configuration).")
    process = subprocess.Popen(
        command,
        cwd=output_dir,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    lines: list[str] = []
    assert process.stdout is not None
    for raw in process.stdout:
        line = _redact(raw.rstrip(), profile.api_key)
        if line:
            lines.append(line)
            log(f"SWE-agent: {line}")
    returncode = process.wait()
    (output_dir / "sweagent.stdout.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    artifacts = [path.relative_to(output_dir).as_posix() for path in output_dir.rglob("*") if path.is_file()]
    result = ExternalResult(
        agent="swe-agent",
        status="complete" if returncode == 0 else "failed",
        artifacts=artifacts,
        details={
            "upstream_revision": SWE_AGENT_REVISION,
            "returncode": returncode,
            "repository_url": repository_url,
            "issue_url": issue_url,
            "model": profile.model,
            "api_base_url": profile.base_url,
            "api_key_persisted": False,
        },
    )
    _write_result(output_dir, result)
    if returncode:
        raise UpstreamAgentError(f"SWE-agent exited with code {returncode}. Open sweagent.stdout.log for the upstream trace.")
    log("SWE-agent: complete; official trajectory and patch artifacts were retained.")
    return result


def openhands_health(*, server_url: str, server_api_key: str, timeout: float = 5) -> dict[str, Any]:
    """Check the official OpenAPI contract before attempting a conversation."""
    value = _request_json("GET", _join(server_url, "/openapi.json"), server_api_key, timeout=timeout)
    if not isinstance(value, dict) or f"{OPENHANDS_API_PREFIX}/conversations" not in value.get("paths", {}):
        raise UpstreamAgentError("OpenHands endpoint did not expose the expected /api/conversations OpenAPI contract.")
    return {"title": value.get("info", {}).get("title", "OpenHands Agent Server"), "version": value.get("info", {}).get("version", "unknown")}


def run_openhands(
    *,
    profile: ExternalModelProfile,
    server_url: str,
    server_api_key: str,
    workspace: str,
    task: str,
    output_dir: Path,
    max_iterations: int,
    max_runtime_seconds: int,
    log: Log,
) -> ExternalResult:
    """Drive a real OpenHands Agent Server conversation through its REST API."""
    if not server_url.startswith(("http://", "https://")):
        raise ValueError("OpenHands Agent Server URL must start with http:// or https://.")
    if not workspace or len(workspace) > 500:
        raise ValueError("OpenHands workspace is required.")
    if not task or len(task) > 12_000:
        raise ValueError("OpenHands task must contain 1 to 12000 characters.")
    if not 1 <= max_iterations <= 500:
        raise ValueError("OpenHands max iterations must be 1 to 500.")
    if not 10 <= max_runtime_seconds <= 3600:
        raise ValueError("OpenHands runtime limit must be 10 to 3600 seconds.")

    health = openhands_health(server_url=server_url, server_api_key=server_api_key)
    output_dir.mkdir(parents=True, exist_ok=False)
    log(f"OpenHands: connected to {health['title']} {health['version']}.")
    # This is StartConversationRequest exactly as defined by the official SDK:
    # a native Agent with an LLM, a LocalWorkspace, then a user event that
    # starts the background loop.  ``base_url`` makes the profile's custom API
    # endpoint part of the actual agent configuration rather than dashboard-only
    # metadata.
    create_payload = {
        "agent": {
            "kind": "Agent",
            "llm": {
                "model": profile.model,
                "api_key": profile.api_key,
                "base_url": profile.base_url,
                "usage_id": "contextrail-evaluation",
            },
        },
        "workspace": {"kind": "LocalWorkspace", "working_dir": workspace},
        "max_iterations": max_iterations,
        "autotitle": False,
        "tags": {"origin": "contextrail", "evaluation": "external-agent"},
    }
    created = _request_json("POST", _join(server_url, f"{OPENHANDS_API_PREFIX}/conversations"), server_api_key, create_payload)
    conversation_id = str(created.get("id", "")) if isinstance(created, dict) else ""
    if not conversation_id:
        raise UpstreamAgentError("OpenHands created no conversation id.")
    log(f"OpenHands: conversation {conversation_id} created.")
    _request_json(
        "POST",
        _join(server_url, f"{OPENHANDS_API_PREFIX}/conversations/{conversation_id}/events"),
        server_api_key,
        {"role": "user", "content": [{"type": "text", "text": task}], "run": True},
    )
    log("OpenHands: task sent; streaming official event pages.")
    deadline = time.monotonic() + max_runtime_seconds
    prior_event_count = -1
    conversation: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        conversation = _request_json("GET", _join(server_url, f"{OPENHANDS_API_PREFIX}/conversations/{conversation_id}"), server_api_key)
        events = _all_events(server_url, server_api_key, conversation_id)
        if len(events) != prior_event_count:
            prior_event_count = len(events)
            log(f"OpenHands: {len(events)} event(s), status={conversation.get('execution_status', 'unknown')}.")
        if str(conversation.get("execution_status", "")).upper() in {"IDLE", "STOPPED", "ERROR", "FAILED", "FINISHED"}:
            break
        time.sleep(0.8)
    else:
        _request_json("POST", _join(server_url, f"{OPENHANDS_API_PREFIX}/conversations/{conversation_id}/interrupt"), server_api_key)
        raise UpstreamAgentError(f"OpenHands exceeded {max_runtime_seconds}s and was interrupted.")

    final = _request_json(
        "GET", _join(server_url, f"{OPENHANDS_API_PREFIX}/conversations/{conversation_id}/agent_final_response"), server_api_key
    )
    (output_dir / "openhands-events.json").write_text(json.dumps(events, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "openhands-conversation.json").write_text(
        json.dumps(_strip_secrets(conversation), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "openhands-final-response.json").write_text(
        json.dumps(_strip_secrets(final), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    status = str(conversation.get("execution_status", "unknown")).lower()
    result = ExternalResult(
        agent="openhands",
        status="complete" if status not in {"error", "failed"} else "failed",
        artifacts=["openhands-events.json", "openhands-conversation.json", "openhands-final-response.json"],
        details={
            "upstream_revision": OPENHANDS_SDK_REVISION,
            "conversation_id": conversation_id,
            "server_url": server_url,
            "server_version": health["version"],
            "execution_status": status,
            "event_count": len(events),
            "model": profile.model,
            "api_base_url": profile.base_url,
            "api_key_persisted_by_dashboard": False,
        },
    )
    _write_result(output_dir, result)
    if result.status != "complete":
        raise UpstreamAgentError(f"OpenHands conversation finished with status {status}.")
    log("OpenHands: complete; conversation event stream and final response are available in this run.")
    return result


def _all_events(server_url: str, server_api_key: str, conversation_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    page_id: str | None = None
    for _ in range(20):
        suffix = f"{OPENHANDS_API_PREFIX}/conversations/{conversation_id}/events/search?limit=100"
        if page_id:
            suffix += f"&page_id={page_id}"
        page = _request_json("GET", _join(server_url, suffix), server_api_key)
        items = page.get("items", []) if isinstance(page, dict) else []
        events.extend(item for item in items if isinstance(item, dict))
        page_id = page.get("next_page_id") if isinstance(page, dict) else None
        if not page_id:
            break
    return _strip_secrets(events)


def _request_json(method: str, url: str, api_key: str, payload: dict[str, Any] | None = None, *, timeout: float = 30) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if api_key:
        headers["X-Session-API-Key"] = api_key
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 -- URL validated at dashboard boundary
            value = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:600]
        raise UpstreamAgentError(f"OpenHands {method} {url} returned {exc.code}: {body}") from exc
    except URLError as exc:
        raise UpstreamAgentError(f"OpenHands endpoint is unreachable: {exc.reason}") from exc
    if not isinstance(value, dict):
        raise UpstreamAgentError(f"OpenHands {method} {url} returned a non-object response.")
    return value


def _write_result(directory: Path, result: ExternalResult) -> None:
    (directory / "external-result.json").write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _join(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _redact(value: str, secret: str) -> str:
    return value.replace(secret, "[redacted]") if secret else value


def _strip_secrets(value: Any) -> Any:
    """Avoid accidental dashboard persistence if a future server echoes secrets."""
    secret_names = {"api_key", "authorization", "token", "secret", "secrets"}
    if isinstance(value, dict):
        return {key: "[redacted]" if key.lower() in secret_names else _strip_secrets(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_strip_secrets(item) for item in value]
    return value
