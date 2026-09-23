"""Lifecycle manager for a local, official OpenHands Agent Server process."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import threading
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


class LocalServerError(RuntimeError):
    """A recoverable failure while starting or stopping the local L3 service."""


@dataclass(frozen=True)
class ServerStatus:
    state: str
    url: str
    installable: bool
    pid: int | None
    log: list[str]


class OpenHandsServerRuntime:
    """Start the upstream module without reimplementing its server lifecycle."""

    def __init__(self, project_root: Path | None = None):
        self.project_root = (project_root or Path.cwd()).resolve()
        self._process: subprocess.Popen[str] | None = None
        self._url = "http://127.0.0.1:8000"
        self._log: deque[str] = deque(maxlen=100)
        self._lock = threading.Lock()

    @property
    def python(self) -> Path:
        return self.project_root / ".openhands-venv" / "Scripts" / "python.exe"

    def status(self) -> ServerStatus:
        with self._lock:
            process = self._process
            if process is None:
                state = "stopped"
                pid = None
            elif process.poll() is None:
                state = "ready" if self._healthy(self._url) else "starting"
                pid = process.pid
            else:
                state = "failed"
                pid = process.pid
            return ServerStatus(state, self._url, self.python.is_file(), pid, list(self._log))

    def start(self, *, port: int) -> ServerStatus:
        if not 1024 <= port <= 65535:
            raise ValueError("OpenHands port must be between 1024 and 65535.")
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise LocalServerError("OpenHands Agent Server is already running.")
            if not self.python.is_file():
                raise LocalServerError(
                    "OpenHands is not installed in .openhands-venv yet. Finish the official-source installation first."
                )
            self._url = f"http://127.0.0.1:{port}"
            environment = os.environ.copy()
            environment.update({"OH_TELEMETRY_EXPORTER": "none", "DO_NOT_TRACK": "1"})
            startupinfo = None
            creationflags = 0
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                creationflags = subprocess.CREATE_NO_WINDOW
            self._log.clear()
            self._log.append("Starting official OpenHands Agent Server module.")
            self._process = subprocess.Popen(
                [str(self.python), "-m", "openhands.agent_server", "--host", "127.0.0.1", "--port", str(port)],
                cwd=self.project_root,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
            threading.Thread(target=self._capture_output, args=(self._process,), daemon=True).start()
        return self.status()

    def stop(self) -> ServerStatus:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                self._process = None
                return ServerStatus("stopped", self._url, self.python.is_file(), None, list(self._log))
            process.terminate()
            self._log.append("Stopping local OpenHands Agent Server.")
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        with self._lock:
            self._process = None
        return self.status()

    def _capture_output(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for raw in process.stdout:
            line = raw.strip()
            if line:
                with self._lock:
                    self._log.append(line[:1000])

    @staticmethod
    def _healthy(url: str) -> bool:
        try:
            with urlopen(f"{url}/openapi.json", timeout=0.4) as response:  # noqa: S310 -- loopback URL assembled above
                return response.status == 200
        except (URLError, TimeoutError, OSError):
            return False
