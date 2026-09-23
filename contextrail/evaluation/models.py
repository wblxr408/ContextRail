"""Minimal model adapters used by the evaluation host.

Only the OpenAI-compatible Chat Completions wire format is implemented here.
It covers OpenAI-compatible hosted and local endpoints while keeping the
evaluation harness independent of a desktop coding-agent host.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import time
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# OpenAI-compatible providers (DeepSeek, Moonshot, and OpenAI itself) require
# tool function names to match ^[a-zA-Z0-9_-]+$.  ContextRail's internal tool
# names use a dotted namespace (context.get, workspace.run).  We translate only
# at the wire boundary: dotted names are rewritten to a wire-safe form on the
# way out, and every name the provider echoes back is restored to its exact
# dotted form on the way in.  The agent's dispatch never sees the wire form.
_WIRE_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def _wire_safe_name(name: str) -> str:
    return _WIRE_UNSAFE.sub("__", name)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_tokens: int | None = None


@dataclass(frozen=True)
class ModelReply:
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    usage: ModelUsage = ModelUsage()
    request_id: str | None = None
    latency_ms: int = 0


class ModelClient(Protocol):
    def complete(self, messages: list[dict], tools: list[dict]) -> ModelReply:
        """Return one assistant turn for standard chat messages and tools."""


class OpenAICompatibleModel:
    """Configurable standard-library client for `/chat/completions` endpoints."""

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_seconds: int = 120,
                 model_revision: str | None = None):
        if not base_url.strip() or not api_key.strip() or not model.strip():
            raise ValueError("base_url, api_key and model are required for an API evaluation run.")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.model_revision = model_revision.strip() if isinstance(model_revision, str) and model_revision.strip() else None
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls) -> "OpenAICompatibleModel":
        """Load credentials without writing them to the workspace or trace files."""
        base_url = os.environ.get("CONTEXT_RAIL_EVAL_API_BASE_URL", "").strip()
        api_key = os.environ.get("CONTEXT_RAIL_EVAL_API_KEY", "").strip()
        model = os.environ.get("CONTEXT_RAIL_EVAL_MODEL", "").strip()
        return cls(base_url=base_url, api_key=api_key, model=model,
                   model_revision=os.environ.get("CONTEXT_RAIL_EVAL_MODEL_REVISION"))

    @property
    def endpoint(self) -> str:
        suffix = "/chat/completions"
        return self.base_url if self.base_url.endswith(suffix) else self.base_url + suffix

    def complete(self, messages: list[dict], tools: list[dict]) -> ModelReply:
        started = time.perf_counter()
        wire_tools, wire_to_dotted = self._wire_tools(tools)
        wire_messages = self._wire_messages(messages, wire_to_dotted)
        payload = {"model": self.model, "messages": wire_messages, "temperature": 0}
        # A tool-free turn (e.g. the forced final answer) sends no tools; some
        # providers reject an empty ``tools`` array, so omit the key entirely.
        if wire_tools:
            payload["tools"] = wire_tools
        request = Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                body = json.loads(raw)
                request_id = response.headers.get("x-request-id") or body.get("id")
        except HTTPError as exc:
            detail = exc.read(1024).decode("utf-8", errors="replace")
            raise RuntimeError(f"Model API returned HTTP {exc.code}: {detail}") from None
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"Model API request failed: {exc}") from None
        latency_ms = round((time.perf_counter() - started) * 1000)
        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Model API response has no assistant message.") from exc
        calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError as exc:
                raise RuntimeError("Model API returned invalid tool-call JSON.") from exc
            wire_name = str(function.get("name") or "")
            calls.append(ToolCall(str(call.get("id") or ""), wire_to_dotted.get(wire_name, wire_name), arguments))
        usage = body.get("usage") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        cache_write = prompt_details.get("cache_write_tokens")
        return ModelReply(
            content=message.get("content") or "",
            tool_calls=tuple(calls),
            usage=ModelUsage(usage.get("prompt_tokens"), usage.get("completion_tokens"),
                             prompt_details.get("cached_tokens"), cache_write),
            request_id=str(request_id) if request_id is not None else None,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _wire_tools(tools: list[dict]) -> tuple[list[dict], dict[str, str]]:
        """Rewrite dotted tool names to a provider-safe form.

        Returns the outbound tool list plus a wire-name -> dotted-name map so
        the response's tool calls can be restored exactly.  A collision between
        two dotted names is a fatal configuration error, not something to paper
        over silently.
        """
        wire_tools: list[dict] = []
        wire_to_dotted: dict[str, str] = {}
        for tool in tools:
            function = tool.get("function") or {}
            dotted = str(function.get("name") or "")
            wire = _wire_safe_name(dotted)
            existing = wire_to_dotted.get(wire)
            if existing is not None and existing != dotted:
                raise RuntimeError(
                    f"Tool names {existing!r} and {dotted!r} collide after wire-safe rewrite to {wire!r}.")
            wire_to_dotted[wire] = dotted
            if wire == dotted:
                wire_tools.append(tool)
            else:
                wire_tools.append({**tool, "function": {**function, "name": wire}})
        return wire_tools, wire_to_dotted

    @staticmethod
    def _wire_messages(messages: list[dict], wire_to_dotted: dict[str, str]) -> list[dict]:
        """Rewrite dotted names inside prior assistant tool_calls to wire form.

        Message history carries the assistant's own earlier tool calls, whose
        function names must match the provider's name regex on resend.  Any name
        appearing in a ``tool_calls`` entry is, by definition, a tool name, so it
        is rewritten to its wire-safe form UNCONDITIONALLY — not gated on the
        current turn's tool set.  This matters on a tool-free turn (the forced
        final answer sends ``tools=[]``): the history still holds dotted names
        like ``context.get``, and without this rewrite they would reach the
        provider verbatim and trigger the exact HTTP 400 the wire layer exists to
        prevent.  ``wire_to_dotted`` still records the mapping so the response can
        be restored, but it is no longer required for history rewriting.
        """
        rewritten: list[dict] = []
        changed = False
        for message in messages:
            calls = message.get("tool_calls")
            if not calls:
                rewritten.append(message)
                continue
            new_calls = []
            for call in calls:
                function = call.get("function") or {}
                name = function.get("name")
                wire = _wire_safe_name(name) if isinstance(name, str) else name
                if wire != name:
                    changed = True
                    new_calls.append({**call, "function": {**function, "name": wire}})
                else:
                    new_calls.append(call)
            rewritten.append({**message, "tool_calls": new_calls})
        return rewritten if changed else messages


class DeterministicEvidenceModel:
    """Local API substitute used only to validate the evaluation plumbing.

    It never reads an oracle from the runner.  It succeeds only after the
    sentinel facts of the current task appear in prompt context or tool output.
    This validates A/B/C packet construction, tool turns, traces and scorers;
    it is not a claim about an LLM's task quality.
    """

    def __init__(self, tasks: tuple[object, ...]):
        self.tasks = {task.id: task for task in tasks}
        self.sequence = 0

    def complete(self, messages: list[dict], tools: list[dict]) -> ModelReply:
        self.sequence += 1
        joined = "\n".join(str(message.get("content", "")) for message in messages)
        task_id, strategy = self._task_identity(messages)
        task = self.tasks[task_id]
        missing = [artifact for artifact in task.fact_artifacts if artifact.fact not in joined]
        if strategy == "C" and missing and tools:
            calls = tuple(ToolCall(f"fixture-{self.sequence}-{index}", "context.get",
                                   {"name": artifact.name, "revision": artifact.revision})
                          for index, artifact in enumerate(missing, start=1))
            return ModelReply("", calls, ModelUsage(max(1, len(joined) // 4), 1, 0),
                              f"fixture-{self.sequence}", 1)
        answer = "\n".join(task.expected_facts) if not missing else "INSUFFICIENT_EVIDENCE"
        return ModelReply(answer, (),
                          ModelUsage(max(1, len(joined) // 4), max(1, len(answer) // 4), 0),
                          f"fixture-{self.sequence}", 1)

    @staticmethod
    def _task_identity(messages: list[dict]) -> tuple[str, str]:
        for message in messages:
            if message.get("role") != "user":
                continue
            try:
                value = json.loads(message.get("content") or "{}")
            except json.JSONDecodeError:
                continue
            if "task_id" in value and "strategy" in value:
                return value["task_id"], value["strategy"]
        raise RuntimeError("Fixture model could not find evaluation task metadata.")
