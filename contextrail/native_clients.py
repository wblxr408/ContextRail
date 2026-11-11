"""Explicit local client bindings and a single existing-memory hook composition.

Generated files are reviewable configuration, not proof of native activation.
No transcript is opened, no model is invoked, and no memory daemon is installed.
"""

import copy
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid

from .bridge import BridgeEvidence
from .errors import AccessDenied, BudgetExceeded, Conflict, InvalidRequest, RailError, Unavailable
from .models import Lease, Scope, Target, canonical, identifier, integer, unicode_text
from .local_process import run_local
from .store import Store


CLIENTS = ("codex", "claude")
MAX_CONFIG = 2 * 1024 * 1024


def _read_json(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_CONFIG + 1)
    if len(raw) > MAX_CONFIG:
        raise InvalidRequest("Client configuration exceeds 2 MiB.")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidRequest("Client configuration must be valid UTF-8 JSON.") from None


def _argv(value):
    if not isinstance(value, list) or not value or len(value) > 64:
        raise InvalidRequest("Existing memory command must be a nonempty argv array.")
    for arg in value:
        if not isinstance(arg, str) or not arg or "\x00" in arg:
            raise InvalidRequest("Memory command arguments must be nonempty text.")
        unicode_text(arg)
    return value


def _write_json(path, value):
    path = Path(path)
    # Same-directory replacement makes a toggle visible as one complete document.
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix="rail-binding-", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(canonical(value).encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    try:
        delays = (0.01, 0.02, 0.04, 0.08, 0.16, 0.32, None)
        for delay in delays:
            try:
                os.replace(temporary, path)
                break
            except PermissionError as exc:
                if os.name != "nt" or exc.winerror not in {5, 32, 33}:
                    raise
                if delay is None:
                    raise Conflict("Client registry is occupied. Release the file before changing the binding.") from None
                time.sleep(delay)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_memory(memory):
    if memory is not None:
        if not isinstance(memory, dict) or set(memory) - {"command", "timeout_seconds"}:
            raise InvalidRequest("Invalid existing memory hook configuration.")
        _argv(memory.get("command"))
        timeout = memory.get("timeout_seconds", 18)
        if type(timeout) not in (int, float) or not 0 < timeout <= 20 or not math.isfinite(timeout):
            raise InvalidRequest("Memory timeout must be finite, positive and at most 20 seconds.")


def read_registry(path):
    registry = _read_json(path)
    if not isinstance(registry, dict) or registry.get("schema") != 1 or set(registry) - {"schema", "memory", "bindings"}:
        raise InvalidRequest("Unsupported native client registry.")
    if not isinstance(registry.get("bindings"), list):
        raise InvalidRequest("Registry requires explicit bindings.")
    _validate_memory(registry.get("memory"))
    for binding in registry["bindings"]:
        keys = {"client", "workspace", "store", "scope", "session", "base_snapshot", "owner", "enabled", "context_bytes", "hook_bytes"}
        if (not isinstance(binding, dict) or not keys <= binding.keys() or binding.keys() - keys - {"memory", "query_binding", "context_assistance", "native_session_binding"}
                or binding["client"] not in CLIENTS or type(binding["enabled"]) is not bool):
            raise InvalidRequest("Invalid native task binding.")
        _validate_memory(binding.get("memory"))
        if binding.get('native_session_binding','explicit') not in ('explicit','current_hook'):
            raise InvalidRequest('Native session binding must be explicit or current_hook.')
        if type(binding.get('context_assistance',False)) is not bool:
            raise InvalidRequest('Context assistance switch must be boolean.')
        if binding.get('context_assistance') and (not isinstance(binding.get('query_binding'),str) or not Path(binding['query_binding']).is_absolute()):
            raise InvalidRequest('Context assistance requires an absolute trusted query binding.')
        if not isinstance(binding["scope"], dict) or set(binding["scope"]) != {"tenant", "project", "branch", "task"}:
            raise InvalidRequest("Binding requires an exact scope object.")
        if not isinstance(binding["owner"], dict) or set(binding["owner"]) != {"session", "epoch"}:
            raise InvalidRequest("Binding requires an explicit owner lease.")
        Scope(**binding["scope"])
        Lease(**binding["owner"])
        identifier(binding["session"])
        identifier(binding["base_snapshot"])
        for key in ("workspace", "store"):
            if not isinstance(binding[key], str) or not Path(binding[key]).is_absolute():
                raise InvalidRequest("Binding paths must be absolute host configuration.")
        for key in ("context_bytes", "hook_bytes"):
            integer(binding[key], 1)
            if binding[key] > 16 * 1024 * 1024:
                raise InvalidRequest("Binding byte budgets cannot exceed 16 MiB.")
    return registry


def prepare_clients(store, config, lease, base_snapshot, output, *, clients=CLIENTS, memory_command=None,
                    context_bytes=None, hook_bytes=None, task_memory_command=None, native_memory_clients=(), query_binding=None, context_assistance=False):
    from dataclasses import asdict
    settings = config.context_settings()
    for key, override in (("context_bytes", context_bytes), ("hook_bytes", hook_bytes)):
        if override is not None:
            integer(override, 1)
            if override > 16 * 1024 * 1024:
                raise InvalidRequest("Binding byte budgets cannot exceed 16 MiB.")
            settings[key] = override
            settings["sources"][key] = "explicit"
    context_bytes, hook_bytes = settings["context_bytes"], settings["hook_bytes"]
    clients = tuple(clients)
    if query_binding is not None:
        from .query_sources import QueryBinding
        QueryBinding.load(query_binding,config.scope.task,config.workspace)
        query_binding=Path(query_binding).resolve()
    if not clients or len(set(clients)) != len(clients) or any(client not in CLIENTS for client in clients):
        raise InvalidRequest("Choose distinct supported clients.")
    if set(native_memory_clients) - set(clients):
        raise InvalidRequest("Native memory delivery must name one of the prepared clients.")
    output = Path(output).resolve()
    if output.exists():
        raise InvalidRequest("Generate into a new layout directory; existing configuration is never overwritten.")
    if context_assistance and query_binding is None:
        raise InvalidRequest("Context assistance requires an exact existing-memory query binding.")
    command = _argv(_read_json(memory_command)) if memory_command else None
    task_command = _argv(_read_json(task_memory_command)) if task_memory_command else None
    bridge = BridgeEvidence(store, config.scope, lease.session)
    snapshot = bridge.refresh(base_snapshot, lease)
    # A hard-context error must be reviewable before generating any hook files.
    bridge.packet(snapshot, budget=context_bytes)
    owner = store.session(config.scope, lease.session)
    registry = {"schema": 1, "memory": {"command": command, "timeout_seconds": 18} if command else None, "bindings": []}
    readers = []
    for client in clients:
        reader = Target("reader-" + client + "-" + config.scope.key[:16], owner.provider, "client-managed-model-unknown")
        readers.append(reader)
        registry["bindings"].append({"client": client, "workspace": str(config.workspace), "store": str(config.store),
            "scope": asdict(config.scope), "session": reader.session, "base_snapshot": base_snapshot,
            "owner": asdict(lease), "enabled": True, "context_bytes": context_bytes, "hook_bytes": hook_bytes})
        if context_assistance:
            registry["bindings"][-1].update(query_binding=str(query_binding), context_assistance=True)
        if task_command:
            registry["bindings"][-1]["memory"] = {"command": task_command, "timeout_seconds": 18}
    # Validate with the same consumer contract before membership/file publication.
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix="rail-client-stage-") as stage:
        stage = Path(stage)
        (stage / "registry.json").write_text(canonical(registry), encoding="utf-8")
        read_registry(stage / "registry.json")
        source_root = str(Path(__file__).resolve().parent.parent)
        (stage / "entry.py").write_text("# Generated entry points to one source tree; no service is copied.\n"
            + "import sys\nsys.path.insert(0," + repr(source_root) + ")\nfrom contextrail.cli import main\nraise SystemExit(main())\n", encoding="utf-8")
        profiles = {}
        for client, reader in zip(clients, readers):
            args = ["-u", str(output / "entry.py"), "mcp", "--workspace", str(config.workspace), "--store", str(config.store),
                    "--project-id", config.scope.project, "--tenant", config.scope.tenant, "--task", config.scope.task,
                    "--session", reader.session]
            if config.config:
                args += ["--config", str(config.config)]
            if config.defaults and config.defaults_explicit:
                args += ["--defaults", str(config.defaults)]
            for key in ("read_bytes", "response_bytes", "context_bytes"):
                if settings["sources"][key] == "explicit":
                    args += ["--" + key.replace("_", "-"), str(settings[key])]
            if query_binding is not None:args+=['--query-binding',str(query_binding)]
            server = {"command": sys.executable, "args": args}
            hook_args = [sys.executable.replace("\\", "/"), "-u", (output / "entry.py").as_posix(), "host-hook", "--registry",
                         (output / "registry.json").as_posix(), "--client", client]
            if client in native_memory_clients:
                hook_args.append("--native-memory")
            # Both native command-hook APIs use a shell command string.
            hook_command = subprocess.list2cmdline(hook_args) if os.name == "nt" else __import__("shlex").join(hook_args)
            handler = {"type": "command", "command": hook_command, "timeout": 25}
            if client == "codex":
                handler["additionalContextLimit"] = 12000
                name = "codex.config.toml"
                (stage / name).write_text("[mcp_servers.contextrail]\ncommand = " + json.dumps(server["command"]) + "\nargs = " +
                                          json.dumps(args, ensure_ascii=False) + "\n", encoding="utf-8")
                hook_name = "codex.hooks.json"
            else:
                name = "claude.mcp.json"
                (stage / name).write_text(canonical({"mcpServers": {"contextrail": server}}), encoding="utf-8")
                hook_name = "claude.settings.json"
            hooks = {"hooks": {"UserPromptSubmit": [{"hooks": [handler]}]}}
            if context_assistance:
                for event in ("PostCompact", "SessionStart", "PostToolUse", "PreToolUse"):
                    # One native invocation; it dispatches the actual event from stdin.
                    event_handler = copy.deepcopy(handler)
                    if event == 'PostCompact': event_handler.pop('additionalContextLimit',None)
                    hooks["hooks"][event] = [{"hooks": [event_handler]}]
            (stage / hook_name).write_text(canonical(hooks), encoding="utf-8")
            profiles[client] = {"mcp_config": str(output / name), "hook_config": str(output / hook_name),
                                "reader": reader.session, "memory_delivery": "existing_native_hook" if client in native_memory_clients else "composed_wrapper",
                                "hook_activation": "native_review_required" if client == "codex" else "prepared_not_activated"}
        for reader in readers:
            store.register_reader(config.scope, lease, reader)
        # The directory appears only once all generated files are complete.
        os.rename(stage, output)
    return {"registry": str(output / "registry.json"), "entry": str(output / "entry.py"), "source_root": source_root,
            "snapshot": snapshot, "base_snapshot": base_snapshot, "clients": profiles,
            "configuration": settings, "state": "prepared_not_deployed"}


