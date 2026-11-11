"""Host CLI, legacy JSON-lines transport and optional MCP stdio adapter."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import TextIO
from uuid import uuid4

from .context import Compiler
from .errors import InvalidRequest, RailError
from .host import ContextTools
from .models import Lease, Scope, Selection, Target, canonical, unicode_text
from .store import Store


PROJECT_COMMANDS = ("project-info", "project-init", "capture-result", "mcp", "bridge-import", "bridge-packet",
                    "bridge-receipt", "status", "deliverable-register", "deliverables", "deliverable", "file-history", "history-details", "history-neighborhood", "checkpoints", "search", "continue", "explain",
                    "client-prepare", "host-hook", "client-toggle", "existing-memory-hook", "client-apply", "client-remove", "memory-save", "extract-result", "batch-read",
                    "search-indexed", "index-status", "index-rebuild", "index-recover", "backup-create", "result-search",
                    "web-cache-store", "web-cache-get",
                    "bookmark-set", "bookmark-open", "bookmark-list", "bookmark-remove",
                    "directory-sync", "directory-status", "directory-memory-enqueue", "redact-evidence", "event-import", "timeline", "timeline-reindex", "query", "brief", "metrics", "efficiency", "record-request",
                    "recover", "resume-bind", "resume-offer", "resume-accept", "resume-abort", "intent-project", "workscene", "native-history",
                    "experience-review", "experience-reject", "read-heat", "document-import", "material-get",
                    "memory-consolidation", "experience-rule-draft", "personal-profile")


def serve(tools: ContextTools, source: TextIO, sink: TextIO, *, max_request_chars: int = 65536) -> None:
    from .accounting import HostAccounting
    meter = HostAccounting(tools.store, tools.scope, tools.session_id)
    generation = uuid4().hex
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
            if request["tool"] in ("context.get", "context.batch"):
                observation = f"jsonl-{generation}-{type(request_id).__name__}-{request_id}"
                result = meter.call_tool(tools, request["tool"], request["arguments"], observation_id=observation)
            else:
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
    restore = commands.add_parser("backup-restore", help="Restore a local backup to a new review directory")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--output", type=Path, required=True)
    for command in PROJECT_COMMANDS:
        sub = commands.add_parser(command)
        sub.add_argument("--workspace", type=Path, default=Path.cwd())
        sub.add_argument("--config", type=Path)
        sub.add_argument("--defaults", type=Path, help="Personal ContextRail byte limits TOML")
        sub.add_argument("--task")
        sub.add_argument("--project-id")
        sub.add_argument("--tenant")
        sub.add_argument("--store")
        sub.add_argument("--response-bytes", type=int)
        sub.add_argument("--read-bytes", type=int)
        if command in ("project-info", "client-prepare", "mcp"):
            sub.add_argument("--context-bytes", type=int)
        if command in ("project-info", "client-prepare"):
            sub.add_argument("--hook-bytes", type=int)
        if command in {'mcp','query','status','brief','metrics','file-history','history-details','history-neighborhood','deliverables','deliverable','bridge-packet','continue'}:sub.add_argument('--query-binding',type=Path,help='Trusted task/workspace source selection; never supplied by model arguments')
        if command not in ("project-info", "host-hook", "client-toggle", "existing-memory-hook", "client-apply", "client-remove", "memory-save", "native-history", "index-recover", "backup-create"):
            sub.add_argument("--session", required=True)
        if command in ("backup-create", "index-recover"):
            sub.add_argument("--output", type=Path, required=True)
            if command == "backup-create":
                sub.add_argument("--include-code", action="store_true", help="Archive committed Git HEAD")
                sub.add_argument("--knowledge-root", type=Path, help="Explicit Markdown root")
                sub.add_argument("--queue-db", type=Path, help="Explicit complete engineering-memory state database")
        elif command == "project-init":
            sub.add_argument("--objective", required=True)
            sub.add_argument("--provider", default="host")
            sub.add_argument("--model", default="external")
        elif command == "capture-result":
            sub.add_argument("--epoch", required=True, type=int)
            sub.add_argument("--result", required=True, help="Host-observed JSON file, or - for stdin")
            sub.add_argument("--intent", help="Optional projection query; never execution metadata")
        elif command == "redact-evidence":
            sub.add_argument("--epoch", required=True, type=int)
            sub.add_argument("--source-ref", required=True, help="Exact saved source reference as JSON")
            sub.add_argument("--role", required=True, choices=('user','assistant','tool','document','lifecycle','evidence'))
        elif command=='query':
            sub.add_argument('--input',type=Path,help='JSON request file; omit to read stdin')
        elif command == "document-import":
            sub.add_argument("--file", type=Path, required=True, help="Explicit PDF, DOCX, XLSX or PPTX in this workspace")
            sub.add_argument("--epoch", type=int, required=True)
            sub.add_argument("--memory-home", type=Path, default=Path.home() / ".codex/services/engineering-memory")
        elif command == "material-get":
            sub.add_argument("--query-binding", type=Path, required=True)
            sub.add_argument("--ref", required=True, help="Exact material Ref from the query result as JSON")
            sub.add_argument("--start", type=int, default=0)
            sub.add_argument("--end", type=int)
        elif command == "personal-profile":
            sub.add_argument("--query-binding", type=Path, required=True)
            sub.add_argument("--input", type=Path, help="Explicit user profile action JSON, or stdin")
            sub.add_argument("--epoch", type=int, help="Current owner epoch for recording or withdrawing a default")
        elif command == "memory-consolidation":
            sub.add_argument("--query-binding", type=Path, required=True)
            sub.add_argument("--input", type=Path, help="Bounded review request JSON, or stdin")
            sub.add_argument("--epoch", type=int, help="Current owner lease epoch for applying or undoing grouping")
        elif command in {"experience-review", "experience-reject", "experience-rule-draft"}:
            sub.add_argument("--query-binding", type=Path, required=True,
                             help="Trusted workspace and exact native-session selection")
            sub.add_argument("--since", help="Inclusive source time with explicit time zone")
            sub.add_argument("--until", help="Exclusive source time with explicit time zone")
            if command == "experience-review":
                sub.add_argument("--reconsider", help="Show this explicitly requested candidate again")
                sub.add_argument("--offset", type=int, default=0)
                sub.add_argument("--limit", type=int, default=20)
            elif command == "experience-reject":
                sub.add_argument("--epoch", type=int, required=True)
                sub.add_argument("--candidate", required=True)
                sub.add_argument("--evidence-version", required=True)
                sub.add_argument("--reason", required=True)
            else:
                sub.add_argument("--candidate", required=True)
                sub.add_argument("--evidence-version", required=True)
                sub.add_argument("--target", default="AGENTS.md")
                sub.add_argument("--rule", required=True, help="A short proposed rule for human review")
                sub.add_argument("--reconsider", action="store_true")
        elif command=='metrics':
            sub.add_argument('--offset',type=int,default=0)
            sub.add_argument('--limit',type=int,default=20)
        elif command == "read-heat":
            sub.add_argument("--snapshot", required=True)
            sub.add_argument("--offset", type=int, default=0)
            sub.add_argument("--limit", type=int, default=20)
        elif command in {'efficiency','record-request'}:
            sub.add_argument('--input',type=Path,help='Explicit host receipt/proposal JSON; omit to read stdin')
            if command=='record-request':
                sub.add_argument('--packet',type=Path,required=True,help='Host SDK asdict(Packet) JSON file')
                sub.add_argument('--final-request',type=Path,help='Actual serialized final request bytes; never printed or retained in ledger')
        elif command in {'timeline','timeline-reindex'}:
            sub.add_argument('--limit',type=int,default=20)
            if command=='timeline-reindex':sub.add_argument('--epoch',type=int,required=True)
            else:
                sub.add_argument('--offset',type=int,default=0)
                for flag in ('native-session','file','role','origin','error-category','since','until','around'):sub.add_argument('--'+flag)
        elif command=='native-history':
            sub.add_argument('--memory-home',type=Path,default=Path.home()/'.codex/services/engineering-memory')
            sub.add_argument('--client',choices=('codex','claude'),required=True)
            sub.add_argument('--native-session',required=True)
            sub.add_argument('--source',type=Path,help='Exact source under original enabled roots; host only')
            sub.add_argument('--input',type=Path,help='JSON read options or {ref,start,end} for processed content')
            sub.add_argument('--output',type=Path,help='Export managed Bridge recovery material')
        elif command=='workscene':
            sub.add_argument('--operation',choices=('read','error-history','attempt','declare','complete','reserve','started','partial','timed-out','recover','cancel','finished','export'),default='read')
            sub.add_argument('--epoch',type=int)
            sub.add_argument('--input',type=Path)
        elif command == "event-import":
            sub.add_argument("--epoch",required=True,type=int)
            sub.add_argument("--memory-home",type=Path,default=Path.home()/'.codex/services/engineering-memory')
            sub.add_argument("--client",required=True,choices=('codex','claude'))
            sub.add_argument("--native-session",required=True)
            sub.add_argument("--offset",type=int,default=0)
            sub.add_argument("--limit",type=int,default=20)
        elif command == "result-search":
            sub.add_argument("--result-ref", required=True, help="Exact saved result manifest reference as JSON")
            sub.add_argument("--query", required=True)
            sub.add_argument("--limit", type=int, default=5)
            sub.add_argument("--offset", type=int, default=0)
        elif command == "web-cache-store":
            sub.add_argument("--epoch", type=int, required=True)
            sub.add_argument("--input", type=Path, required=True, help="Trusted capture-host HTTP observation JSON")
        elif command == "web-cache-get":
            sub.add_argument("--source", required=True)
            sub.add_argument("--url", required=True)
            sub.add_argument("--refresh", action="store_true", help="Require a new fetch by the existing connector")
        elif command == "directory-sync":
            sub.add_argument("--epoch", required=True, type=int)
            selection = sub.add_mutually_exclusive_group(required=True)
            selection.add_argument("--root", help="Explicit workspace directory, no source execution")
            selection.add_argument("--resume", help="Exact saved directory run")
            sub.add_argument("--step", type=int, help="Exact saved cursor step, required for resume")
            for flag in ("max-files", "max-depth", "max-file-bytes", "max-directory-entries"):
                sub.add_argument("--" + flag, type=int)
        elif command == "directory-status":
            sub.add_argument("--run", required=True)
            sub.add_argument("--offset", type=int, default=0)
            sub.add_argument("--limit", type=int, default=20)
        elif command == "directory-memory-enqueue":
            sub.add_argument("--run", required=True)
            sub.add_argument("--epoch", required=True, type=int)
            sub.add_argument("--memory-home", type=Path, default=Path.home() / ".codex/services/engineering-memory")
            sub.add_argument("--memory-code", type=Path, help="Trusted existing engineering-memory application code")
            sub.add_argument("--offset", type=int, default=0)
            sub.add_argument("--limit", type=int, default=20)
        elif command == "extract-result":
            sub.add_argument("--source", required=True, help="JSON name/revision evidence reference")
            sub.add_argument("--kind", choices=("log", "json", "csv", "git_status", "git_numstat"), required=True)
            sub.add_argument("--options", default="{}", help="JSON extraction options; never an expression")
            sub.add_argument("--limit", type=int, default=20)
        elif command == "batch-read":
            sub.add_argument("--calls", required=True, help="JSON independent-call array file or - for stdin")
            sub.add_argument("--concurrency", type=int, default=2)
            sub.add_argument("--per-call-bytes", type=int, default=2048)
        elif command.startswith("bridge-"):
            sub.add_argument("--bridge-validator", type=Path, help="Trusted installed context-bridge requirements.py")
            if command == "bridge-import":
                sub.add_argument("--bundle", type=Path, required=True)
                sub.add_argument("--scope", dest="bridge_scope", required=True)
                sub.add_argument("--epoch", required=True, type=int)
            else:
                sub.add_argument("--snapshot", required=True, help="Exact Rail snapshot returned by bridge-import")
                if command == "bridge-packet":
                    sub.add_argument("--budget", type=int)
                    sub.add_argument("--prefer-frequent-reads", action="store_true")
                else:
                    sub.add_argument("--receipt", type=Path, required=True)
        elif command in ('recover','resume-bind','resume-offer','resume-accept','resume-abort','intent-project'):
            if command in ('recover','resume-bind','resume-offer'):
                sub.add_argument('--snapshot',required=command!='recover')
                sub.add_argument('--version',type=int,required=command!='recover')
                sub.add_argument('--budget',type=int)
            if command in ('resume-bind','resume-offer','resume-abort'):sub.add_argument('--epoch',type=int,required=True)
            if command in ('recover','resume-bind','intent-project'):
                sub.add_argument('--client',choices=('codex','claude'),required=command!='recover')
                sub.add_argument('--native-session',required=command!='recover')
            if command=='resume-bind':sub.add_argument('--binding-id',required=True)
            if command in ('resume-offer','resume-accept','resume-abort'):sub.add_argument('--operation-id',required=True)
            if command=='resume-offer':sub.add_argument('--target',required=True,help='Trusted host JSON Target')
            if command=='resume-accept':sub.add_argument('--receipt',type=Path,required=True)
            if command=='recover':
                sub.add_argument('--offset',type=int,default=0);sub.add_argument('--limit',type=int,default=20)
            if command=='intent-project':
                sub.add_argument('--memory-home',type=Path,default=Path.home()/'.codex/services/engineering-memory')
                sub.add_argument('--event-id',required=True);sub.add_argument('--start',type=int,default=0);sub.add_argument('--end',type=int)
                sub.add_argument('--output',type=Path,help='Create a new full processed-source file without overwriting')
        elif command == "checkpoints":
            sub.add_argument("--offset", type=int, default=0)
            sub.add_argument("--limit", type=int, default=20)
        elif command == "search":
            sub.add_argument("--query", required=True)
            sub.add_argument("--limit", type=int, default=20)
        elif command == "search-indexed":
            sub.add_argument("--query", required=True)
            sub.add_argument("--limit", type=int, default=20)
            sub.add_argument("--offset", type=int, default=0)
        elif command == "index-rebuild":
            sub.add_argument("--epoch", type=int, required=True)
        elif command.startswith("bookmark-"):
            if command == "bookmark-list":
                sub.add_argument("--prefix", default="")
                sub.add_argument("--offset", type=int, default=0)
                sub.add_argument("--limit", type=int, default=20)
                sub.add_argument("--completion", action="store_true", help="Output only visible bookmark names as ASCII JSON")
            else:
                sub.add_argument("--name", required=True)
            if command in {"bookmark-set", "bookmark-remove"}:
                sub.add_argument("--epoch", required=True, type=int)
            if command == "bookmark-set":
                sub.add_argument("--snapshot", required=True)
                sub.add_argument("--query", help="Optional saved search in the pinned task")
            if command == "bookmark-open":
                sub.add_argument("--budget", type=int)
        elif command in ("continue", "explain", "brief"):
            sub.add_argument("--snapshot", required=True)
            sub.add_argument("--budget", type=int)
            if command == "continue":
                sub.add_argument("--prefer-frequent-reads", action="store_true")
            if command in ('brief','explain'):
                sub.add_argument('--offset',type=int,default=0)
                sub.add_argument('--limit',type=int,default=20)
                sub.add_argument('--trace-offset',type=int,default=0)
                sub.add_argument('--trace-limit',type=int,default=5)
                sub.add_argument('--requested',default='[]',help='JSON exact requested refs')
                sub.add_argument('--state-output',type=Path,help='Explicit new STATE file; refuses overwrite')
            if command == "brief":
                sub.add_argument("--offline", action="store_true", help="Include the existing queue using only local reads")
                sub.add_argument("--queue-offset", type=int, default=0)
                sub.add_argument("--queue-limit", type=int, default=10)
        elif command == "client-prepare":
            sub.add_argument("--epoch", type=int, required=True)
            sub.add_argument("--snapshot", required=True)
            sub.add_argument("--output", type=Path, required=True)
            sub.add_argument("--client", choices=("codex", "claude"), action="append")
            sub.add_argument("--memory-command", type=Path, help="JSON argv for the existing memory hook")
            sub.add_argument("--task-memory-command", type=Path, help="Optional explicit argv override only while this task binding is enabled")
            sub.add_argument("--native-memory-client", choices=("codex", "claude"), action="append", default=[])
            sub.add_argument('--query-binding',type=Path)
            sub.add_argument('--context-assistance',action='store_true',help='Enable exact compact recovery and optional file history on supported main-session hooks')
        elif command in ("host-hook", "client-toggle"):
            sub.add_argument("--registry", type=Path, required=True)
            sub.add_argument("--client", choices=("codex", "claude"), required=True)
            if command == "client-toggle":
                group = sub.add_mutually_exclusive_group(required=True)
                group.add_argument("--enabled", action="store_true")
                group.add_argument("--disabled", action="store_true")
            else:
                sub.add_argument("--native-memory", action="store_true", help="Existing native hook independently delivers memory; do not invoke it twice")
        elif command == "deliverable-register":
            sub.add_argument("--epoch",type=int,required=True)
            sub.add_argument("--input",type=Path,required=True)
            sub.add_argument("--allow-root",type=Path,action="append",default=[])
        elif command == "deliverable":
            sub.add_argument("--input",type=Path,required=True)
        elif command == "deliverables":
            sub.add_argument("--offset",type=int,default=0)
            sub.add_argument("--limit",type=int,default=20)
        elif command in ("file-history", "history-details", "history-neighborhood"):
            if command == "file-history": sub.add_argument("--file", required=True)
            else: sub.add_argument("--input",type=Path,required=True)
        elif command == "existing-memory-hook":
            sub.add_argument("--home", type=Path, default=Path.home() / ".codex/services/engineering-memory")
            sub.add_argument("--mode", choices=("hybrid", "text"), default="hybrid")
        elif command == "memory-save":
            sub.add_argument("--home", type=Path, default=Path.home() / ".codex/services/engineering-memory")
            sub.add_argument("--input", required=True, help="Explicit user record JSON file, or - for stdin")
        elif command in ("client-apply", "client-remove"):
            if command == "client-apply":
                sub.add_argument("--layout", type=Path, required=True)
                sub.add_argument("--adopt-layout", type=Path, help="Explicit old generated layout; claim only exact matching legacy entries")
            sub.add_argument("--client", choices=("codex", "claude"), action="append")
        if command in ("search-indexed", "result-search"):
            sub.add_argument("--context-chars", type=int, default=80)
            sub.add_argument("--window-limit", type=int, default=3)
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


def project_command(args):
    if args.command=='native-history':
        from .native_history import NativeHistory
        try:
            raw=args.input.read_bytes() if args.input else b'{}'
            if len(raw)>32768:raise ValueError()
            data=json.loads(raw)
            if not isinstance(data,dict):raise ValueError()
        except (OSError,ValueError,UnicodeError):raise InvalidRequest('History input requires bounded JSON options') from None
        reader=NativeHistory(args.memory_home,args.workspace,args.client,args.native_session,source=args.source)
        data.setdefault('budget',args.response_bytes or 16384)
        try:output=reader.export_state(args.output,**data) if args.output else reader.get(**data) if 'ref' in data else reader.read(**data)
        except TypeError:raise InvalidRequest('History options are invalid') from None
        print(canonical(output));return
    from .project import load_project
    config = load_project(args.workspace, config_path=args.config, defaults_path=args.defaults,
                          overrides={key: getattr(args, key, None) for key in
                                     ("task", "project_id", "tenant", "store", "response_bytes", "read_bytes",
                                      "context_bytes", "hook_bytes")})
    packet_budget = args.budget if getattr(args, 'budget', None) is not None else config.context_bytes
    if args.command == "project-info":
        print(canonical(config.describe()))
        return
    if args.command == "backup-create":
        from .maintenance import create_backup
        print(canonical(create_backup(config, args.output, knowledge_root=args.knowledge_root,
                                      queue_db=args.queue_db, include_code=args.include_code)))
        return
    if args.command == "index-recover":
        from .maintenance import recover_evidence_index
        print(canonical(recover_evidence_index(config, args.output)))
        return
    if args.command == "project-init":
        config.store.parent.mkdir(parents=True, exist_ok=True)
    elif not config.store.is_file():
        raise InvalidRequest("Initialize the project store before capture or MCP access.")
    with Store(config.store) as store:
        query_binding=None
        if getattr(args,'query_binding',None):
            from .query_sources import QueryBinding
            query_binding=QueryBinding.load(args.query_binding,config.scope.task,config.workspace)
        if args.command in {'metrics','efficiency','record-request'}:
            tools=ContextTools(store,config.scope,args.session,max_read_bytes=config.read_bytes,query_binding=query_binding)
            if args.command=='metrics':
                output=tools.call('context.metrics',{'offset':args.offset,'limit':args.limit,'budget':min(config.read_bytes,config.response_bytes)})
            else:
                try:
                    data=json.loads(args.input.read_bytes() if args.input else sys.stdin.buffer.read(65537))
                    if not isinstance(data,dict) or len(canonical(data).encode())>65536:raise ValueError()
                except (OSError,ValueError,TypeError):raise InvalidRequest('Invalid bounded request observation/proposal JSON') from None
                if args.command=='efficiency':output=tools.call('context.efficiency',data)
                else:
                    from .accounting import HostAccounting
                    from .models import Packet,Ref
                    try:
                        with args.packet.open('rb') as stream:packet_bytes=stream.read(16*1024*1024+1)
                        if len(packet_bytes)>16*1024*1024:raise ValueError()
                        raw=json.loads(packet_bytes);raw['target']=Target(**raw['target'])
                        for key in ('included','omitted'):raw[key]=tuple(Ref(**r) for r in raw[key])
                        packet=Packet(**raw)
                        body=None
                        if args.final_request:
                            with args.final_request.open('rb') as stream:body=stream.read(16*1024*1024+1)
                        if data.keys()-{'observation_id','components','reserves','usage','usage_format','request_id','run_id','attempt','latency_ms'}:raise ValueError()
                        output=HostAccounting(store,config.scope,args.session).record_request(packet,final_request=body,**data)
                    except (OSError,ValueError,TypeError,KeyError):raise InvalidRequest('Invalid host packet or observation arguments') from None
            print(canonical(output));return
        if args.command == "project-init":
            target = Target(args.session, args.provider, args.model)
            lease = store.create_task(config.scope, target, args.objective, allowed_providers=(args.provider,))
            print(canonical({**config.describe(), "lease": asdict(lease)}))
        elif args.command == "document-import":
            from .document_memory import import_to_existing_memory
            with store.transaction():
                store._lease(config.scope, Lease(args.session, args.epoch))
                output = import_to_existing_memory(args.memory_home, args.file, config.workspace)
            print(canonical(output))
        elif args.command == "material-get":
            try:
                ref = json.loads(args.ref)
            except (ValueError, RecursionError):
                raise InvalidRequest("Material Ref must be JSON.") from None
            tools = ContextTools(store, config.scope, args.session, max_read_bytes=config.read_bytes,
                                 query_binding=query_binding)
            print(canonical(tools.call("context.material_get", {
                "ref": ref, "start": args.start, "end": args.end,
                "budget": min(config.read_bytes, config.response_bytes)})))
        elif args.command == "personal-profile":
            import time
            from contextlib import nullcontext
            from .errors import Unavailable
            from .query_sources import ExistingMemory, SourceFailure

            try:
                with (args.input.open("rb") if args.input else nullcontext(sys.stdin.buffer)) as stream:
                    raw=stream.read(4097)
                if len(raw)>4096:
                    raise ValueError()
                request=json.loads(raw or b'{}')
                if not isinstance(request,dict) or request.keys()-{'action','scope','event_id','subject','statement','limit'}:
                    raise ValueError()
                action=request.get('action','view')
                if action not in ('view','set','revoke'):
                    raise ValueError()
            except (OSError,ValueError,RecursionError):
                raise InvalidRequest("Provide a bounded personal profile request.") from None
            if query_binding is None or query_binding.memory_home is None:
                raise InvalidRequest("A personal profile requires the original memory binding.")
            writes=action!='view'
            if writes and args.epoch is None:
                raise InvalidRequest("Recording a preference requires the current task owner epoch.")
            options={**request,'scope':query_binding.workspace}
            if 'scope' in request:
                options['record_scope']=request['scope']
            with store.transaction() if writes else nullcontext():
                if writes:
                    store._lease(config.scope,Lease(args.session,args.epoch))
                else:
                    store.session(config.scope,args.session)
                try:
                    result=ExistingMemory(query_binding).invoke(
                        "personal_profile",[],options,time.monotonic()+query_binding.timeout_seconds)
                except SourceFailure:
                    raise Unavailable("The original memory service could not complete the profile request.") from None
            print(canonical(result))
        elif args.command == "memory-consolidation":
            import time
            from contextlib import nullcontext
            from .errors import Unavailable
            from .query_sources import ExistingMemory, SourceFailure

            try:
                with (args.input.open("rb") if args.input else nullcontext(sys.stdin.buffer)) as stream:
                    raw = stream.read(16385)
                if len(raw) > 16384:
                    raise ValueError()
                request = json.loads(raw)
                allowed = {"action", "identifiers", "query", "group_id", "revision", "representative", "identifier",
                           "note_version", "start_line", "limit"}
                if not isinstance(request, dict) or request.keys() - allowed:
                    raise ValueError()
                if request.get("action", "review") not in ("review", "apply", "undo", "open"):
                    raise ValueError()
            except (OSError, ValueError, RecursionError):
                raise InvalidRequest("Provide a bounded consolidation request object.") from None
            if query_binding is None or query_binding.memory_home is None:
                raise InvalidRequest("Consolidation requires the trusted original memory binding.")
            writes = request.get("action") in ("apply", "undo")
            if writes and args.epoch is None:
                raise InvalidRequest("Applying or undoing consolidation requires the current owner epoch.")
            budget = min(config.read_bytes, config.response_bytes)
            if not 2048 <= budget <= 65536:
                raise InvalidRequest("Consolidation needs a response budget between 2048 and 65536 bytes.")
            with store.transaction() if writes else nullcontext():
                if writes:
                    store._lease(config.scope, Lease(args.session, args.epoch))
                else:
                    store.session(config.scope, args.session)
                try:
                    output = ExistingMemory(query_binding).invoke(
                        "consolidation", [], {**request, "scope": query_binding.workspace, "budget": budget},
                        time.monotonic() + query_binding.timeout_seconds)
                except SourceFailure:
                    raise Unavailable("The original memory service could not complete consolidation.") from None
            print(canonical(output))
        elif args.command == "experience-rule-draft":
            from .rule_draft import rule_draft

            store.session(config.scope, args.session)
            print(canonical(rule_draft(query_binding, args.candidate, args.evidence_version,
                                       args.target, args.rule, since=args.since, until=args.until,
                                       reconsider=args.reconsider,
                                       budget=min(config.read_bytes, config.response_bytes))))
        elif args.command in {"experience-review", "experience-reject"}:
            from .experience import reject_experience, review_experiences
            window = {"since": args.since, "until": args.until}
            if args.command == "experience-review":
                store.session(config.scope, args.session)
                output = review_experiences(query_binding, **window, reconsider=args.reconsider,
                                            offset=args.offset, limit=args.limit,
                                            budget=min(config.read_bytes, config.response_bytes))
            else:
                # Hold the task lease across the write to the existing memory store.
                with store.transaction():
                    store._lease(config.scope, Lease(args.session, args.epoch))
                    output = reject_experience(query_binding, args.candidate,
                                               args.evidence_version, args.reason, **window)
            print(canonical(output))
        elif args.command=='query':
            try:
                raw=args.input.read_bytes() if args.input else sys.stdin.buffer.read(65537)
                if len(raw)>65536:raise ValueError()
                request=json.loads(raw)
            except (OSError,ValueError,UnicodeError):raise InvalidRequest('Query input must be a bounded JSON object') from None
            if not isinstance(request,dict):raise InvalidRequest('Query input must be a JSON object')
            request={'budget':config.response_bytes,**request}
            tools=ContextTools(store,config.scope,args.session,max_read_bytes=config.read_bytes,query_binding=query_binding)
            print(canonical(tools.call('context.query',request)))
        elif args.command=='timeline':
            tools=ContextTools(store,config.scope,args.session,max_read_bytes=config.read_bytes)
            fields=('limit','offset','native_session','file','role','origin','error_category','since','until','around')
            print(canonical(tools.call('context.timeline',{**{key:getattr(args,key) for key in fields if getattr(args,key) is not None},'budget':config.response_bytes})))
        elif args.command=='timeline-reindex':
            from .timeline import reindex
            print(canonical(reindex(store,config.scope,Lease(args.session,args.epoch),limit=args.limit)))
        elif args.command=='workscene':
            from .workscene import WorkScene
            from .models import Ref
            try:
                raw=args.input.read_bytes() if args.input else sys.stdin.buffer.read(2*1024*1024+1)
                data=json.loads(raw) if raw.strip() else {}
                if not isinstance(data,dict) or len(raw)>2*1024*1024:raise ValueError()
            except (OSError,ValueError,UnicodeError):raise InvalidRequest('Workscene input requires a bounded JSON object') from None
            if args.operation in {'read','error-history'}:
                value=ContextTools(store,config.scope,args.session,max_read_bytes=config.read_bytes).call('context.error_history' if args.operation=='error-history' else 'context.workscene',{'budget':config.response_bytes,**data})
                print(canonical(value));return
            else:
                if args.epoch is None:raise InvalidRequest('Host workscene operations require an explicit lease epoch')
                scene=WorkScene(store,config.scope,Lease(args.session,args.epoch),config.workspace)
                if 'source' in data:
                    try:data['source']=Ref(**data['source'])
                    except (ValueError,TypeError):raise InvalidRequest('Workscene source requires an exact Ref') from None
                if args.operation=='attempt':
                    from .error_history import ErrorHistory
                    scene=ErrorHistory(store,config.scope,Lease(args.session,args.epoch),config.workspace)
                operation={'timed-out':'timed_out','export':'export_state'}.get(args.operation,args.operation)
                try:value=getattr(scene,operation)(**data)
                except TypeError:raise InvalidRequest('Workscene operation arguments are invalid') from None
                if isinstance(value,Ref):value=asdict(value)
            print(canonical({'operation':args.operation,'result':value}))
        elif args.command == "event-import":
            from .event_bridge import import_events
            print(canonical(import_events(store,config,Lease(args.session,args.epoch),args.memory_home,
                args.client,args.native_session,offset=args.offset,limit=args.limit,budget=config.response_bytes)))
        elif args.command == "redact-evidence":
            from .redacted_evidence import redact_evidence
            from .models import Ref
            try:
                source = Ref(**json.loads(args.source_ref))
            except (ValueError, TypeError, RecursionError):
                raise InvalidRequest('Source reference must be an exact reference JSON object.') from None
            print(canonical(redact_evidence(store, config.scope, Lease(args.session,args.epoch), source, args.role)))
        elif args.command == "directory-sync":
            from .directory_sync import DirectorySync
            output = DirectorySync(store, config, Lease(args.session, args.epoch)).sync(root=args.root, resume=args.resume,
                step=args.step, **{key: getattr(args, key) for key in ("max_files", "max_depth", "max_file_bytes", "max_directory_entries")})
            print(canonical(output))
        elif args.command == "directory-status":
            tools = ContextTools(store, config.scope, args.session, max_read_bytes=config.read_bytes)
            print(canonical(tools.call("context.directory_status", {"run": args.run, "offset": args.offset,
                "limit": args.limit, "budget": config.response_bytes})))
        elif args.command == "directory-memory-enqueue":
            from .directory_memory import enqueue_directory
            print(canonical(enqueue_directory(store, config, Lease(args.session, args.epoch), args.run, args.memory_home,
                code=args.memory_code, offset=args.offset, limit=args.limit, budget=config.response_bytes)))
        elif args.command == "web-cache-store":
            from .web_cache import save_observation
            try:
                with args.input.open("rb") as stream:
                    raw = stream.read(16385)
                if len(raw) > 16384:
                    raise ValueError()
                observation = json.loads(raw)
            except (ValueError, RecursionError):
                raise InvalidRequest("Web observation input must be valid JSON within 16 KiB.") from None
            print(canonical(save_observation(store, config.scope, Lease(args.session, args.epoch), observation,
                                             budget=config.response_bytes)))
        elif args.command == "web-cache-get":
            tools = ContextTools(store, config.scope, args.session, max_read_bytes=config.read_bytes)
            print(canonical(tools.call("context.web_cache", {"source": args.source, "url": args.url,
                                                             "refresh": args.refresh,
                                                             "budget": min(config.response_bytes, config.read_bytes)})))
        elif args.command == "result-search":
            try:
                ref = json.loads(args.result_ref)
            except (ValueError, RecursionError):
                raise InvalidRequest("Result reference must be JSON.") from None
            tools = ContextTools(store, config.scope, args.session, max_read_bytes=config.read_bytes)
            print(canonical(tools.call("context.result_search", {"result_ref": ref, "query": args.query,
                "limit": args.limit, "offset": args.offset, "budget": config.response_bytes,
                "context_chars": args.context_chars, "window_limit": args.window_limit})))
        elif args.command in ("search-indexed", "index-status", "index-rebuild"):
            if args.command == "index-rebuild":
                output = store.rebuild_index(config.scope, Lease(args.session, args.epoch))
            else:
                tools = ContextTools(store, config.scope, args.session, max_read_bytes=config.read_bytes)
                output = tools.call("context.index_status", {}) if args.command == "index-status" else tools.call("context.search_indexed", {
                    "query": args.query, "limit": args.limit, "offset": args.offset, "budget": config.response_bytes,
                    "context_chars": args.context_chars, "window_limit": args.window_limit})
            print(canonical(output))
        elif args.command == "read-heat":
            tools = ContextTools(store, config.scope, args.session, max_read_bytes=config.read_bytes)
            print(canonical(tools.call("context.read_heat", {
                "snapshot": args.snapshot, "offset": args.offset, "limit": args.limit,
                "budget": min(config.read_bytes, config.response_bytes)})))
        elif args.command == "batch-read":
            from .accounting import HostAccounting
            try:
                with (open(args.calls, "rb") if args.calls != "-" else __import__("contextlib").nullcontext(sys.stdin.buffer)) as stream:
                    raw = stream.read(131073)
                if len(raw) > 131072:
                    raise InvalidRequest("Batch input exceeds 128 KiB.")
                calls = json.loads(raw)
            except (ValueError, RecursionError):
                raise InvalidRequest("Batch calls must be a JSON array.") from None
            tools = ContextTools(store, config.scope, args.session, max_read_bytes=config.read_bytes)
            meter = HostAccounting(store, config.scope, args.session)
            print(canonical(meter.call_tool(tools, "context.batch", {
                "calls": calls, "concurrency": args.concurrency,
                "per_call_bytes": args.per_call_bytes, "budget": config.response_bytes})))
        elif args.command == "extract-result":
            try:
                source, options = json.loads(args.source), json.loads(args.options)
            except (ValueError, RecursionError):
                raise InvalidRequest("Extraction source/options must be JSON.") from None
            tools = ContextTools(store, config.scope, args.session, max_read_bytes=config.read_bytes)
            print(canonical(tools.call("context.extract", {"source": source, "kind": args.kind, "options": options,
                                                          "limit": args.limit, "budget": config.response_bytes})))
        elif args.command == "capture-result":
            from .tool_results import OutputRecorder
            try:
                if args.result == "-":
                    data = json.load(sys.stdin)
                else:
                    # Binary read preserves CRLF inside encoded stream fields.
                    data = json.loads(Path(args.result).read_bytes())
            except (ValueError, RecursionError):
                raise InvalidRequest("Malformed observed-result JSON.") from None
            result = OutputRecorder(store, config.scope, Lease(args.session, args.epoch), config.workspace, ignore=config.ignore).capture(
                data, budget=config.response_bytes, intent=args.intent)
            print(canonical(result))
        elif args.command == "client-prepare":
            from .native_clients import prepare_clients
            result = prepare_clients(store, config, Lease(args.session, args.epoch), args.snapshot, args.output,
                                      clients=args.client or ("codex", "claude"), memory_command=args.memory_command,
                                      task_memory_command=args.task_memory_command,
                                      native_memory_clients=args.native_memory_client,query_binding=args.query_binding,context_assistance=args.context_assistance)
            print(canonical(result))
        elif args.command.startswith("bridge-"):
            from .bridge import BridgeEvidence
            bridge = BridgeEvidence(store, config.scope, args.session, validator=args.bridge_validator)
            if args.command == "bridge-import":
                output = bridge.import_bundle(args.bundle, args.bridge_scope, Lease(args.session, args.epoch))
            elif args.command == "bridge-packet":
                output = bridge.packet(args.snapshot, budget=packet_budget,
                                       prefer_frequent_reads=args.prefer_frequent_reads)
                if query_binding is not None:
                    from .profile_brief import attach_profile
                    output=attach_profile(output,query_binding,output['context']['task'],packet_budget)
            else:
                output = bridge.receipt(args.snapshot, args.receipt)
            print(canonical(output))
        elif args.command in ('brief','explain'):
            try:requested=json.loads(args.requested)
            except ValueError:raise InvalidRequest('Requested refs must be JSON') from None
            tools = ContextTools(store, config.scope, args.session, max_read_bytes=config.read_bytes, query_binding=query_binding)
            options = {'snapshot':args.snapshot,'budget':packet_budget,'offset':args.offset,'limit':args.limit,
                       'trace_offset':args.trace_offset,'trace_limit':args.trace_limit,'requested':requested}
            if getattr(args, 'offline', False):
                options.update(queue_offset=args.queue_offset, queue_limit=args.queue_limit)
                output = tools.call("context.offline_brief", options)
            else:
                output = tools.call("context." + args.command, options)
            if args.state_output:
                from .layered import export_state
                export_state(output,args.state_output)
            print(canonical(output))
        elif args.command in ('recover','resume-bind','resume-offer','resume-accept','resume-abort','intent-project'):
            from .recovery import Recovery
            recovery=Recovery(store,config,args.session)
            if args.command=='recover':
                if args.snapshot is not None and args.version is not None:
                    output=recovery.preview(args.snapshot,args.version,budget=packet_budget,client=args.client,native_session=args.native_session)
                else:output=recovery.candidates(snapshot=args.snapshot,version=args.version,client=args.client,native_session=args.native_session,offset=args.offset,limit=args.limit)
            elif args.command=='resume-bind':output=recovery.bind(args.snapshot,args.version,Lease(args.session,args.epoch),client=args.client,native_session=args.native_session,binding_id=args.binding_id)
            elif args.command=='resume-offer':
                try:target=Target(**json.loads(args.target))
                except (ValueError,TypeError):raise InvalidRequest('Target must be a JSON session/provider/model object') from None
                output=recovery.offer(args.snapshot,args.version,Lease(args.session,args.epoch),target,operation_id=args.operation_id,budget=packet_budget)
            elif args.command=='resume-accept':
                from .bridge import _read, _json, MAX_LEDGER
                output=recovery.accept(args.operation_id,_json(_read(args.receipt,MAX_LEDGER)))
            elif args.command=='resume-abort':
                h=recovery._offer(args.operation_id)['handoff'];store.abort(config.scope,h,Lease(args.session,args.epoch));output={'status':'aborted','offer_id':args.operation_id,'handoff':h}
            else:
                from .intent import project_native_user
                output=project_native_user(args.memory_home,config.workspace,args.client,args.native_session,args.event_id,start=args.start,end=args.end,output=args.output)
            print(canonical(output))
        elif args.command in ("deliverable-register", "deliverables", "deliverable"):
            from .deliverables import register
            if args.command in ("deliverable-register","deliverable"):
                try:
                    with args.input.open('rb') as stream:raw=stream.read(65537)
                    if len(raw)>65536:raise ValueError()
                    options=json.loads(raw)
                    if not isinstance(options,dict):raise ValueError()
                except (OSError,ValueError):raise InvalidRequest('Product input must be a bounded JSON object') from None
            if args.command=='deliverable-register':
                output=register(store,config.scope,Lease(args.session,args.epoch),config.workspace,options,allowed_roots=args.allow_root)
            else:
                reader=ContextTools(store,config.scope,args.session,max_read_bytes=config.read_bytes,query_binding=query_binding)
                reader.config_view=config.describe()
                output=reader.call('context.'+args.command,{'offset':args.offset,'limit':args.limit} if args.command=='deliverables' else options)
            print(canonical(output))
        elif args.command in ("file-history", "history-details", "history-neighborhood"):
            if args.command == "file-history": options={'file':args.file}
            else:
                try:
                    with args.input.open('rb') as stream:raw=stream.read(32769)
                    if len(raw)>32768:raise ValueError()
                    options=json.loads(raw)
                    if not isinstance(options,dict):raise ValueError()
                except (OSError,ValueError):raise InvalidRequest('History navigation requires bounded JSON options') from None
            reader=ContextTools(store,config.scope,args.session,max_read_bytes=config.read_bytes,query_binding=query_binding)
            print(canonical(reader.call('context.'+args.command.replace('-','_'),options)))
        elif args.command.startswith("bookmark-"):
            from .bookmarks import Bookmarks
            bookmarks = Bookmarks(store, config, args.session)
            if args.command == "bookmark-set":
                output = bookmarks.set(args.name, args.snapshot, Lease(args.session, args.epoch), args.query)
            elif args.command == "bookmark-open":
                output = bookmarks.open(args.name, budget=packet_budget)
            elif args.command == "bookmark-remove":
                output = bookmarks.remove(args.name, Lease(args.session, args.epoch))
            else:
                output = bookmarks.list(prefix=args.prefix, offset=args.offset, limit=args.limit, budget=config.response_bytes)
                if args.completion:
                    print(json.dumps([entry["name"] for entry in output["entries"]], ensure_ascii=True))
                    return
            print(canonical(output))
        elif args.command in ("status", "checkpoints", "search", "continue"):
            from .navigation import TaskNavigation
            navigation = TaskNavigation(store, config, args.session)
            if args.command == "status":
                status_tools = ContextTools(store,config.scope,args.session,max_read_bytes=config.read_bytes,query_binding=query_binding)
                status_tools.config_view = config.describe()
                output = status_tools.call('context.status',{'budget':min(config.read_bytes,config.response_bytes)})
            elif args.command == "checkpoints":
                output = navigation.checkpoints(offset=args.offset, limit=args.limit)
            elif args.command == "search":
                output = navigation.search(args.query, limit=args.limit)
            else:
                output = navigation.resume(args.snapshot, budget=packet_budget,
                                           prefer_frequent_reads=args.prefer_frequent_reads,
                                           query_binding_path=args.query_binding.resolve() if args.query_binding else None)
                if query_binding is not None:
                    from .profile_brief import attach_profile
                    output=attach_profile(output,query_binding,output['context']['task'],packet_budget)
            print(canonical(output))
        else:
            import asyncio
            from .mcp_server import run_stdio
            mcp_tools = ContextTools(store, config.scope, args.session, max_read_bytes=config.read_bytes,query_binding=query_binding)
            mcp_tools.config_view = config.describe()
            asyncio.run(run_stdio(mcp_tools))


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        if args.command in ("host-hook", "existing-memory-hook"):
            from .native_clients import host_hook, existing_memory_hook
            try:
                raw = sys.stdin.buffer.read(1048577)
                if len(raw) > 1048576:
                    raise InvalidRequest("Native event exceeds 1 MiB.")
                payload = json.loads(raw)
            except (ValueError, RecursionError):
                raise InvalidRequest("Invalid native event JSON.") from None
            if args.command == "host-hook":
                result = host_hook(args.registry, args.client, payload, native_memory=args.native_memory)
            else:
                result = existing_memory_hook(args.home, payload, mode=args.mode)
            print(canonical(result), flush=True)
            if args.command == "host-hook":
                from .native_assistance import acknowledge_return
                acknowledge_return(args.registry, args.client, payload, result)
        elif args.command == "client-apply":
            from .native_clients import apply_project_clients
            print(canonical(apply_project_clients(args.layout, args.workspace, clients=args.client or ("codex", "claude"), adopt_layout=args.adopt_layout)))
        elif args.command == "client-remove":
            from .native_clients import remove_project_clients
            print(canonical(remove_project_clients(args.workspace, clients=args.client or ("codex", "claude"))))
        elif args.command == "memory-save":
            from .explicit_memory import save, read_record
            print(canonical(save(args.home, args.workspace, read_record(args.input))))
        elif args.command == "client-toggle":
            from .native_clients import toggle_client
            print(canonical(toggle_client(args.registry, args.client, args.enabled)))
        elif args.command == "demo":
            print(canonical(demo()))
        elif args.command == "tools":
            print(canonical(ContextTools.definitions()))
        elif args.command == "backup-restore":
            from .maintenance import restore_backup
            print(canonical(restore_backup(args.backup, args.output)))
        elif args.command in PROJECT_COMMANDS:
            project_command(args)
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
