"""Local OpenAI-compatible fixture server for end-to-end API-host smoke tests."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from typing import Type

from .models import DeterministicEvidenceModel
from .tasks import STRESS_TASKS


def fixture_handler(model: DeterministicEvidenceModel) -> Type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            if self.path.rstrip("/") != "/v1/chat/completions":
                self.send_error(404)
                return
            try:
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                reply = model.complete(payload.get("messages") or [], payload.get("tools") or [])
                response = {
                    "id": reply.request_id,
                    "object": "chat.completion",
                    "created": 0,
                    "model": payload.get("model", "fixture-model"),
                    "choices": [{"index": 0, "finish_reason": "tool_calls" if reply.tool_calls else "stop",
                                 "message": {"role": "assistant", "content": reply.content, "tool_calls": [
                                     {"id": call.id, "type": "function", "function": {"name": call.name,
                                      "arguments": json.dumps(call.arguments, ensure_ascii=False)}}
                                     for call in reply.tool_calls]}}],
                    "usage": {"prompt_tokens": reply.usage.input_tokens, "completion_tokens": reply.usage.output_tokens,
                              "total_tokens": reply.usage.input_tokens + reply.usage.output_tokens,
                              "prompt_tokens_details": {"cached_tokens": reply.usage.cached_input_tokens}},
                }
            except Exception as exc:
                self.send_error(400, str(exc))
                return
            encoded = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_):
            return

    return Handler


def serve_fixture_api(host: str = "127.0.0.1", port: int = 8787) -> None:
    with ThreadingHTTPServer((host, port), fixture_handler(DeterministicEvidenceModel(STRESS_TASKS))) as server:
        server.serve_forever()