def toggle_client(path, client, enabled):
    registry = read_registry(path)
    matches = [binding for binding in registry["bindings"] if binding["client"] == client]
    if not matches:
        raise InvalidRequest("No binding exists for this client.")
    for binding in matches:
        binding["enabled"] = enabled
    _write_json(path, registry)
    return {"client": client, "enabled": enabled, "memory_hook": "unchanged"}


def apply_project_clients(layout, workspace, *, clients=CLIENTS, adopt_layout=None):
    """Apply the explicitly generated owned entries as one rollback-capable set."""
    from .client_config import change_clients
    return change_clients(workspace, clients, layout=layout, adopt_layout=adopt_layout)


def remove_project_clients(workspace, *, clients=CLIENTS):
    """Remove only unchanged entries recorded in this workspace's receipt."""
    from .client_config import change_clients
    return change_clients(workspace, clients)


def existing_memory_hook(home, payload, *, mode="hybrid"):
    """Reuse the installed Hook formatter and its existing shared main runtime.

    Explicit text mode is useful for a native 30-second Hook deadline. It
    does not change global retrieval defaults, spawn a private model service,
    or claim semantic ranking when only keyword/graph retrieval was requested.
    """
    if mode not in {"hybrid", "text"}:
        raise InvalidRequest("Choose hybrid or explicit text retrieval.")
    home = Path(home).resolve()
    application = home / "app"
    if not (application / "memory_integration.py").is_file() or not (application / "resource_client.py").is_file():
        raise Unavailable("The existing main-memory Hook and shared runtime are required.")
    sys.path.insert(0, str(application))
    try:
        from memory_integration import hook_context
        from resource_client import RemoteRuntime
        runtime = RemoteRuntime(home)
        class HookView:
            @property
            def db(self):
                return runtime.db

            def recall(self, query, **options):
                return runtime.recall(query, mode=mode, **options)
        try:
            try:
                return hook_context(HookView(), payload)
            except (RuntimeError, ConnectionError, TimeoutError) as exc:
                raise Unavailable("The existing shared main-memory runtime could not complete this Hook.") from exc
        finally:
            runtime.close()
    finally:
        sys.path.pop(0)


