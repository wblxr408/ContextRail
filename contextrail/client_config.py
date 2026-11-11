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


def legacy_ownership(root, layout, client):
    """Explicit host-supplied old layout is evidence, never a name-based claim."""
    from .native_clients import read_registry, _read_json
    layout = Path(layout).resolve()
    bindings = read_registry(layout/'registry.json')['bindings']
    if len([b for b in bindings if b['client']==client and Path(b['workspace']).resolve()==root]) != 1:
        raise InvalidRequest('Legacy layout must bind exactly this client and workspace.')
    if client=='codex':
        fragment=(layout/'codex.config.toml').read_bytes().decode('utf-8')
        value=tomllib.loads(fragment)
        server=value.get('mcp_servers',{}).get('contextrail')
        if not isinstance(server,dict) or value!={'mcp_servers':{'contextrail':server}}:
            raise InvalidRequest('Legacy TOML must contain exactly the generated server.')
        raw=read(target(root,'.codex/config.toml'))
        text=raw.decode('utf-8') if raw is not None else ''
        current=tomllib.loads(text)
        block=MARKER+fragment if MARKER+fragment in text else fragment
        if current.get('mcp_servers',{}).get('contextrail')!=server or text.count(block)!=1:
            raise Conflict('Legacy MCP entry differs from the supplied layout; no configuration was claimed.')
        owned={'server':server,'block':block}
        hooks=_read_json(layout/'codex.hooks.json')['hooks']
        current_hooks=object_json(read(target(root,'.codex/hooks.json'))).get('hooks',{})
    else:
        server=_read_json(layout/'claude.mcp.json')['mcpServers']['contextrail']
        if object_json(read(target(root,'.mcp.json'))).get('mcpServers',{}).get('contextrail')!=server:
            raise Conflict('Legacy MCP entry differs from the supplied layout; no configuration was claimed.')
        owned={'server':server}
        hooks=_read_json(layout/'claude.settings.json')['hooks']
        current_hooks=object_json(read(target(root,'.claude/settings.local.json'))).get('hooks',{})
    if not isinstance(hooks,dict) or set(hooks)-{'UserPromptSubmit','SessionStart','PostCompact','PostToolUse','PreToolUse'}:
        raise InvalidRequest('Legacy hook events are invalid.')
    for event,groups in hooks.items():
        current=current_hooks.get(event,[]) if isinstance(current_hooks,dict) else []
        if not isinstance(groups,list) or not groups or not isinstance(current,list) or any(current.count(g)!=1 for g in groups):
            raise Conflict('Legacy Hook differs or is duplicated; no configuration was claimed.')
        if event=='UserPromptSubmit':owned['groups']=copy.deepcopy(groups)
        else:owned.setdefault('event_groups',{})[event]=copy.deepcopy(groups)
    return owned


def update_hook_groups(groups, owned, desired):
    """Replace managed groups in their original slots while retaining user order."""
    positions = set()
    for group in owned:
        matches = [index for index, item in enumerate(groups) if item == group]
        if len(matches) != 1 or matches[0] in positions:
            raise Conflict('Owned hook was edited or duplicated; preserve it for manual reconciliation.')
        positions.add(matches[0])
    unowned = [group for index, group in enumerate(groups) if index not in positions]
    commands = {
        hook['command'] for group in unowned if isinstance(group, dict) and isinstance(group.get('hooks'), list)
        for hook in group['hooks'] if isinstance(hook, dict) and isinstance(hook.get('command'), str)
    }
    additions = []
    for group in desired:
        if not isinstance(group, dict) or not isinstance(group.get('hooks'), list) or any(
            not isinstance(hook, dict) or not isinstance(hook.get('command'), str) for hook in group['hooks']
        ):
            raise InvalidRequest('Generated hook group is invalid.')
        if group in unowned or group in additions:
            continue
        group_commands = {hook['command'] for hook in group['hooks']}
        if commands & group_commands:
            raise Conflict('A hook uses the same command with different settings.')
        commands.update(group_commands)
        additions.append(copy.deepcopy(group))

    updated = []
    replacement = iter(additions)
    last_slot = max(positions, default=-1)
    for index, group in enumerate(groups):
        if index not in positions:
            updated.append(group)
        elif (item := next(replacement, None)) is not None:
            updated.append(item)
        if index == last_slot:
            updated.extend(replacement)
    if last_slot == -1:
        updated.extend(replacement)
    return updated, additions


