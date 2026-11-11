"""Verify actual Windows Claude Code Hook and MCP transport using a loopback Messages fixture."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import threading

from native_client_acceptance import fixture, prompt, run_client
from contextrail import Scope, Store
from contextrail.local_process import run_local
from contextrail.models import canonical


def messages_gateway(data, expected):
    results = {}
    calls = ("context_packet", "context_get")
    marker = "中文传输🙂"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, body, kind):
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            path = self.path.split("?")[0]
            if path == "/v1/messages/count_tokens":
                self.reply(b'{"input_tokens":42}', "application/json")
                return
            if path != "/v1/messages":
                self.send_error(404)
                return
            try:
                self.respond(request)
            except (AssertionError, KeyError, ValueError, StopIteration) as error:
                results["failure"] = str(error)
                self.send_error(422, "Unexpected Messages exchange in Windows fixture")

        def respond(self, request):
            blocks = [block for item in request["messages"] if isinstance(item.get("content"), list)
                      for block in item["content"]]
            values = {}
            for block in blocks:
                if block["type"] != "tool_result":
                    continue
                body = block["content"]
                text = body if isinstance(body, str) else next(part["text"] for part in body if part["type"] == "text")
                values[block["tool_use_id"]] = json.loads(text)
            results["prompt_unicode"] = marker in canonical(request["messages"])
            assert results["prompt_unicode"], "Claude Code did not forward the Unicode prompt"
            if "toolu_packet" in values:
                packet = values["toolu_packet"]
                results["packet_exact"] = (packet["snapshot"] == data["snapshot"]
                    and packet["context"]["task"]["objective"] == expected["objective"])
                assert results["packet_exact"], "Claude Code did not return the selected Rail packet"
            if "toolu_cold" in values:
                cold = values["toolu_cold"]
                results["cold_crlf_exact"] = cold["content"] == expected["cold_tail"]
                assert results["cold_crlf_exact"], "Claude Code changed the CRLF cold evidence"
                content = {"type": "text", "text": canonical({"objective": packet["context"]["task"]["objective"],
                                                              "cold_tail": cold["content"]})}
            else:
                reading_cold = "toolu_packet" in values
                selected = calls[int(reading_cold)]
                tool = next(item["name"] for item in request["tools"]
                            if item["name"].startswith("mcp__contextrail__")
                            and item["name"].replace(".", "_").endswith("__" + selected))
                args = {**data["cold_ref"], "start": data["start"]} if reading_cold else {
                    "snapshot": data["snapshot"], "budget": 7000}
                content = {"type": "tool_use", "id": "toolu_cold" if reading_cold else "toolu_packet",
                           "name": tool, "input": args}
            is_tool = content["type"] == "tool_use"
            usage = {"input_tokens": 42, "output_tokens": 8, "cache_creation_input_tokens": 0,
                     "cache_read_input_tokens": 0}
            message = {"id": "msg_windows_transport", "type": "message", "role": "assistant",
                       "model": request["model"], "usage": usage, "content": [content],
                       "stop_reason": "tool_use" if is_tool else "end_turn", "stop_sequence": None}
            if not request.get("stream"):
                self.reply(canonical(message).encode(), "application/json")
                return
            key = "input" if is_tool else "text"
            value = content[key]
            delta = {"type": "input_json_delta", "partial_json": canonical(value)} if is_tool else {
                "type": "text_delta", "text": value}
            events = [
                {"type": "message_start", "message": {**message, "content": [], "stop_reason": None}},
                {"type": "content_block_start", "index": 0, "content_block": {**content, key: {} if is_tool else ""}},
                {"type": "content_block_delta", "index": 0, "delta": delta},
                {"type": "content_block_stop", "index": 0},
                {"type": "message_delta", "delta": {"stop_reason": message["stop_reason"], "stop_sequence": None},
                 "usage": {"output_tokens": 8}},
                {"type": "message_stop"},
            ]
            body = "".join(f"event: {event['type']}\ndata: {canonical(event)}\n\n" for event in events)
            self.reply(body.encode("utf-8"), "text/event-stream")

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler), results, marker


def run(output):
    if os.name != "nt":
        raise SystemExit("Windows is required.")
    data, expected = fixture(output, existing_memory=False)
    profile = data["layout"]["clients"]["claude"]
    alias = output / "中文 junction"
    powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    junction = run_local([powershell, "-NoProfile", "-NonInteractive", "-Command",
        "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path $env:H08_LINK -Target $env:H08_TARGET | Out-Null"],
        env={**os.environ, "H08_LINK": str(alias), "H08_TARGET": data["workspace"]}, timeout=15)
    assert junction.returncode == 0, junction.stderr.decode("utf-8", "replace")
    config_dir = output / "claude-config"
    config_dir.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith(("ANTHROPIC_", "CLAUDE_CODE_"))
           and key.upper() not in {"CLAUDECODE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}}
    env.update(CLAUDE_CONFIG_DIR=str(config_dir), CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
               ANTHROPIC_API_KEY="fixture-only-local-key", ENABLE_TOOL_SEARCH="false",
               NO_PROXY="127.0.0.1,localhost")
    server, observed, marker = messages_gateway(data, expected)
    with server:
        env["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:" + str(server.server_port)
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        try:
            args = [shutil.which("claude.cmd") or shutil.which("claude.exe"), "-p", "--no-session-persistence",
                    "--output-format", "stream-json", "--verbose", "--strict-mcp-config",
                    "--mcp-config", profile["mcp_config"], "--settings", profile["hook_config"],
                    "--tools", "", "--allowedTools", "mcp__contextrail__*", "--max-turns", "6"]
            result = run_local(args, cwd=alias, env=env,
                               input=(marker + "\r\n" + prompt(data)).encode("utf-8"), timeout=60)
        finally:
            server.shutdown()
            worker.join()
    (output / "claude-stdout.jsonl").write_bytes(result.stdout)
    (output / "claude-stderr.txt").write_bytes(result.stderr)
    events = [json.loads(line) for line in result.stdout.splitlines()]
    init = next((event for event in events if event.get("type") == "system" and event.get("subtype") == "init"), {})
    final = next((event for event in events if event.get("type") == "result"), {})
    with Store(data["store"]) as store:
        receipts = [row["metadata"] for row in store.events(Scope(**data["scope"])) if row["kind"] == "native_context.prepared"]
    summary = {**observed, "exit_code": result.returncode, "native_session": init.get("session_id"),
               "hook_same_snapshot": any(row.get("client") == "claude" and row.get("snapshot") == data["snapshot"]
                   and row.get("native_session") == init.get("session_id") for row in receipts),
               "final_exact": json.loads(final.get("result", "null")) == expected}
    (output / "windows-claude-summary.json").write_text(canonical(summary), encoding="utf-8")
    print(canonical(summary), flush=True)
    assert result.returncode == 0 and init.get("session_id"), summary
    assert all(summary.get(key) is True for key in ("prompt_unicode", "packet_exact", "cold_crlf_exact",
                                                   "hook_same_snapshot", "final_exact")), summary
    return data, expected


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--with-codex", action="store_true", help="Also run real Codex on the same task using existing auth")
    options = parser.parse_args()
    directory = options.output.resolve()
    data, expected = run(directory)
    if options.with_codex:
        assert run_client("codex", directory, data, expected)["retrieval_passed"]