def _memory_hook(memory, payload):
    if memory is None:
        return {}, "not_configured"
    try:
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        completed = run_local(memory["command"], input=canonical(payload).encode("utf-8"),
                              env=env, timeout=memory.get("timeout_seconds", 18))
        if completed.stderr:
            print(completed.stderr[:4096].decode("utf-8", errors="replace").rstrip(), file=sys.stderr)
        if completed.returncode:
            return {"systemMessage": "The existing memory hook failed; current task context is evaluated separately."}, "failed"
        if len(completed.stdout) > MAX_CONFIG:
            raise ValueError()
        result = json.loads(completed.stdout)
        if not isinstance(result, dict):
            raise ValueError()
        return result, "returned"
    except subprocess.TimeoutExpired:
        return {"systemMessage": "The existing memory hook timed out."}, "timed_out"
    except (OSError, ValueError, UnicodeError, RecursionError):
        return {"systemMessage": "The existing memory hook is unavailable or returned invalid JSON."}, "unavailable"


def host_hook(path, client, payload, *, native_memory=False):
    if isinstance(payload, dict) and payload.get("hook_event_name") != "UserPromptSubmit":
        from .native_assistance import assist
        return assist(path, client, payload)
    if isinstance(payload, dict):
        from .native_assistance import assist
        recovery = assist(path, client, payload, optional=True)
        if recovery is not None:
            return recovery
    if client not in CLIENTS or not isinstance(payload, dict) or payload.get("hook_event_name") != "UserPromptSubmit":
        raise InvalidRequest("Only the configured clients' UserPromptSubmit event is supported.")
    identifier(payload.get("session_id"))
    if not isinstance(payload.get("prompt"), str) or not payload["prompt"].strip():
        raise InvalidRequest("UserPromptSubmit requires the native prompt text.")
    unicode_text(payload["prompt"])
    if not isinstance(payload.get("cwd"), str) or not Path(payload["cwd"]).is_absolute():
        raise InvalidRequest("Native cwd must be an absolute path.")
    cwd = Path(payload["cwd"]).resolve()
    turn = payload.get("turn_id")
    if turn is not None:
        identifier(turn)
    registry = read_registry(path)
    matches = [binding for binding in registry["bindings"] if binding["client"] == client and
               cwd.is_relative_to(Path(binding["workspace"]).resolve())]
    if len(matches) > 1:
        raise InvalidRequest("Native cwd matches multiple explicit task bindings; choose one in the registry.")
    memory = registry.get("memory")
    if matches and matches[0]["enabled"] and matches[0].get("memory"):
        memory = matches[0]["memory"]
    original, memory_status = ({}, "provided_by_existing_native_hook_not_observed") if native_memory else _memory_hook(memory, payload)
    if not matches or not matches[0]["enabled"] or original.get("decision") == "block" or original.get("continue") is False:
        return original
    return _task_context(matches[0], payload, client, original, memory_status)


