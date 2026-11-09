"""Minimal model adapters used by the evaluation host.

Only the OpenAI-compatible Chat Completions wire format is implemented here.
It covers OpenAI-compatible hosted and local endpoints while keeping the
evaluation harness independent of a desktop coding-agent host.
"""

from __future__ import annotations

from dataclasses import dataclass
from http.client import HTTPException
import json
import os
import re
import time
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from ..errors import InvalidRequest
from ..models import digest, identifier, integer


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

    @classmethod
    def total(cls, observations: list[ModelUsage]) -> ModelUsage:
        def total(field: str) -> int | None:
            values = [getattr(item, field) for item in observations]
            return sum(values) if values and all(value is not None for value in values) else None
        return cls(*(total(field) for field in
                     ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_tokens")))


@dataclass(frozen=True)
class ModelReply:
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    usage: ModelUsage = ModelUsage()
    request_id: str | None = None
    latency_ms: int | None = None
    ttft_ms: int | None = None
    timestamp: float | None = None
    retries: int | None = None
    request_digest: str | None = None


class ModelRequestError(RuntimeError):
    """A failed dispatch with the safe measurements the client observed."""

    def __init__(self, code: str, observation: ModelReply):
        super().__init__(f"Model API request failed ({code}).")
        self.code = code
        self.observation = observation


class ModelClient(Protocol):
    def complete(self, messages: list[dict], tools: list[dict]) -> ModelReply:
        """Return one assistant turn for standard chat messages and tools."""


class OpenAICompatibleModel:
    """Configurable standard-library client for `/chat/completions` endpoints."""

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_seconds: int = 120,
                 model_revision: str | None = None, provider: str | None = None, stream: bool = False):
        if not base_url.strip() or not api_key.strip() or not model.strip():
            raise ValueError("base_url, api_key and model are required for an API evaluation run.")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.provider = provider or urlsplit(self.base_url).hostname or "openai-compatible"
        identifier(self.provider)
        self.stream = stream
        self.model_revision = model_revision.strip() if isinstance(model_revision, str) and model_revision.strip() else None
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls) -> "OpenAICompatibleModel":
        """Load credentials without writing them to the workspace or trace files."""
        base_url = os.environ.get("CONTEXT_RAIL_EVAL_API_BASE_URL", "").strip()
        api_key = os.environ.get("CONTEXT_RAIL_EVAL_API_KEY", "").strip()
        model = os.environ.get("CONTEXT_RAIL_EVAL_MODEL", "").strip()
        return cls(base_url=base_url, api_key=api_key, model=model,
                   model_revision=os.environ.get("CONTEXT_RAIL_EVAL_MODEL_REVISION"),
                   provider=os.environ.get("CONTEXT_RAIL_EVAL_PROVIDER"),
                   stream=os.environ.get("CONTEXT_RAIL_EVAL_STREAM", "1") != "0")

    @property
    def endpoint(self) -> str:
        suffix = "/chat/completions"
        return self.base_url if self.base_url.endswith(suffix) else self.base_url + suffix

    def complete(self, messages: list[dict], tools: list[dict]) -> ModelReply:
        started = time.perf_counter()
        timestamp = time.time()
        wire_tools, wire_to_dotted = self._wire_tools(tools)
        wire_messages = self._wire_messages(messages, wire_to_dotted)
        payload = {"model": self.model, "messages": wire_messages, "temperature": 0}
        # A tool-free turn (e.g. the forced final answer) sends no tools; some
        # providers reject an empty ``tools`` array, so omit the key entirely.
        if wire_tools:
            payload["tools"] = wire_tools
        if self.stream:
            payload.update(stream=True, stream_options={"include_usage": True})
        raw_request = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_digest = digest(raw_request)
        request = Request(
            self.endpoint,
            data=raw_request,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        request_id = None
        ttft_ms = None
        usage = ModelUsage()
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                request_id = response.headers.get("x-request-id")
                if response.headers.get_content_type() == "text/event-stream":
                    body, ttft_ms, complete = self._read_stream(response, started)
                else:
                    body = json.loads(response.read())
                    complete = True
                request_id = request_id or body.get("id")
                usage = self._usage(body.get("usage"))
                if not complete:
                    raise ValueError("Stream ended before a completion marker.")
            message = body["choices"][0]["message"]
            calls = tuple(self._tool_call(call, wire_to_dotted) for call in message.get("tool_calls") or ())
        except HTTPError as exc:
            request_id = exc.headers.get("x-request-id")
            exc.close()
            code = f"http_{exc.code}"
        except (URLError, OSError, HTTPException):
            code = "transport_error"
        except (ValueError, IndexError, KeyError, TypeError, AttributeError, InvalidRequest):
            code = "invalid_response"
        else:
            code = None
        latency_ms = round((time.perf_counter() - started) * 1000)
        metadata = dict(usage=usage, request_id=str(request_id) if request_id is not None else None,
                        latency_ms=latency_ms, ttft_ms=ttft_ms, timestamp=timestamp, retries=0,
                        request_digest=request_digest)
        if code is not None:
            raise ModelRequestError(code, ModelReply("", **metadata))
        return ModelReply(message.get("content") or "", calls, **metadata)

    def tokenizer_input(self, messages: list[dict], tools: list[dict]) -> tuple[list[dict], list[dict]]:
        wire_tools, names = self._wire_tools(tools)
        return self._wire_messages(messages, names), wire_tools

    @staticmethod
    def _usage(usage: dict | None) -> ModelUsage:
        usage = usage or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        counts = (usage.get("prompt_tokens"), usage.get("completion_tokens"),
                  prompt_details.get("cached_tokens"), prompt_details.get("cache_write_tokens"))
        for count in counts:
            if count is not None:
                integer(count)
        if counts[0] is not None and counts[2] is not None and counts[2] > counts[0]:
            raise ValueError("Cached input exceeds provider-reported input.")
        return ModelUsage(*counts)

    @staticmethod
    def _tool_call(call: dict, names: dict[str, str]) -> ToolCall:
        function = call["function"]
        arguments = json.loads(function.get("arguments") or "{}")
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object.")
        wire = function["name"]
        return ToolCall(call["id"], names.get(wire, wire), arguments)

    @staticmethod
    def _read_stream(response, started: float) -> tuple[dict, int | None, bool]:
        parts = []
        calls: dict[int, dict] = {}
        usage = request_id = ttft_ms = None
        finish = done = failed = False
        event = []
        for line in response:
            line = line.decode("utf-8").rstrip("\r\n")
            if line.startswith("data:"):
                event.append(line[5:].lstrip(" "))
            if line or not event:
                continue
            data = "\n".join(event)
            event.clear()
            if data == "[DONE]":
                done = True
                break
            chunk = json.loads(data)
            if chunk.get("error"):
                failed = True
                break
            request_id = request_id or chunk.get("id")
            if chunk.get("usage") is not None:
                usage = chunk["usage"]
            for choice in chunk.get("choices", []):
                if choice.get("index", 0) != 0:
                    continue
                delta = choice.get("delta") or {}
                content = delta.get("content") or ""
                tools = delta.get("tool_calls") or []
                generated = content or any(any((tool.get("function") or {}).values()) for tool in tools)
                if generated and ttft_ms is None:
                    ttft_ms = round((time.perf_counter() - started) * 1000)
                parts.append(content)
                for tool in tools:
                    call = calls.setdefault(tool["index"], {"id": "", "function": {"name": "", "arguments": ""}})
                    call["id"] += tool.get("id") or ""
                    function = tool.get("function") or {}
                    for field in ("name", "arguments"):
                        call["function"][field] += function.get(field) or ""
                finish = finish or choice.get("finish_reason") is not None
        message = {"content": "".join(parts), "tool_calls": [calls[index] for index in sorted(calls)]}
        return {"id": request_id, "usage": usage, "choices": [{"message": message}]}, ttft_ms, not failed and (done or finish)

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
    provider = "deterministic-fixture"

    def __init__(self, tasks: tuple[object, ...]):
        self.tasks = {task.id: task for task in tasks}
        self.sequence = 0
        self.request_prefix = f"fixture-{uuid4().hex}"

    def complete(self, messages: list[dict], tools: list[dict]) -> ModelReply:
        self.sequence += 1
        request_id = f"{self.request_prefix}-{self.sequence}"
        joined = "\n".join(str(message.get("content", "")) for message in messages)
        task_id, strategy = self._task_identity(messages)
        task = self.tasks[task_id]
        user = json.loads(messages[1]["content"])
        if user.get("phase") == "summary":
            evidence = json.loads(user["context"])["evidence"]
            summary = "\n".join(item["text"].splitlines()[0] for item in evidence
                                if item["current"] and item["text"].startswith("AUTHORITATIVE FACT:"))
            return ModelReply(summary, usage=ModelUsage(max(1, len(joined) // 4), max(1, len(summary) // 4), 0),
                              request_id=request_id, latency_ms=1)
        missing = [artifact for artifact in task.fact_artifacts if artifact.fact not in joined]
        if strategy == "C" and missing and tools:
            calls = tuple(ToolCall(f"fixture-{self.sequence}-{index}", "context.get",
                                   {"name": artifact.name, "revision": artifact.revision})
                          for index, artifact in enumerate(missing, start=1))
            return ModelReply("", calls, ModelUsage(max(1, len(joined) // 4), 1, 0),
                              request_id, 1)
        answer = "\n".join(task.expected_facts) if not missing else "INSUFFICIENT_EVIDENCE"
        return ModelReply(answer, (),
                          ModelUsage(max(1, len(joined) // 4), max(1, len(answer) // 4), 0),
                          request_id, 1)

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
