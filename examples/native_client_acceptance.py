"""Exercise the requested task-packet/cold-page behavior in real installed CLIs.

Uses the user's existing model/auth settings. No model override, Hook-trust
bypass, new daemon or publication. Raw native logs stay in the supplied local
output directory; stdout contains only selected acceptance results.
"""

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tomllib
import uuid

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))

from contextrail import Lease, Ref, Store, Target
from contextrail.bridge import BridgeEvidence
from contextrail.models import canonical
from contextrail.native_clients import host_hook, prepare_clients
from contextrail.project import load_project


def fixture(output):
    output.mkdir(parents=True, exist_ok=False)
    workspace = output / "workspace"
    workspace.mkdir()
    (workspace / ".contextrail.toml").write_text('[project]\ntask="client-acceptance"\n', encoding="utf-8")
    bundle = output / "bundle"
    bundle.mkdir()
    goal = "仅验证自用任务包与原文回读，标记 " + uuid.uuid4().hex[:10]
    tail = "COLD-" + uuid.uuid4().hex
    (bundle / "user.md").write_bytes((goal + "\r\n").encode())
    cold = ("原始冷页🙂\r\n" * 6000 + tail + "\r\n").encode()
    (bundle / "cold.log").write_bytes(cold)
    rows = [{"id": "R001", "revision": 1, "status": "active", "kind": "goal", "scope": "global", "always_load": True,
             "statement": goal, "source": {"path": "user.md", "quote": goal, "locator": "controlled acceptance request"},
             "acceptance": "两个客户端分别读取指定 snapshot 和 cold page", "progress": "pending", "evidence": []},
            {"id": "R002", "revision": 1, "status": "active", "kind": "deliverable", "scope": "implementation", "always_load": False,
             "statement": "按引用读取 cold.log 末尾", "source": {"path": "user.md", "quote": goal, "locator": "request"},
             "acceptance": "返回实际原文", "progress": "done", "evidence": ["cold.log"]}]
    (bundle / "requirements.json").write_text(canonical({"schema": 1, "task_id": "client-acceptance",
        "snapshot_id": "client-acceptance/rev-1", "requirements": rows}), encoding="utf-8")
    config = load_project(workspace)
    config.store.parent.mkdir(parents=True)
    with Store(config.store) as store:
        lease = store.create_task(config.scope, Target("owner", "host", "client-managed-model-unknown"),
                                  "native client acceptance", allowed_providers=("host",))
        exported = BridgeEvidence(store, config.scope, lease.session).import_bundle(bundle, "implementation", lease)
        manifest = json.loads(store.get(config.scope, Ref(**exported["manifest_ref"])).content)
        # Existing main-memory hook command: the same program invoked by current Codex hooks.json.
        command_file = output / "existing-memory-command.json"
        command_file.write_text(canonical([str(Path.home() / "AppData/Roaming/uv/tools/basic-memory/Scripts/python.exe"),
                                          str(Path.home() / ".codex/services/engineering-memory/app/memory_cli.py"), "hook"]), encoding="utf-8")
        layout = prepare_clients(store, config, lease, exported["snapshot"], output / "layout", memory_command=command_file,
                                  context_bytes=7000)
    schema = {"type": "object", "properties": {"objective": {"type": "string"}, "cold_tail": {"type": "string"}},
              "required": ["objective", "cold_tail"], "additionalProperties": False}
    (output / "response-schema.json").write_text(canonical(schema), encoding="utf-8")
    expected = {"objective": goal, "cold_tail": tail + "\r\n"}
    (output / "expected.json").write_text(canonical(expected), encoding="utf-8")
    descriptor = {"workspace": str(workspace), "store": str(config.store), "scope": asdict(config.scope), "layout": layout,
                  "snapshot": exported["snapshot"], "cold_ref": manifest["sources"]["cold.log"],
                  "start": len(cold) - len((tail + "\r\n").encode()), "schema": schema}
    (output / "fixture.json").write_text(canonical(descriptor), encoding="utf-8")
    return descriptor, expected


def prompt(data):
    return ("ContextRail 自用 demo：要求与原始来源回读。 This is a read-only native MCP acceptance request. Do not edit files, run shell commands, or delegate. "
            "Use only the contextrail MCP tools. First call context.packet with snapshot=" + data["snapshot"] +
            " and budget=7000. Then call context.get with arguments " + canonical({**data["cold_ref"], "start": data["start"]}) +
            ". Return the actual packet task objective and exact cold-page content as JSON fields objective and cold_tail. "
            "Copy the entire content value character-for-character, including both CR and LF at the end. "
            "In final JSON encode that ending as \\r\\n, never normalize it to \\n. "
            "Do not infer either value from this prompt; retrieve both from the tools.")