def _task_context(binding, payload, client, original, memory_status, *, recovery=None):
    cwd = Path(payload["cwd"]).resolve()
    turn = payload.get("turn_id")
    try:
        scope, owner = Scope(**binding["scope"]), Lease(**binding["owner"])
        if not Path(binding["store"]).is_file():
            raise Unavailable("The bound Rail store is missing; do not reconstruct task state as an empty database.")
        with Store(binding["store"]) as store:
            bridge = BridgeEvidence(store, scope, binding["session"])
            sid = bridge.refresh(binding["base_snapshot"], owner)
            if recovery is None:
                returned=store.db.execute("SELECT metadata FROM events WHERE scope=? AND kind='native_context.returned' AND json_extract(metadata,'$.client')=? AND json_extract(metadata,'$.native_session')=? ORDER BY sequence DESC LIMIT 1",
                    (scope.key,client,payload['session_id'])).fetchone()
                if returned and json.loads(returned['metadata']).get('snapshot')==sid:
                    return original
                delivered=store.db.execute("SELECT metadata FROM events WHERE scope=? AND kind='native_compact.returned' AND json_extract(metadata,'$.client')=? AND json_extract(metadata,'$.native_session')=? ORDER BY sequence DESC LIMIT 1",
                    (scope.key,client,payload['session_id'])).fetchone()
                if delivered:
                    attempt=json.loads(delivered['metadata']).get('attempt')
                    prepared=store.db.execute("SELECT metadata FROM events WHERE scope=? AND kind='native_compact.prepared' AND json_extract(metadata,'$.attempt')=? ORDER BY sequence DESC LIMIT 1",(scope.key,attempt)).fetchone()
                    if prepared and json.loads(prepared['metadata']).get('snapshot')==sid:
                        # SessionStart already returned this exact task after
                        # compaction. Keep the original memory hook's result,
                        # rather than appending the same task again on submit.
                        return original
            task = bridge.packet(sid, budget=binding["context_bytes"],intent_details=False)
        specific = original.get("hookSpecificOutput", {})
        if not isinstance(specific, dict) or not isinstance(specific.get("additionalContext", ""), str):
            raise InvalidRequest("Existing memory hook context has an invalid shape.")
        history = specific.get("additionalContext", "")
        from .learning_origin import origin
        from .loss import report
        context = {"schema": "contextrail.native-context/v1", "task": task,
                   "memory": {"status": memory_status, "text": "", "truncated": bool(history), "trust": "historical_data_not_current_policy"},
                   "origin": {**origin('native-context:'+client+':'+payload['session_id']+':'+str(turn or 'unknown'),
                                       [task['origin']['source_event']], 'native_context', False),
                              "client": client, "native_session": payload["session_id"], "turn_id": turn,
                              "event": payload["hook_event_name"], "cwd": str(cwd)},
                   "history_control": "not_controlled", "history_replaced": False}
        if recovery is not None: context['recovery']=recovery
        result = copy.deepcopy(original)
        inline_characters = 10000 if client == "claude" else None
        def render(length):
            context["memory"].update(text=history[:length], truncated=length < len(history))
            observed=memory_status=='returned'
            context['loss']={'memory':report('native_memory_hook','utf8_bytes',total=len(history.encode('utf-8')) if observed else None,
                selected=len(history[:length].encode('utf-8')) if observed else None,
                truncated=len(history.encode('utf-8'))-len(history[:length].encode('utf-8')) if observed else None,unknown=0 if observed else None,
                scope='observed_hook_result_only_not_complete_history',reasons={'hook_status':memory_status},recover={'reader':'basic-memory.read_note','project':'main'})}
            result["hookSpecificOutput"] = {"hookEventName": payload["hook_event_name"], "additionalContext": canonical(context)}
            return len(canonical(result).encode("utf-8"))
        def fits(length):
            size = render(length)
            # Claude uses JavaScript string length and sends only a file/preview
            # above this native limit. Count UTF-16 units, including emoji pairs.
            chars = len(result["hookSpecificOutput"]["additionalContext"].encode("utf-16-le")) // 2
            return size <= binding["hook_bytes"] and (inline_characters is None or chars <= inline_characters)
        if not fits(0):
            # The full required intent remains in START/evidence. Avoid repeating
            # its source inventory in the transport envelope, then let the
            # original compiler omit optional pages within native limits.
            def compact(value):
                intent=value.get('resume_intent',{})
                if intent.get('status')=='anchored':
                    value['resume_intent']={key:intent[key] for key in ('status','effective_request','next_action','coverage','ref')}
                # Compiler already enforced exact source/currentness. The native
                # inline transport keeps every selected body and exact Ref;
                # repetitive per-row display metadata stays in context.packet.
                for key in ('stable_evidence','evidence'):
                    value['context'][key]=[{k:v for k,v in row.items() if k in {'ref','text','required','trust'}}
                        for row in value['context'].get(key,[])]
                value['transport_view']={'omitted':'per_evidence_display_metadata_only','full_packet':{
                    'tool':'context.packet','arguments':{'snapshot':sid,'budget':binding['context_bytes']}}}
                return value
            context['task']=compact(task)
            task_budget=binding['context_bytes']
            while not fits(0):
                serialized=canonical(result);inline=result['hookSpecificOutput']['additionalContext']
                overflow=max(len(serialized.encode('utf-8'))-binding['hook_bytes'],
                    len(inline.encode('utf-16-le'))//2-(inline_characters or 2**63-1),256)
                task_budget-=overflow
                if task_budget<=0:break
                with Store(binding['store']) as store:
                    smaller=BridgeEvidence(store,scope,binding['session']).packet(sid,budget=task_budget,intent_details=False)
                task=smaller;context['task']=compact(smaller)
        if not fits(0):
            raise BudgetExceeded("Complete current task context does not fit the native hook byte/inline budget.")
        low, high = 0, len(history)
        while low < high:
            middle = (low + high + 1) // 2
            if fits(middle):
                low = middle
            else:
                high = middle - 1
        prepared_bytes = render(low)
        # Existing task audit only: an invocation receipt, not raw prompt,
        # transcript capture or proof that a model acted on this context.
        with Store(binding["store"]) as store:
            with store.transaction():
                store._lease(scope, owner)
                store._event(scope, "native_context.prepared", client=client, native_session=payload["session_id"],
                              turn_id=turn, base_snapshot=binding["base_snapshot"], snapshot=sid,
                              task_version=task['context']['task']['version'],
                              memory_status=memory_status, memory_chars=len(context["memory"]["text"]),
                              memory_truncated=context["memory"]["truncated"], returned_bytes=prepared_bytes,
                              selected_refs=[row['ref'] for row in task['context'].get('stable_evidence',[])+task['context'].get('evidence',[])],
                              loss={'task':task.get('loss'),'memory':context['loss']['memory']})
        return result
    except (RailError, OSError) as exc:
        code = exc.code if isinstance(exc, RailError) else "storage_error"
        return {"decision": "block", "reason": "ContextRail " + code + ": " + str(exc)[:240],
                "systemMessage": "Current task requirements are unavailable; reconcile the explicit binding/source before continuing."}
