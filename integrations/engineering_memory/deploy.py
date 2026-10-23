"""Explicit local deployment into the existing service; no task/service creation."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil


def deploy(home):
    root = Path(__file__).resolve().parents[2]
    home = home.resolve(strict=True)
    app = home / 'app'
    # The existing service must have been stopped using its native control.ps1.
    # Do not stop/kill/restart processes implicitly in a source-copy operation.
    if not (home / 'stop').exists():
        raise ValueError('Stop the existing service with control.ps1 stop before deployment')
    backup = home / 'backups' / ('capture-v1-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(parents=True)
    changes = {name: (Path(__file__).parent / name).read_text(encoding='utf-8')
               for name in ('listener.py','history_import.py','memory_runtime.py','memory_integration.py','mcp_proxy.py','resource_client.py')}
    changes['learning_origin.py']=(root/'contextrail/learning_origin.py').read_text(encoding='utf-8')
    changes['capture_rules.py']=(root/'contextrail/capture_rules.py').read_text(encoding='utf-8')
    changes['fidelity.py']=(root/'contextrail/fidelity.py').read_text(encoding='utf-8')
    changes['derived_pages.py']=(root/'contextrail/derived_pages.py').read_text(encoding='utf-8')
    for name in ('reflect-schema.json','reflect-instructions.txt'):
        changes[name]=(Path(__file__).parent/name).read_text(encoding='utf-8')
    for name in ('ingest_delivery.py','stream_cursor.py'):
        code=(root/'contextrail'/name).read_text(encoding='utf-8')
        for local,remote in (('.sanitization','capture_sanitizer'),('.source_events','event_identity'),('.capture_rules','capture_rules'),('.capture_policy','capture_policy')):
            code=code.replace('from '+local+' import ','from '+remote+' import ')
        changes[name]=code
    timeline=(root/'contextrail/timeline_index.py').read_text(encoding='utf-8')
    timeline=timeline.replace('from ._vendor.context_mode_errors import CATEGORIES,classify_error','from context_mode_errors import CATEGORIES,classify_error')
    changes['timeline_index.py']=timeline.replace('from .source_events import workspace_key','from event_identity import workspace_key')
    changes['context_mode_errors.py']=(root/'contextrail/_vendor/context_mode_errors.py').read_text(encoding='utf-8')
    policy=(root/'contextrail/capture_policy.py').read_text(encoding='utf-8')
    changes['capture_policy.py']=policy.replace('from .sanitization import require_safe_identity','from capture_sanitizer import require_safe_identity').replace('from .source_events import canonical,workspace_key','from event_identity import canonical,workspace_key')
    patterns = (root / 'contextrail/_vendor/ai_memory_patterns.py').read_text(encoding='utf-8')
    sanitizer = (root / 'contextrail/sanitization.py').read_text(encoding='utf-8')
    changes['capture_sanitizer.py'] = sanitizer.replace('from ._vendor.ai_memory_patterns import BUILTIN_PATTERNS', patterns)
    identity = (root / 'contextrail/source_events.py').read_text(encoding='utf-8')
    changes['event_identity.py'] = identity.replace('from .sanitization import require_safe_identity, sanitize_capture',
        'from capture_sanitizer import require_safe_identity, sanitize_capture')
    stream=(root/'contextrail/event_stream.py').read_text(encoding='utf-8')
    stream=stream.replace('from .sanitization import CaptureError, sanitize_capture, require_safe_identity',
        'from capture_sanitizer import CaptureError, sanitize_capture, require_safe_identity')
    stream=stream.replace('from .source_events import source_identity, admit_event, workspace_key, canonical',
        'from event_identity import source_identity, admit_event, workspace_key, canonical')
    stream=stream.replace('from .capture_policy import SCHEMA as CAPTURE_SCHEMA, claim_owner','from capture_policy import SCHEMA as CAPTURE_SCHEMA, claim_owner')
    stream=stream.replace('from .learning_origin import origin','from learning_origin import origin')
    stream=stream.replace('from .capture_rules import project_item','from capture_rules import project_item')
    stream=stream.replace('from .timeline_index import SCHEMA as TIMELINE_SCHEMA, index_native, native_partition, query as query_timeline, set_state as timeline_state, MAX_SOURCE, INDEXER',
        'from timeline_index import SCHEMA as TIMELINE_SCHEMA, index_native, native_partition, query as query_timeline, set_state as timeline_state, MAX_SOURCE, INDEXER')
    changes['native_event_stream.py']=stream.replace('from ._vendor.context_mode_tools import normalize_tool_name',
        'from context_mode_tools import normalize_tool_name')
    changes['context_mode_tools.py']=(root/'contextrail/_vendor/context_mode_tools.py').read_text(encoding='utf-8')
    changes['native_event_stream.py']=changes['native_event_stream.py'].replace('from ._vendor.context_mode_usage import parse_codex_usage','from context_mode_usage import parse_codex_usage')
    changes['context_mode_usage.py']=(root/'contextrail/_vendor/context_mode_usage.py').read_text(encoding='utf-8')
    changes['context_mode_LICENSE.txt']=(root/'contextrail/_vendor/context_mode_LICENSE.txt').read_text(encoding='utf-8')
    changes['capture_sanitizer_LICENSE.txt'] = (root / 'contextrail/_vendor/ai_memory_LICENSE.txt').read_text(encoding='utf-8')
    changes['capture_sanitizer_NOTICES.md'] = (root / 'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')
    previous = {}
    for name in changes:
        target = app / name
        previous[name] = target.exists()
        if target.exists():
            shutil.copy2(target, backup / name)
    (backup / 'deployment.json').write_text(json.dumps({'home':str(home),'previous':previous},indent=2),encoding='utf-8')
    for name, text in changes.items():
        target = app / name
        temporary = target.with_name(name + '.capture-new')
        temporary.write_text(text, encoding='utf-8')
        temporary.replace(target)
    return {'backup':str(backup),'deployed':list(changes),'activation':'requires_existing_service_start'}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home',type=Path,default=Path.home()/'.codex/services/engineering-memory')
    args=parser.parse_args()
    print(json.dumps(deploy(args.home),ensure_ascii=False))
