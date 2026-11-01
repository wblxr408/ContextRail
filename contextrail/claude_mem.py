"""Bounded, on-demand execution of the attributed upstream TypeScript subset."""
import base64
import json
import os
from pathlib import Path
import subprocess

from .errors import Conflict, InvalidRequest, Unavailable


def invoke(action, **payload):
    request = json.dumps(dict(action=action, **payload), ensure_ascii=False).encode('utf-8')
    if len(request) > 16 * 1024 * 1024:
        raise InvalidRequest('Upstream adapter request exceeds 16 MiB.')
    runner = Path(__file__).parent / '_vendor/claude_mem/runner.mjs'
    try:
        completed = subprocess.run([os.environ.get('CONTEXTRAIL_NODE', 'node'), '--experimental-strip-types', str(runner)],
            input=request, capture_output=True, timeout=15, shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if len(completed.stdout) > 16 * 1024 * 1024:
            raise ValueError()
        response = json.loads(completed.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise Unavailable('The bundled claude-mem adapter requires Node with native TypeScript stripping (22.6+). '
                          'Set CONTEXTRAIL_NODE to the trusted Node executable; no background service is required.') from None
    if not response.get('ok'):
        if action == 'normalize-memory':
            raise InvalidRequest('Explicit memory must remain nonempty after sanitization and normalization.')
        if action == 'classify':
            raise Unavailable('The upstream classifier could not interpret the observed queue state.')
        if response.get('code') == 'rollback_incomplete':
            raise Conflict('Client update failed and rollback was incomplete. Preserve recovery files at: ' + str(response.get('recovery_dir')))
        raise Conflict('Client update was rejected; committed targets were rolled back. Check file locks, path links and concurrent edits.')
    if completed.returncode:
        raise Unavailable('The bundled claude-mem adapter could not finish the requested operation.')
    return response['result']


def replace_files(root, plans):
    encode = lambda value: base64.b64encode(value).decode('ascii') if value is not None else None
    return invoke('replace', root=str(root), files=[{'name':str(path.relative_to(root)),
        'contents':encode(after), 'expected':encode(before)} for path, before, after in plans])