def run_client(client, output, data, expected):
    profile = data["layout"]["clients"][client]
    if client == "codex":
        server = tomllib.loads(Path(profile["mcp_config"]).read_text(encoding="utf-8"))["mcp_servers"]["contextrail"]
        args = [shutil.which("codex.exe") or "codex", "exec", "--ephemeral", "--json", "--skip-git-repo-check",
                "--sandbox", "read-only", "-C", data["workspace"], "--color", "never",
                "--output-schema", str(output / "response-schema.json"), "--output-last-message", str(output / "codex-final.json")]
        # Override the complete maps for this invocation. CLI dotted-key
        # overrides treat quotes literally, creating invalid phantom servers.
        transport = "{command=" + json.dumps(server["command"]) + ",args=" + json.dumps(server["args"], ensure_ascii=False) + "}"
        args += ["-c", "mcp_servers={contextrail=" + transport + "}", "-c", "plugins={}", "-"]
    else:
        installed = Path(shutil.which("claude.cmd")).parent / "node_modules/@anthropic-ai/claude-code/bin/claude.exe"
        args = [str(installed), "-p", "--no-session-persistence", "--output-format", "stream-json", "--verbose",
                "--strict-mcp-config", "--mcp-config", profile["mcp_config"], "--settings", profile["hook_config"],
                "--tools", "", "--allowedTools", "mcp__contextrail__*", "--max-turns", "6"]
    (output / (client + "-invocation.json")).write_text(canonical({"argv": args, "model_override": False,
        "hook_trust_bypass": False}), encoding="utf-8")
    start = time.monotonic()
    creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(args, cwd=data["workspace"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, creationflags=creationflags)
    timed_out = False
    try:
        stdout, stderr = process.communicate(prompt(data).encode("utf-8"), timeout=180)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
        else:
            process.kill()
        stdout, stderr = process.communicate()
    (output / (client + "-stdout.jsonl")).write_bytes(stdout)
    (output / (client + "-stderr.txt")).write_bytes(stderr)
    events = []
    for line in stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    tool_names = []
    packet_read_exact = cold_read_exact = False
    native_session = None
    def inspect_content(parts):
        nonlocal packet_read_exact, cold_read_exact
        if isinstance(parts, str):
            parts = [{"type": "text", "text": parts}]
        for part in parts or []:
            try:
                value = json.loads(part.get("text", ""))
            except (ValueError, TypeError, AttributeError):
                continue
            if isinstance(value, dict) and "context" in value:
                packet_read_exact |= value["context"]["task"]["objective"] == expected["objective"]
            if isinstance(value, dict) and "artifact_bytes" in value:
                cold_read_exact |= value.get("content") == expected["cold_tail"]
    for event in events:
        if event.get("type") == "thread.started":
            native_session = event.get("thread_id")
        if event.get("type") == "system" and event.get("subtype") == "init":
            native_session = event.get("session_id")
        item = event.get("item", {})
        if item.get("type") == "mcp_tool_call":
            tool_names.append(item.get("tool"))
            if isinstance(item.get("result"), dict):
                inspect_content(item["result"].get("content"))
        for block in event.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_names.append(block.get("name"))
            if isinstance(block, dict) and block.get("type") == "tool_result":
                inspect_content(block.get("content"))
    result = None
    try:
        if client == "codex":
            result = json.loads((output / "codex-final.json").read_bytes())
        else:
            final = next(event for event in reversed(events) if event.get("type") == "result")
            result = final.get("structured_output") or json.loads(final.get("result", ""))
    except (OSError, ValueError, StopIteration):
        pass
    exact = result == expected
    packet_called = any(name in {"context.packet", "mcp__contextrail__context_packet", "mcp__contextrail__context.packet"} for name in tool_names)
    get_called = any(name in {"context.get", "mcp__contextrail__context_get", "mcp__contextrail__context.get"} for name in tool_names)
    summary = {"client": client, "exit_code": process.returncode, "timed_out": timed_out,
        "elapsed_seconds": round(time.monotonic() - start, 2), "tool_names": tool_names,
        "exact_result": exact, "packet_called": packet_called, "cold_get_called": get_called,
        "passed": process.returncode == 0 and exact and packet_called and get_called,
        "packet_wire_exact": packet_read_exact, "cold_wire_exact": cold_read_exact,
        "retrieval_passed": process.returncode == 0 and packet_read_exact and cold_read_exact and packet_called and get_called,
        "native_session": native_session,
        "hook_activation": "not_changed_requires_native_review" if client == "codex" else "explicit_settings_invocation"}
    with Store(data["store"]) as store:
        from contextrail.models import Scope
        receipts = [row["metadata"] for row in store.events(Scope(**data["scope"])) if row["kind"] == "native_context.prepared"
                    and row["metadata"].get("native_session") == native_session and row["metadata"].get("client") == client]
        summary["hook_preparation_receipts"] = receipts
    (output / (client + "-summary.json")).write_text(canonical(summary), encoding="utf-8")
    print(canonical(summary), flush=True)
    return summary


def live_memory(output, data):
    payload = {"hook_event_name": "UserPromptSubmit", "session_id": "acceptance-memory-" + uuid.uuid4().hex,
               "cwd": data["workspace"], "prompt": "ContextRail Bridge 自用 demo 要求与源新鲜度"}
    result = host_hook(data["layout"]["registry"], "codex", payload)
    (output / "live-memory-hook.json").write_text(canonical(result), encoding="utf-8")
    try:
        context = json.loads(result["hookSpecificOutput"]["additionalContext"])
        summary = {"invocation": "direct_adapter_with_live_existing_main_hook", "memory_status": context["memory"]["status"],
                   "history_chars": len(context["memory"]["text"]), "task_snapshot": context["task"]["snapshot"],
                   "history_control": context["history_control"]}
    except (KeyError, ValueError):
        summary = {"invocation": "direct_adapter_with_live_existing_main_hook", "decision": result.get("decision"), "memory_status": "not_observed"}
    (output / "live-memory-summary.json").write_text(canonical(summary), encoding="utf-8")
    print(canonical(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--client", action="append", choices=("codex", "claude"))
    parser.add_argument("--existing-fixture", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.existing_fixture:
        data = json.loads((output / "fixture.json").read_bytes())
        expected = json.loads((output / "expected.json").read_bytes())
    else:
        data, expected = fixture(output)
    print(canonical({"fixture": str(output), "snapshot": data["snapshot"]}), flush=True)
    if not args.existing_fixture:
        live_memory(output, data)
    for client in args.client or ("codex", "claude"):
        run_client(client, output, data, expected)
