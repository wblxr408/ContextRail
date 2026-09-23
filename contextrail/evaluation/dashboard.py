"""Loopback-only dashboard for ContextRail and upstream-agent evaluations."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from typing import Any
from urllib.parse import parse_qs, urlparse

from .agent import MinimalApiAgent, run_stress_suite
from .agent_runtime import LocalServerError, OpenHandsServerRuntime
from .external_agents import (
    ExternalModelProfile,
    openhands_health,
    run_openhands,
    run_sweagent,
    sweagent_executable,
)
from .models import OpenAICompatibleModel
from .switching import ModelProfile, SwitchingApiAgent, TRAJECTORIES, run_switch_suite
from .tasks import STRESS_TASKS


AGENT_CATALOG = (
    {
        "id": "minimal",
        "complexity": "L1 · 可控基线",
        "name": "ContextRail Minimal API Agent",
        "summary": "唯一用于 A/B/C 因果对照；直接观察 Packet、冷页和工具回合。",
        "source": "本项目 contextrail/evaluation/agent.py",
        "url": "",
        "action": "运行 12 条压力集",
        "available": True,
    },
    {
        "id": "swe-agent",
        "complexity": "L2 · 完整修复 Agent",
        "name": "SWE-agent",
        "summary": "委托官方 CLI 的仓库修复、沙箱、轨迹和补丁提交流程。",
        "source": "SWE-agent/SWE-agent",
        "url": "https://github.com/SWE-agent/SWE-agent",
        "action": "运行官方 SWE-agent",
        "available": False,
    },
    {
        "id": "openhands",
        "complexity": "L3 · Agent Server",
        "name": "OpenHands Agent Server",
        "summary": "委托官方 Agent Server 的对话、工具、workspace 与事件流。",
        "source": "OpenHands/software-agent-sdk",
        "url": "https://github.com/OpenHands/software-agent-sdk",
        "action": "连接 OpenHands Agent Server",
        "available": False,
    },
)


@dataclass
class Job:
    id: str
    agent: str
    status: str
    output: str | None = None
    error: str | None = None
    log: list[str] | None = None


class DashboardState:
    def __init__(self, runs_root: Path):
        self.runs_root = runs_root.resolve()
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.openhands_server = OpenHandsServerRuntime()

    def catalog(self) -> list[dict[str, Any]]:
        rows = [dict(item) for item in AGENT_CATALOG]
        for row in rows:
            if row["id"] == "swe-agent":
                row["available"] = sweagent_executable() is not None
            if row["id"] == "openhands":
                row["available"] = self.openhands_server.status().installable
        return rows

    def list_runs(self) -> list[dict[str, str]]:
        runs = []
        for directory in self.runs_root.iterdir():
            if not directory.is_dir():
                continue
            if (directory / "metrics.csv").is_file():
                runs.append({"id": directory.name, "label": f"{directory.name} · A/B/C", "kind": "context"})
            if (directory / "switch-metrics.csv").is_file():
                runs.append({"id": directory.name, "label": f"{directory.name} · SWITCH", "kind": "switch"})
            if (directory / "external-result.json").is_file():
                result = json.loads((directory / "external-result.json").read_text(encoding="utf-8"))
                runs.append({"id": directory.name, "label": f"{directory.name} · {result.get('agent', 'EXTERNAL').upper()}", "kind": "external"})
        return sorted(runs, key=lambda item: item["id"], reverse=True)

    def read_run(self, identifier: str) -> dict[str, Any]:
        directory = self._run_dir(identifier)
        metrics = directory / "metrics.csv"
        switch_metrics = directory / "switch-metrics.csv"
        external = directory / "external-result.json"
        if external.is_file():
            result = json.loads(external.read_text(encoding="utf-8"))
            trace_name = "openhands-events.json" if (directory / "openhands-events.json").is_file() else "sweagent.stdout.log"
            trace = (directory / trace_name).read_text(encoding="utf-8") if trace_name.endswith(".log") else json.loads((directory / trace_name).read_text(encoding="utf-8"))
            return {"id": identifier, "kind": "external", "metrics": [], "tasks": [], "report": "", "result": result, "trace": trace}
        if switch_metrics.is_file():
            with switch_metrics.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            return {"id": identifier, "kind": "switch", "metrics": rows, "tasks": [],
                    "report": self._read_report(directory, "switch-report.md")}
        if not metrics.is_file():
            raise FileNotFoundError(identifier)
        with metrics.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        tasks: dict[str, dict[str, Any]] = {}
        for row in rows:
            task_id = row["task_id"]
            strategy = row["strategy"]
            tasks.setdefault(task_id, {"task_id": task_id, "strategies": {}})["strategies"][strategy] = row
        return {"id": identifier, "kind": "context", "metrics": rows, "tasks": list(tasks.values()), "report": self._read_report(directory, "report.md")}

    def read_trace(self, identifier: str, task_id: str, strategy: str) -> dict[str, Any]:
        directory = self._run_dir(identifier)
        if strategy not in {"A", "B", "C"} or not task_id.startswith("X"):
            raise ValueError("Invalid trace selector.")
        trace_path = directory / "tasks" / task_id / strategy / "trace.json"
        if not trace_path.is_file():
            raise FileNotFoundError(str(trace_path))
        return json.loads(trace_path.read_text(encoding="utf-8"))

    def read_switch_trace(self, identifier: str, task_id: str, trajectory: str) -> dict[str, Any]:
        if trajectory not in TRAJECTORIES or not task_id.startswith("X"):
            raise ValueError("Invalid switch trace selector.")
        trace_path = self._run_dir(identifier) / "switches" / trajectory.replace("->", "-") / task_id / "trace.json"
        if not trace_path.is_file():
            raise FileNotFoundError(str(trace_path))
        return json.loads(trace_path.read_text(encoding="utf-8"))

    def submit(self, payload: dict[str, Any]) -> Job:
        agent = payload.get("agent")
        if agent not in {item["id"] for item in AGENT_CATALOG}:
            raise ValueError("Unknown agent selection.")
        self._preflight_external(agent, payload)
        job_id = datetime.now(UTC).strftime("run-%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
        with self.lock:
            job = Job(job_id, agent, "queued", log=[])
            self.jobs[job_id] = job
        if agent == "minimal" and payload.get("mode", "context") == "switch":
            thread = threading.Thread(target=self._run_switch, args=(job_id, payload), daemon=True)
        elif agent == "minimal":
            thread = threading.Thread(target=self._run_minimal, args=(job_id, payload), daemon=True)
        else:
            thread = threading.Thread(target=self._run_external, args=(job_id, payload), daemon=True)
        thread.start()
        return job

    def _preflight_external(self, agent: Any, payload: dict[str, Any]) -> None:
        """Reject avoidable L2/L3 failures before creating an asynchronous job."""
        if agent == "minimal":
            return
        if payload.get("mode", "context") != "context":
            raise ValueError(
                "L2/L3 use their upstream single-model loop; select L1 ContextRail for switch trajectories."
            )
        self._text(payload, "base_url", 500)
        self._text(payload, "api_key", 500)
        self._text(payload, "model", 200)
        if agent == "swe-agent":
            if sweagent_executable() is None:
                raise ValueError("SWE-agent is not installed or is not available on PATH.")
            repository_url = self._text(payload, "repository_url", 500)
            issue_url = self._text(payload, "issue_url", 500)
            if not repository_url.startswith("https://github.com/"):
                raise ValueError("SWE-agent repository must be a https://github.com/ URL.")
            if not issue_url.startswith(repository_url.rstrip("/") + "/issues/"):
                raise ValueError("SWE-agent issue URL must belong to the selected repository.")
            cost_limit = float(payload.get("cost_limit", 2))
            if not 0.01 <= cost_limit <= 100:
                raise ValueError("SWE-agent per-instance cost limit must be between 0.01 and 100 USD.")
            return
        if agent != "openhands":
            raise ValueError("Unknown external agent.")

        server_url = self._text(payload, "openhands_server_url", 500)
        server_key = self._optional_text(payload, "openhands_server_api_key", 500)
        workspace = self._text(payload, "openhands_workspace", 500)
        task = self._text(payload, "openhands_task", 12_000)
        iterations = int(payload.get("openhands_max_iterations", 30))
        runtime = int(payload.get("openhands_max_runtime_seconds", 600))
        if not 1 <= iterations <= 500:
            raise ValueError("OpenHands max iterations must be 1 to 500.")
        if not 10 <= runtime <= 3600:
            raise ValueError("OpenHands runtime limit must be 10 to 3600 seconds.")
        # The managed local server resolves relative workspaces from the project
        # root. Check that path now so a queued job cannot fail on a typo.
        if server_url.rstrip("/") == self.openhands_server.status().url.rstrip("/"):
            status = self.openhands_server.status()
            if status.state != "ready":
                raise ValueError(f"OpenHands Agent Server is not READY (current state: {status.state}).")
            workspace_path = Path(workspace)
            if not workspace_path.is_absolute():
                workspace_path = self.openhands_server.project_root / workspace_path
            if not workspace_path.is_dir():
                raise ValueError(f"OpenHands workspace does not exist or is not a directory: {workspace}")
        health = openhands_health(server_url=server_url, server_api_key=server_key)
        if not health.get("version"):
            raise ValueError("OpenHands Agent Server did not report a version.")

    def get_job(self, identifier: str) -> Job:
        with self.lock:
            if identifier not in self.jobs:
                raise FileNotFoundError(identifier)
            return self.jobs[identifier]

    def local_openhands_status(self) -> dict[str, Any]:
        return asdict(self.openhands_server.status())

    def start_local_openhands(self, payload: dict[str, Any]) -> dict[str, Any]:
        port = int(payload.get("port", 8000))
        return asdict(self.openhands_server.start(port=port))

    def stop_local_openhands(self) -> dict[str, Any]:
        return asdict(self.openhands_server.stop())

    def _run_minimal(self, job_id: str, payload: dict[str, Any]) -> None:
        try:
            base_url = self._text(payload, "base_url", 500)
            api_key = self._text(payload, "api_key", 500)
            model = self._text(payload, "model", 200)
            budget = int(payload.get("budget", 4000))
            if not (base_url.startswith("https://") or base_url.startswith("http://")):
                raise ValueError("API URL must start with http:// or https://.")
            if not 256 <= budget <= 200_000:
                raise ValueError("Context budget must be between 256 and 200000.")
            output = self.runs_root / job_id
            agent = MinimalApiAgent(OpenAICompatibleModel(base_url=base_url, api_key=api_key, model=model),
                                    context_budget=budget)
            with self.lock:
                self.jobs[job_id].status = "running"
            run_stress_suite(agent, STRESS_TASKS, output)
            with self.lock:
                self.jobs[job_id].status = "complete"
                self.jobs[job_id].output = job_id
        except Exception as exc:  # Surface only a concise failure; never include submitted API key.
            with self.lock:
                self.jobs[job_id].status = "failed"
                self.jobs[job_id].error = self._redact(str(exc), payload)

    def _run_switch(self, job_id: str, payload: dict[str, Any]) -> None:
        try:
            profiles = self._profiles(payload)
            trajectory = str(payload.get("trajectory", "A->B"))
            if trajectory not in TRAJECTORIES:
                raise ValueError("Unsupported switch trajectory.")
            budget = int(payload.get("budget", 4000))
            output = self.runs_root / job_id
            with self.lock:
                self.jobs[job_id].status = "running"
            agent = SwitchingApiAgent(profiles, context_budget=budget)
            run_switch_suite(agent, STRESS_TASKS, output, trajectories=(trajectory,))
            with self.lock:
                self.jobs[job_id].status = "complete"
                self.jobs[job_id].output = job_id
        except Exception as exc:
            with self.lock:
                self.jobs[job_id].status = "failed"
                self.jobs[job_id].error = self._redact(str(exc), payload)

    def _run_external(self, job_id: str, payload: dict[str, Any]) -> None:
        try:
            agent = str(payload["agent"])
            if payload.get("mode", "context") != "context":
                raise ValueError("L2/L3 run their upstream loops as a single-model baseline; use L1 switch mode to measure ContextRail handoff correctness.")
            profile = ExternalModelProfile(
                base_url=self._text(payload, "base_url", 500),
                api_key=self._text(payload, "api_key", 500),
                model=self._text(payload, "model", 200),
            )
            output = self.runs_root / job_id
            with self.lock:
                self.jobs[job_id].status = "running"
            logger = lambda line: self._append_log(job_id, self._redact(line, payload))
            if agent == "swe-agent":
                run_sweagent(
                    profile=profile,
                    repository_url=self._text(payload, "repository_url", 500),
                    issue_url=self._text(payload, "issue_url", 500),
                    output_dir=output,
                    cost_limit=float(payload.get("cost_limit", 2)),
                    log=logger,
                )
            elif agent == "openhands":
                run_openhands(
                    profile=profile,
                    server_url=self._text(payload, "openhands_server_url", 500),
                    server_api_key=self._optional_text(payload, "openhands_server_api_key", 500),
                    workspace=self._text(payload, "openhands_workspace", 500),
                    task=self._text(payload, "openhands_task", 12_000),
                    output_dir=output,
                    max_iterations=int(payload.get("openhands_max_iterations", 30)),
                    max_runtime_seconds=int(payload.get("openhands_max_runtime_seconds", 600)),
                    log=logger,
                )
            else:
                raise ValueError("Unknown external agent.")
            with self.lock:
                self.jobs[job_id].status = "complete"
                self.jobs[job_id].output = job_id
        except Exception as exc:
            with self.lock:
                self.jobs[job_id].status = "failed"
                self.jobs[job_id].error = self._redact(str(exc), payload)

    def _append_log(self, job_id: str, message: str) -> None:
        with self.lock:
            job = self.jobs[job_id]
            lines = job.log if job.log is not None else []
            lines.append(message[:800])
            job.log = lines[-80:]

    def _run_dir(self, identifier: str) -> Path:
        if not identifier or Path(identifier).name != identifier:
            raise ValueError("Invalid run identifier.")
        candidate = (self.runs_root / identifier).resolve()
        if candidate.parent != self.runs_root:
            raise ValueError("Run selection escapes the configured results root.")
        return candidate

    @staticmethod
    def _read_report(directory: Path, name: str) -> str:
        report = directory / name
        return report.read_text(encoding="utf-8") if report.is_file() else ""

    def _profiles(self, payload: dict[str, Any]) -> dict[str, ModelProfile]:
        values = payload.get("profiles")
        if not isinstance(values, list):
            raise ValueError("profiles are required for a switch experiment.")
        profiles: dict[str, ModelProfile] = {}
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("Each profile must be an object.")
            profile_id = self._text(value, "id", 1)
            if profile_id not in {"A", "B", "C"} or profile_id in profiles:
                raise ValueError("Profile IDs must be unique A, B, or C.")
            base_url, api_key, model = (self._text(value, key, limit) for key, limit in
                                       (("base_url", 500), ("api_key", 500), ("model", 200)))
            profiles[profile_id] = ModelProfile(profile_id, model, OpenAICompatibleModel(base_url=base_url, api_key=api_key, model=model))
        if not {"A", "B"}.issubset(profiles):
            raise ValueError("Profiles A and B are required.")
        return profiles

    @staticmethod
    def _redact(message: str, payload: dict[str, Any]) -> str:
        values = [payload.get("api_key", "")]
        values.extend(value.get("api_key", "") for value in payload.get("profiles", []) if isinstance(value, dict))
        values.append(payload.get("openhands_server_api_key", ""))
        for value in values:
            if isinstance(value, str) and value:
                message = message.replace(value, "[redacted]")
        return message

    @staticmethod
    def _text(payload: dict[str, Any], key: str, limit: int) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"{key} is required.")
        return value.strip()

    @staticmethod
    def _optional_text(payload: dict[str, Any], key: str, limit: int) -> str:
        value = payload.get(key, "")
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f"{key} must be a string.")
        return value.strip()


class DashboardHandler(BaseHTTPRequestHandler):
    state: DashboardState

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                return self._send(HTTPStatus.OK, "text/html; charset=utf-8", _html().encode("utf-8"))
            if parsed.path == "/api/state":
                return self._json({"catalog": self.state.catalog(), "runs": self.state.list_runs()})
            if parsed.path == "/api/run":
                return self._json(self.state.read_run(self._query(parsed, "id")))
            if parsed.path == "/api/trace":
                return self._json(self.state.read_trace(self._query(parsed, "id"), self._query(parsed, "task"),
                                                        self._query(parsed, "strategy")))
            if parsed.path == "/api/switch-trace":
                return self._json(self.state.read_switch_trace(self._query(parsed, "id"), self._query(parsed, "task"),
                                                               self._query(parsed, "trajectory")))
            if parsed.path == "/api/job":
                return self._json(asdict(self.state.get_job(self._query(parsed, "id"))))
            if parsed.path == "/api/openhands-server":
                return self._json(self.state.local_openhands_status())
            self._error(HTTPStatus.NOT_FOUND, "Route not found.")
        except (FileNotFoundError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 20_000:
                raise ValueError("Invalid request length.")
            payload = json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(payload, dict):
                raise ValueError("Configuration must be a JSON object.")
            if parsed.path == "/api/jobs":
                return self._json(asdict(self.state.submit(payload)), status=HTTPStatus.ACCEPTED)
            if parsed.path == "/api/openhands-server/start":
                return self._json(self.state.start_local_openhands(payload), status=HTTPStatus.ACCEPTED)
            if parsed.path == "/api/openhands-server/stop":
                return self._json(self.state.stop_local_openhands())
            return self._error(HTTPStatus.NOT_FOUND, "Route not found.")
        except (LocalServerError, ValueError, json.JSONDecodeError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))

    def log_message(self, *_: object) -> None:
        return

    def _query(self, parsed, name: str) -> str:
        value = parse_qs(parsed.query).get(name, [""])[0]
        return value

    def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send(status, "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"error": message}, status)

    def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def serve_dashboard(*, host: str = "127.0.0.1", port: int = 0, runs_root: Path = Path("evaluation-runs")) -> None:
    """Serve the dashboard on loopback; port 0 asks Windows for a free port."""
    state = DashboardState(runs_root)
    handler = type("ContextRailDashboardHandler", (DashboardHandler,), {"state": state})
    with ThreadingHTTPServer((host, port), handler) as server:
        bound_host, bound_port = server.server_address[:2]
        print(f"ContextRail dashboard: http://{bound_host}:{bound_port}", flush=True)
        server.serve_forever()


def _html() -> str:
    return (Path(__file__).with_name("dashboard.html")).read_text(encoding="utf-8")
