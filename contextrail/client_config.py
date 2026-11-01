"""Project-owned config receipts over claude-mem's staged replacement core."""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import tomllib
import uuid

from .errors import AccessDenied, Conflict, InvalidRequest, RailError
from .claude_mem import replace_files

RECEIPT = '.context-rail/client-install.json'
MARKER = '\n# ContextRail project MCP; Hook trust remains native.\n'
LIMIT = 2 * 1024 * 1024


def read(path):
    if path.is_symlink():
        raise AccessDenied('Client configuration must not cross a symbolic link.')
    try:
        with path.open('rb') as stream:
            value = stream.read(LIMIT + 1)
    except FileNotFoundError:
        return None
    if len(value) > LIMIT:
        raise InvalidRequest('Client configuration exceeds 2 MiB.')
    return value


def object_json(raw):
    try:
        value = json.loads(raw) if raw is not None else {}
    except (ValueError, UnicodeError):
        raise InvalidRequest('Preserve invalid project JSON for manual reconciliation.') from None
    if not isinstance(value, dict):
        raise InvalidRequest('Project configuration must be an object.')
    return value


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def target(root, relative):
    path = root / relative
    current = path
    while current != root:
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            raise AccessDenied('Client configuration must not cross a link or junction.')
        current = current.parent
    if not path.resolve().is_relative_to(root):
        raise AccessDenied('Client configuration escapes the workspace.')
    return path


@contextmanager
def locked(root):
    directory = target(root, '.context-rail')
    directory.mkdir(exist_ok=True)
    path = directory / 'client-config.lock'
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise Conflict('Another client update owns client-config.lock. If its process exited, review and remove that lock explicitly.') from None
    try:
        os.write(fd, str(os.getpid()).encode('ascii'))
        yield
    finally:
        os.close(fd)
        path.unlink()


