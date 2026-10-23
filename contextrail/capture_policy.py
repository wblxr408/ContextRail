"""Single-owner admission and content-free Hook delegation in the existing store."""
import json
import time
from uuid import uuid5,NAMESPACE_URL
from .sanitization import require_safe_identity
from .source_events import canonical,workspace_key

SCHEMA='''
CREATE TABLE IF NOT EXISTS capture_owners(
 domain TEXT NOT NULL,workspace TEXT NOT NULL,client TEXT NOT NULL,native_session TEXT NOT NULL,
 capture_owner TEXT NOT NULL,claimed_at REAL NOT NULL,PRIMARY KEY(domain,workspace,client,native_session));
CREATE TABLE IF NOT EXISTS capture_receipts(
 id TEXT PRIMARY KEY,workspace TEXT NOT NULL,client TEXT NOT NULL,native_session TEXT NOT NULL,
 producer TEXT NOT NULL,capture_owner TEXT NOT NULL,outcome TEXT NOT NULL,source_event TEXT,
 deliveries INTEGER NOT NULL,metadata TEXT NOT NULL);
'''


def claim_owner(db,workspace,client,session,*,domain='native'):
    if not db.in_transaction:raise ValueError('Capture ownership requires the caller transaction')
    for value in (workspace,client,session):require_safe_identity(value)
    owner='existing_jsonl_listener' if domain=='native' else 'existing_import_queue' if domain=='import' else None
    if owner is None:raise ValueError('Unsupported capture domain')
    key=(domain,workspace_key(workspace),client,session)
    db.execute('INSERT OR IGNORE INTO capture_owners VALUES(?,?,?,?,?,?)',(*key,owner,time.time()))
    row=db.execute('SELECT capture_owner FROM capture_owners WHERE domain=? AND workspace=? AND client=? AND native_session=?',key).fetchone()
    if row[0]!=owner:raise ValueError('Capture owner conflicts with the installed policy')
    return owner


def receipt(db,workspace,client,session,producer,owner,outcome,source_event=None,*,metadata=None):
    for value in (workspace,client,session,producer,owner,outcome):require_safe_identity(value)
    if source_event is not None:require_safe_identity(source_event)
    key=[workspace_key(workspace),client,session,producer,outcome,source_event,(metadata or {}).get('hook_event')]
    identity='capture-receipt:'+uuid5(NAMESPACE_URL,canonical(key)).hex
    data={'policy':'capture-owner-v1','delivery':'not_observed' if outcome=='delegated' else 'source_admission',**(metadata or {})}
    db.execute('INSERT INTO capture_receipts VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET deliveries=deliveries+1',
        (identity,workspace_key(workspace),client,session,producer,owner,outcome,source_event,1,canonical(data)))
    return {'id':identity,'producer':producer,'capture_owner':owner,'status':outcome,'source_event':source_event,**data}


def delegate_hook(db,workspace,client,session,hook_event,source_event=None):
    if client not in {'codex','claude'} or hook_event not in {'UserPromptSubmit','PostToolUse','Stop','SessionStart','PreCompact'}:
        raise ValueError('Unsupported native Hook selection')
    owner=claim_owner(db,workspace,client,session)
    return receipt(db,workspace,client,session,'native_hook',owner,'delegated',source_event,
                   metadata={'hook_event':hook_event,'input_body_retained':False})


def capture_status(db,workspace,*,offset=0,limit=20):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:raise ValueError('Invalid capture page')
    workspace=workspace_key(workspace)
    owners=[dict(row) for row in db.execute('SELECT * FROM capture_owners WHERE workspace=? ORDER BY domain,client,native_session LIMIT ? OFFSET ?',(workspace,limit,offset))]
    total=db.execute('SELECT count(*) FROM capture_owners WHERE workspace=?',(workspace,)).fetchone()[0]
    receipts=[{**dict(row),'metadata':json.loads(row['metadata'])} for row in db.execute('SELECT * FROM capture_receipts WHERE workspace=? ORDER BY rowid DESC LIMIT ?',(workspace,limit))]
    return {'owners':owners,'receipts':receipts,'total':total,'offset':offset,'next_offset':offset+len(owners) if offset+len(owners)<total else None}