def change_clients(workspace, clients, *, layout=None, adopt_layout=None):
    from .native_clients import CLIENTS, read_registry, _read_json
    root = Path(workspace).resolve()
    clients = tuple(clients)
    if not clients or len(set(clients)) != len(clients) or set(clients) - set(CLIENTS):
        raise InvalidRequest('Choose supported distinct clients.')
    registry = read_registry(Path(layout) / 'registry.json') if layout else None
    if adopt_layout is not None and registry is None:
        raise InvalidRequest('Legacy adoption requires an explicit desired layout.')
    with locked(root):
        receipt_path = target(root, RECEIPT)
        original_receipt = read(receipt_path)
        receipt = object_json(original_receipt) if original_receipt else {'schema':1,'workspace':str(root),'clients':{}}
        if receipt.get('schema') != 1 or receipt.get('workspace') != str(root) or not isinstance(receipt.get('clients'), dict):
            raise Conflict('Client ownership receipt does not match this workspace.')
        plans = []
        for client in clients:
            owned = receipt['clients'].get(client, {})
            if not isinstance(owned, dict):
                raise Conflict('Client ownership receipt was modified.')
            if adopt_layout is not None and not owned:
                owned = legacy_ownership(root,adopt_layout,client)
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
            if registry:
                new_owned['layout'] = str(layout)
            if block and before and b'\r\n' in before:
                block = block.replace('\r\n', '\n').replace('\n', '\r\n')
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
                    if not registry:
                        servers.pop('contextrail')
                        if not servers and owned.get('created_server_table'):
                            current.pop(key)
                        after = encoded(current)
                    elif installed != server:
                        servers['contextrail'] = server
                        after = encoded(current)
                if registry:
                    new_owned.update(server=server)
                    if block: new_owned['block'] = block
                    if owned.get('created_server_table'):
                        new_owned['created_server_table'] = True
            elif 'contextrail' not in servers and registry:
                if client == 'codex': after = (before or b'') + block.encode('utf-8')
                else:
                    if key not in current:
                        new_owned['created_server_table'] = True
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
            hooks = current.get('hooks', {})
            if not isinstance(hooks, dict): raise InvalidRequest('Existing hooks must be an object.')
            previous = object_json(before)
            old_events = dict(owned.get('event_groups', {}))
            if owned.get('groups'): old_events['UserPromptSubmit'] = owned['groups']
            wanted = hook_fragment['hooks'] if registry else {}
            if set(wanted) - {'UserPromptSubmit','SessionStart','PostCompact','PostToolUse','PreToolUse'}:
                raise InvalidRequest('Generated hook event is unsupported.')
            created_events = set(owned.get('created_hook_events', ()))
            owns_hooks = owned.get('created_hooks') or 'hooks' not in current
            for event in dict.fromkeys([*old_events,*wanted]):
                groups = hooks.get(event, [])
                if not isinstance(groups,list): raise InvalidRequest('Existing hook groups must be a list.')
                updated, new_groups = update_hook_groups(groups, old_events.get(event, []), wanted.get(event, []))
                if updated:
                    if event not in hooks:
                        created_events.add(event)
                    hooks[event] = updated
                elif event in created_events:
                    hooks.pop(event, None)
                    created_events.remove(event)
                elif event in hooks:
                    hooks[event] = updated
                if new_groups:
                    if event=='UserPromptSubmit': new_owned['groups']=new_groups
                    else: new_owned.setdefault('event_groups',{})[event]=new_groups
            if hooks:
                current['hooks'] = hooks
                if registry and owns_hooks:
                    new_owned['created_hooks'] = True
            elif owns_hooks:
                current.pop('hooks', None)
            if registry and created_events:
                new_owned['created_hook_events'] = sorted(created_events)
            after = encoded(current) if current != previous else before
            plans.append((hook_path, before, after))
            if registry: receipt['clients'][client] = new_owned
            else: receipt['clients'].pop(client, None)
        receipt_bytes = original_receipt if original_receipt is not None and object_json(original_receipt)==receipt else encoded(receipt)
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