def change_clients(workspace, clients, *, layout=None):
    from .native_clients import CLIENTS, read_registry, _read_json
    root = Path(workspace).resolve()
    clients = tuple(clients)
    if not clients or len(set(clients)) != len(clients) or set(clients) - set(CLIENTS):
        raise InvalidRequest('Choose supported distinct clients.')
    registry = read_registry(Path(layout) / 'registry.json') if layout else None
    with locked(root):
        receipt_path = target(root, RECEIPT)
        original_receipt = read(receipt_path)
        receipt = object_json(original_receipt) if original_receipt else {'schema':1,'workspace':str(root),'clients':{}}
        if receipt.get('schema') != 1 or receipt.get('workspace') != str(root) or not isinstance(receipt.get('clients'), dict):
            raise Conflict('Client ownership receipt does not match this workspace.')
        plans = []
        for client in clients:
            owned = receipt['clients'].get(client, {})
            if registry:
                matches = [b for b in registry['bindings'] if b['client'] == client and Path(b['workspace']).resolve() == root]
                if len(matches) != 1:
                    raise InvalidRequest('Layout must bind exactly this client and workspace.')
                layout = Path(layout).resolve()
                if client == 'codex':
                    fragment = (layout/'codex.config.toml').read_text(encoding='utf-8')
                    parsed_fragment = tomllib.loads(fragment)
                    server = parsed_fragment.get('mcp_servers', {}).get('contextrail')
                    if parsed_fragment != {'mcp_servers':{'contextrail':server}} or not isinstance(server, dict):
                        raise InvalidRequest('Generated TOML must contain exactly the contextrail server.')
                    block = MARKER + fragment
                    hook_fragment = _read_json(layout/'codex.hooks.json')
                else:
                    server = _read_json(layout/'claude.mcp.json')['mcpServers']['contextrail']
                    block = None
                    hook_fragment = _read_json(layout/'claude.settings.json')
                desired_groups = hook_fragment['hooks']['UserPromptSubmit']
                if not isinstance(desired_groups, list) or any(not isinstance(g, dict) or not isinstance(g.get('hooks'), list) for g in desired_groups):
                    raise InvalidRequest('Generated hooks have an invalid shape.')
            else:
                server = block = None
                desired_groups = []
            path = target(root, '.codex/config.toml' if client == 'codex' else '.mcp.json')
            before = read(path)
            try:
                current = tomllib.loads(before.decode('utf-8')) if before is not None and client == 'codex' else object_json(before)
            except (ValueError, UnicodeError):
                raise InvalidRequest('Preserve invalid project TOML for manual reconciliation.') from None
            key = 'mcp_servers' if client == 'codex' else 'mcpServers'
            servers = current.get(key, {})
            if not isinstance(servers, dict):
                raise InvalidRequest('Existing MCP configuration must be an object.')
            installed = servers.get('contextrail')
            new_owned = {}
            after = before
            if 'server' in owned:
                if installed != owned['server']:
                    raise Conflict('Owned MCP entry was edited; preserve it for manual reconciliation.')
                if client == 'codex':
                    text = before.decode('utf-8')
                    old = owned.get('block')
                    if not old or text.count(old) != 1:
                        raise Conflict('Owned TOML block changed; preserve comments and reconcile manually.')
                    after = text.replace(old, block or '', 1).encode('utf-8')
                else:
                    if registry: servers['contextrail'] = server
                    else: servers.pop('contextrail')
                    after = encoded(current)
                if registry:
                    new_owned.update(server=server)
                    if block: new_owned['block'] = block
            elif installed is None and registry:
                if client == 'codex': after = (before or b'') + block.encode('utf-8')
                else:
                    current.setdefault(key, {})['contextrail'] = server
                    after = encoded(current)
                new_owned.update(server=server)
                if block: new_owned['block'] = block
            elif registry and installed != server:
                raise Conflict('An unowned contextrail MCP entry differs; no user entry was overwritten.')
            # Matching pre-existing entries are not silently claimed as ours.
            plans.append((path, before, after))
            hook_path = target(root, '.codex/hooks.json' if client == 'codex' else '.claude/settings.local.json')
            before = read(hook_path)
            current = object_json(before)
            hooks = current.setdefault('hooks', {})
            if not isinstance(hooks, dict): raise InvalidRequest('Existing hooks must be an object.')
            previous = object_json(before)
            old_events = dict(owned.get('event_groups', {}))
            if owned.get('groups'): old_events['UserPromptSubmit'] = owned['groups']
            wanted = hook_fragment['hooks'] if registry else {}
            if set(wanted) - {'UserPromptSubmit','SessionStart','PostCompact','PostToolUse','PreToolUse'}:
                raise InvalidRequest('Generated hook event is unsupported.')
            for event in dict.fromkeys([*old_events,*wanted]):
                groups = hooks.setdefault(event, [])
                if not isinstance(groups,list): raise InvalidRequest('Existing hook groups must be a list.')
                old_groups = old_events.get(event, [])
                for group in old_groups:
                    if groups.count(group) != 1:
                        raise Conflict('Owned hook was edited or duplicated; preserve it for manual reconciliation.')
                for group in old_groups: groups.remove(group)
                new_groups = []
                commands = [h.get('command') for g in groups if isinstance(g,dict) for h in g.get('hooks',[]) if isinstance(h,dict)]
                for group in wanted.get(event, []):
                    if not isinstance(group,dict) or not isinstance(group.get('hooks'),list):
                        raise InvalidRequest('Generated hook group is invalid.')
                    if group in groups: continue
                    if any(h.get('command') in commands for h in group['hooks']):
                        raise Conflict('An unowned hook uses the same command with different settings.')
                    groups.append(copy.deepcopy(group));new_groups.append(group)
                if new_groups:
                    if event=='UserPromptSubmit': new_owned['groups']=new_groups
                    else: new_owned.setdefault('event_groups',{})[event]=new_groups
            after = encoded(current) if current != previous else before
            plans.append((hook_path, before, after))
            if registry: receipt['clients'][client] = new_owned
            else: receipt['clients'].pop(client, None)
        receipt_bytes = encoded(receipt)
        if original_receipt is not None or registry:
            plans.append((receipt_path, original_receipt, receipt_bytes))
        changed = [(p,b,a) for p,b,a in plans if b != a]
        backups = target(root, '.context-rail/client-config-backups/' + uuid.uuid4().hex)
        if changed:
            backups.mkdir(parents=True)
            for path, before, _ in changed:
                if before is not None:
                    backup = backups / path.relative_to(root)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    backup.write_bytes(before)
            (backups/'restore-plan.json').write_bytes(encoded({'workspace':str(root),'files':[
                {'path':str(p.relative_to(root)),'previously_present':b is not None} for p,b,_ in changed]}))
            try:
                replace_files(root, changed)
            except RailError as exc:
                raise type(exc)(str(exc)+' Existing configuration backups and restore plan: '+str(backups)) from None
        return {'workspace':str(root), 'applied':[str(p) for p,_,_ in changed],
            'unchanged':[str(p) for p,b,a in plans if b == a], 'backup_dir':str(backups) if changed else None,
            'receipt':str(receipt_path), 'operation':'apply' if registry else 'remove',
            'activation':{c:'native_hook_review_required' if c == 'codex' else 'new_session_or_native_project_approval_required' for c in clients}}
