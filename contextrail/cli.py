"""Local JSON-lines transport. Deliberately not advertised as MCP."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import TextIO

from .context import Compiler
from .errors import InvalidRequest, RailError
from .host import ContextTools
from .models import Scope, Selection, Target, canonical, unicode_text
from .store import Store


def serve(tools: ContextTools, source: TextIO, sink: TextIO, *, max_request_chars: int = 65536) -> None:
    while True:
        line = source.readline(max_request_chars + 1)
        if not line:
            break
        request_id = None
        try:
            if len(line) > max_request_chars:
                while line and not line.endswith("\n"):
                    line = source.readline(max_request_chars + 1)
                raise InvalidRequest("Request exceeds transport size limit.")
            try:
                request = json.loads(line)
            except (ValueError, RecursionError):
                raise InvalidRequest("Malformed JSON request.") from None
            if not isinstance(request, dict) or set(request) != {"id", "tool", "arguments"}:
                raise InvalidRequest("Expected id, tool and arguments fields.")
            if type(request["id"]) not in (str, int) or isinstance(request["id"], str) and len(request["id"]) > 128:
                raise InvalidRequest("Request id must be a short string or integer.")
            if isinstance(request["id"], str):
                unicode_text(request["id"])
            request_id = request["id"]
            result = tools.call(request["tool"], request["arguments"])
            response = {"id": request_id, "result": result}
        except RailError as exc:
            response = {"id": request_id, "error": {"code": exc.code, "message": str(exc)}}
        except sqlite3.Error:
            response = {"id": request_id, "error": {"code": "storage_error", "message": "Local storage is unavailable."}}
        sink.write(canonical(response) + "\n")
        sink.flush()


def demo() -> dict:
    """Exercise real persistence, compilation, delivery and fencing, without an LLM."""
    scope = Scope("demo", "sample", "main", "handoff")
    a = Target("session-a", "provider-a", "model-a")
    b = Target("session-b", "provider-b", "model-b")
    with tempfile.TemporaryDirectory(prefix="contextrail-demo-") as folder:
        with Store(Path(folder) / "rail.sqlite3") as store:
            lease = store.create_task(scope, a, "Preserve exact evidence across A -> B -> A.",
                                      constraints=("Never change the acceptance criteria.",),
                                      acceptance=("Exact identifier remains CR-测试-0042.",),
                                      allowed_providers=(a.provider, b.provider))
            required = store.put(scope, lease, "contract", "identifier=CR-测试-0042\r\n".encode("utf-8"), expected_revision=0)
            cold = store.put(scope, lease, "old-log", b"old diagnostic output\n" * 1000, expected_revision=0)
            compiler = Compiler(store)
            steps = []
            for target in (b, a):
                sid = store.snapshot(scope, lease, (Selection(required, required=True), Selection(cold)))
                h = store.prepare(scope, lease, sid, target)
                packet = compiler.for_handoff(scope, h.id, budget=4096)
                # The host must send packet.body to its model request and then acknowledge delivery.
                # This local demo is a host simulation and does not call a model.
                store.acknowledge(scope, h.id, target.session, packet.sha256)
                store.validate(scope, h.id)
                lease = store.activate(scope, h.id)
                steps.append({"target": target.session, "epoch": lease.epoch,
                              "context_bytes": packet.units, "cold_pages": len(packet.omitted)})
            recovered = ContextTools(store, scope, a.session).call("context.get", asdict(cold))
            return {"mode": "local_host_simulation", "model_calls": 0, "handoffs": steps,
                    "final_owner": store.task(scope)["owner"], "cold_artifact_bytes": recovered["artifact_bytes"],
                    "exact_evidence_verified": store.get(scope, required).content == "identifier=CR-测试-0042\r\n".encode("utf-8")}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="ContextRail local evidence runtime")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="Run an isolated A -> B -> A local simulation")
    commands.add_parser("tools", help="Print host-neutral read tool definitions")
    for command in ("init", "serve"):
        sub = commands.add_parser(command)
        sub.add_argument("--store", required=True, type=Path)
        sub.add_argument("--tenant", required=True)
        sub.add_argument("--project", required=True)
        sub.add_argument("--branch", default="main")
        sub.add_argument("--task", required=True)
        sub.add_argument("--session", required=True)
        if command == "init":
            sub.add_argument("--provider", required=True)
            sub.add_argument("--model", required=True)
            sub.add_argument("--objective", required=True)
            sub.add_argument("--allow-provider", action="append", required=True)
            sub.add_argument("--constraint", action="append", default=[])
            sub.add_argument("--acceptance", action="append", default=[])
    return root


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        if args.command == "demo":
            print(canonical(demo()))
        elif args.command == "tools":
            print(canonical(ContextTools.definitions()))
        else:
            scope = Scope(args.tenant, args.project, args.branch, args.task)
            if args.command == "serve" and not args.store.is_file():
                raise InvalidRequest("Initialize the store before serving context tools.")
            with Store(args.store) as store:
                if args.command == "init":
                    target = Target(args.session, args.provider, args.model)
                    lease = store.create_task(scope, target, args.objective,
                                              constraints=tuple(args.constraint), acceptance=tuple(args.acceptance),
                                              allowed_providers=tuple(args.allow_provider))
                    print(canonical({"scope": asdict(scope), "lease": asdict(lease)}))
                else:
                    serve(ContextTools(store, scope, args.session), sys.stdin, sys.stdout)
        return 0
    except RailError as exc:
        print(canonical({"error": {"code": exc.code, "message": str(exc)}}), file=sys.stderr)
        return 1
    except UnicodeError:
        print(canonical({"error": {"code": "invalid_request", "message": "Transport input must be valid UTF-8."}}), file=sys.stderr)
        return 1
    except (sqlite3.Error, OSError):
        print(canonical({"error": {"code": "storage_error", "message": "Unable to open or use the local store."}}), file=sys.stderr)
        return 1
