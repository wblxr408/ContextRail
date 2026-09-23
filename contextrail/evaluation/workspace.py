"""Workspace tools for a minimal API coding agent.

Use these only inside a disposable task directory or benchmark container.  The
class confines paths to that root; command isolation is provided by the caller's
Docker/SWE-bench environment rather than by ContextRail.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class WorkspaceTools:
    def __init__(self, root: Path, *, command_timeout_seconds: int = 120, max_read_chars: int = 65536):
        self.root = root.resolve()
        self.command_timeout_seconds = command_timeout_seconds
        self.max_read_chars = max_read_chars
        if not self.root.is_dir():
            raise ValueError("Workspace root must be an existing directory.")

    def call(self, name: str, arguments: dict) -> dict:
        if name == "workspace.read_file":
            self._keys(arguments, {"path"})
            path = self._path(arguments["path"])
            content = path.read_text(encoding="utf-8")
            if len(content) > self.max_read_chars:
                return {"path": arguments["path"], "content": content[:self.max_read_chars], "truncated": True}
            return {"path": arguments["path"], "content": content, "truncated": False}
        if name == "workspace.write_file":
            self._keys(arguments, {"path", "content"})
            if not isinstance(arguments["content"], str):
                raise ValueError("workspace.write_file content must be text.")
            path = self._path(arguments["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(arguments["content"], encoding="utf-8")
            return {"path": arguments["path"], "bytes": len(arguments["content"].encode("utf-8"))}
        if name == "workspace.run":
            self._keys(arguments, {"command"})
            if not isinstance(arguments["command"], str) or not arguments["command"].strip():
                raise ValueError("workspace.run command must be nonempty text.")
            completed = subprocess.run(arguments["command"], cwd=self.root, shell=True, text=True,
                                       capture_output=True, timeout=self.command_timeout_seconds, check=False)
            return {"exit_code": completed.returncode, "stdout": completed.stdout[-self.max_read_chars:],
                    "stderr": completed.stderr[-self.max_read_chars:]}
        raise ValueError(f"Unknown workspace tool: {name}")

    def _path(self, relative: object) -> Path:
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise ValueError("Workspace path must be a nonempty relative path.")
        candidate = (self.root / relative).resolve()
        if self.root not in candidate.parents and candidate != self.root:
            raise ValueError("Workspace path escapes the task root.")
        return candidate

    @staticmethod
    def _keys(arguments: dict, expected: set[str]) -> None:
        if not isinstance(arguments, dict) or set(arguments) != expected:
            raise ValueError("Unsupported workspace tool arguments.")

    @staticmethod
    def definitions() -> list[dict]:
        return [
            {"name": "workspace.read_file", "description": "Read a UTF-8 file within the disposable task workspace.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}},
                              "required": ["path"], "additionalProperties": False}},
            {"name": "workspace.write_file", "description": "Write a UTF-8 file within the disposable task workspace.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                              "required": ["path", "content"], "additionalProperties": False}},
            {"name": "workspace.run", "description": "Run a command inside the disposable benchmark workspace.",
             "input_schema": {"type": "object", "properties": {"command": {"type": "string"}},
                              "required": ["command"], "additionalProperties": False}},
        ]
