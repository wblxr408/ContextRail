"""Explicit user recording through existing M, independent of model availability."""
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
from uuid import NAMESPACE_URL, uuid5

from .claude_mem import invoke
from .errors import Conflict, InvalidRequest, Unavailable
from .models import identifier, unicode_text
from .sanitization import sanitize_capture
from ._vendor.ai_memory_brief import excerpt_from_page


def read_record(path):
    try:
        if path == '-': raw = sys.stdin.buffer.read(65537)
        else:
            with Path(path).open('rb') as stream: raw = stream.read(65537)
        if len(raw)>65536: raise ValueError()
        return json.loads(raw)
    except (OSError,ValueError,UnicodeError,RecursionError):
        raise InvalidRequest('Explicit memory input must be UTF-8 JSON up to 64 KiB.') from None


@contextmanager
def runtime(home):
    """Use the trusted installed M owner and its authoritative configured vault."""
    home = Path(home).resolve()
    try:
        config = json.loads((home/'config.json').read_text(encoding='utf-8'))
        vault = Path(config['vault'])
        if not vault.is_absolute() or not vault.is_dir(): raise ValueError()
        fd = os.open(home/'explicit-memory.lock', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise Conflict('Another explicit memory operation owns explicit-memory.lock; review a stopped owner before removing its lock.') from None
    except (OSError, ValueError, KeyError, TypeError):
        raise Unavailable('Existing memory config must name its absolute authoritative vault; no replacement vault was created.') from None
    memory = None
    sys.path.insert(0,str(home/'app'))
    try:
        spec = importlib.util.spec_from_file_location('rail_explicit_existing_memory',home/'app/memory_runtime.py')
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            memory = module.MemoryRuntime(vault, home/'state.sqlite', use_models=False)
        except (OSError, ImportError, ValueError, sqlite3.Error):
            raise Unavailable('The existing M runtime and its dependencies must be installed; model loading is disabled for explicit recording.') from None
        yield memory, module.ConflictError
    finally:
        if memory is not None: memory.close()
        sys.path.pop(0)
        os.close(fd)
        (home/'explicit-memory.lock').unlink()


def save(home, workspace, payload):
    if not isinstance(payload,dict) or payload.keys()-{'event_id','text','title','role'} or payload.get('role','user') != 'user':
        raise InvalidRequest('Explicit recording accepts only user text, title and a stable event_id.')
    identifier(payload.get('event_id'))
    text,title = payload.get('text'), payload.get('title','')
    if not isinstance(text,str) or not text.strip() or not isinstance(title,str) or len(text.encode('utf-8'))>32768 or len(title)>256:
        raise InvalidRequest('Provide nonempty user text up to 32 KiB and a title up to 256 characters.')
    unicode_text(text);unicode_text(title)
    safe = sanitize_capture({'text':text,'title':title}, 'user').value
    observation = invoke('normalize-memory',input=safe)
    root = Path(workspace).resolve()
    if not root.is_dir(): raise InvalidRequest('Workspace must be an existing directory.')
    event = 'manual:'+uuid5(NAMESPACE_URL,os.path.normcase(str(root))).hex+':'+payload['event_id']
    narrative = observation['narrative']
    claim = {'subject':observation['title'],'predicate':'explicit_record','value':narrative,
        'quote':narrative,'source':'user','scope':str(root),'authority':True}
    with runtime(home) as (memory, conflict_type):
        try:
            result = memory.retain(event,[claim],{'user':narrative})
        except conflict_type:
            raise Conflict('This explicit source identity already binds different text; use a new event_id for a new decision.') from None
        except (OSError, ValueError, sqlite3.Error):
            raise Unavailable('Existing M could not finish recording. Retry the same event_id; do not infer a completed index.') from None
    excerpt,_ = excerpt_from_page({},narrative)
    return {**result,'learning_state':'explicit_user_record_not_model_derived','model_calls':0,
        'index_state':'not_observed','model_consumption':'unknown',
        'observation_type':observation['type'],'title':observation['title'],
        'brief':{'mode':'deterministic_excerpt','text':excerpt,'source':result['source']},
        'recall':{'existing_tool':'memory_recall','arguments':{'query':observation['title'],'scope':str(root)},
            'offline_sdk':{'method':'MemoryRuntime.recall','arguments':{'query':observation['title'],'scope':str(root),'mode':'text'}}},
        'trust':'explicit_user_history_not_current_task_policy'}
