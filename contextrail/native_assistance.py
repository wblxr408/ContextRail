"""Main-session native assistance over exact original events and Rail receipts."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from .claude_mem import invoke
from .errors import InvalidRequest, RailError, Unavailable
from .models import Scope, Lease, canonical, identifier
from .query_sources import QueryBinding
from .source_events import workspace_key
from .store import Store

SCHEMA = '''CREATE TABLE IF NOT EXISTS native_context_deliveries(
 scope TEXT NOT NULL,client TEXT NOT NULL,native_session TEXT NOT NULL,base_snapshot TEXT NOT NULL,
 source_event TEXT NOT NULL,source_sequence INTEGER NOT NULL,state TEXT NOT NULL,
 attempt TEXT,claim_until REAL,updated REAL NOT NULL,error TEXT,
 PRIMARY KEY(scope,client,native_session,base_snapshot,source_event));'''
EVENTS = {'PostCompact','SessionStart','PostToolUse','UserPromptSubmit','PreToolUse'}


def selected(path, client, payload):
    from .native_clients import read_registry, CLIENTS
    if client not in CLIENTS or not isinstance(payload,dict) or payload.get('hook_event_name') not in EVENTS:
        raise InvalidRequest('Unsupported native assistance event.')
    identifier(payload.get('session_id'))
    if not isinstance(payload.get('cwd'),str) or not Path(payload['cwd']).is_absolute():
        raise InvalidRequest('Native assistance requires an absolute cwd.')
    matches=[b for b in read_registry(path)['bindings'] if b['client']==client and b['enabled'] and
        Path(payload['cwd']).resolve().is_relative_to(Path(b['workspace']).resolve())]
    if len(matches)>1:raise InvalidRequest('Native assistance requires exactly one workspace binding.')
    if not matches or not matches[0].get('context_assistance'):return None,None
    binding=matches[0]
    query=QueryBinding.load(binding.get('query_binding'),binding['scope']['task'],binding['workspace'])
    if binding.get('native_session_binding')=='current_hook' and payload.get('agent_id') in (None,payload['session_id']):
        from dataclasses import replace
        current=(client,payload['session_id'])
        # The trusted native Hook supplies identity; never choose a latest log.
        # Static historical sources remain unchanged on disk and bounded here.
        query=replace(query,native_sessions=(current,*[p for p in query.native_sessions if p!=current][:3]))
    if (client,payload['session_id']) not in query.native_sessions:return None,None
    return binding,query


def marker(binding,client,session,operation):
    from .client_config import target
    root=Path(binding['workspace']).resolve()
    control=target(root,'.context-rail')
    key=canonical([Scope(**binding['scope']).key,client,session,binding['base_snapshot']])
    return invoke('compact-marker',root=str(control),session=key,operation=operation)


def compaction(query,client,session):
    try:
        with closing(sqlite3.connect((query.memory_home/'state.sqlite').as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
            row=db.execute("SELECT event_id,sequence FROM normalized_events WHERE workspace=? AND client=? AND native_session=? AND event_type='context.compacted' ORDER BY sequence DESC LIMIT 1",
                (workspace_key(query.workspace),client,session)).fetchone()
            return row
    except (OSError,sqlite3.Error):return None


def _assist(path, client, payload, *, optional=False):
    binding,query=selected(path,client,payload)
    if binding is None:
        return None if optional else {'systemMessage':'ContextRail assistance unavailable: exact enabled task/native-session binding required.'}
    if payload.get('agent_id') not in (None,payload['session_id']):
        return None if optional else {'systemMessage':'ContextRail assistance unavailable for unbound child threads.'}
    event=payload['hook_event_name'];session=payload['session_id']
    scope=Scope(**binding['scope']);owner=Lease(**binding['owner'])
    if not Path(binding['store']).is_file():raise Unavailable('The original Rail store is unavailable; no replacement was created.')
    with Store(binding['store']) as store:
        store.db.executescript(SCHEMA)
        if event=='PostCompact' or (event=='SessionStart' and payload.get('source')=='compact'):
            marked=marker(binding,client,session,'mark')
            if not marked['pending']:raise Unavailable('Compaction marker could not be persisted; no delivery was recorded.')
            with store.transaction():
                store._lease(scope,owner)
                store._event(scope,'native_compact.pending',client=client,native_session=session,base_snapshot=binding['base_snapshot'])
            if event=='PostCompact':return {'suppressOutput':True}
        if not marker(binding,client,session,'has')['pending']:
            # A process may have taken the marker and died before stdout acknowledgement.
            uncertain=store.db.execute("SELECT claim_until FROM native_context_deliveries WHERE scope=? AND client=? AND native_session=? AND base_snapshot=? AND state IN ('prepared','pending') ORDER BY source_sequence DESC LIMIT 1",
                (scope.key,client,session,binding['base_snapshot'])).fetchone()
            if not uncertain:
                if event=='PreToolUse':
                    from .file_history import hook_file_history
                    return hook_file_history(binding,query,client,payload)
                return None if optional else {}
            if (uncertain['claim_until'] or 0)>time.time():
                return {'systemMessage':'ContextRail delivery remains prepared with transport outcome unknown; claim is still held.'}
            marker(binding,client,session,'mark')
        source=compaction(query,client,session)
        if source is None:
            return {'systemMessage':'ContextRail awaiting_original_compaction: original listener source identity is not yet observed; marker retained.'}
        source_event,source_sequence=source
        key=(scope.key,client,session,binding['base_snapshot'],source_event)
        completed=store.db.execute('SELECT state FROM native_context_deliveries WHERE scope=? AND client=? AND native_session=? AND base_snapshot=? AND source_event=?',key).fetchone()
        if completed and completed['state']=='returned':
            if event=='PreToolUse':
                from .file_history import hook_file_history
                return hook_file_history(binding,query,client,payload)
            return None if optional else {}
        attempt=uuid4().hex;now=time.time()
        with store.transaction():
            store._lease(scope,owner)
            store.db.execute("UPDATE native_context_deliveries SET state='superseded',updated=? WHERE scope=? AND client=? AND native_session=? AND base_snapshot=? AND source_sequence<? AND state IN ('pending','prepared')",
                (now,scope.key,client,session,binding['base_snapshot'],source_sequence))
            row=store.db.execute('SELECT state,claim_until FROM native_context_deliveries WHERE scope=? AND client=? AND native_session=? AND base_snapshot=? AND source_event=?',key).fetchone()
            if row and row['state']=='returned':return {} if not optional else None
            if row and row['state']=='prepared' and (row['claim_until'] or 0)>now:
                return {'systemMessage':'ContextRail compaction delivery is being prepared; no duplicate context returned.'}
            store.db.execute('''INSERT INTO native_context_deliveries VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(scope,client,native_session,base_snapshot,source_event) DO UPDATE SET
                state='prepared',attempt=excluded.attempt,claim_until=excluded.claim_until,updated=excluded.updated,error=NULL''',
                (*key,source_sequence,'prepared',attempt,now+60,now,None))
        try:
            # The DB claim survives a failed marker consume; it is not a delivery receipt.
            marker(binding,client,session,'take')
            from .native_clients import _task_context
            recovery={'source_event':source_event,'source_sequence':source_sequence,'base_snapshot':binding['base_snapshot'],
                'state':'prepared','attempt':attempt,'model_consumption':'unknown'}
            result=_task_context(binding,payload,client,{},'not_requeried_during_compaction_recovery',recovery=recovery)
            if result.get('decision')=='block':
                code=result['reason'].split(':',1)[0].replace('ContextRail ','')
                raise RequiredContextFailure(code,result['reason'])
            output=result['hookSpecificOutput'];context=json.loads(output['additionalContext'])
            if len(canonical(result).encode('utf-8'))>binding['hook_bytes'] or (client=='claude' and len(output['additionalContext'].encode('utf-16-le'))//2>10000):
                raise RequiredContextFailure('budget_exceeded','Compaction recovery envelope exceeds the native byte/inline budget.')
            with store.transaction():
                store._lease(scope,owner)
                current=store.db.execute('SELECT attempt FROM native_context_deliveries WHERE scope=? AND client=? AND native_session=? AND base_snapshot=? AND source_event=?',key).fetchone()
                if current['attempt']!=attempt:raise Unavailable('Compaction claim changed before preparation finished.')
                store._event(scope,'native_compact.prepared',client=client,native_session=session,source_event=source_event,
                    attempt=attempt,base_snapshot=binding['base_snapshot'],snapshot=context['task']['snapshot'])
            return result
        except (RailError,OSError,KeyError,ValueError) as exc:
            code=exc.code if isinstance(exc,RailError) else 'storage_or_format_error'
            with store.transaction():
                store.db.execute("UPDATE native_context_deliveries SET state='pending',attempt=NULL,claim_until=NULL,error=?,updated=? WHERE scope=? AND client=? AND native_session=? AND base_snapshot=? AND source_event=? AND attempt=?",(code,time.time(),*key,attempt))
                store._event(scope,'native_compact.failed',client=client,native_session=session,source_event=source_event,reason=code)
            marker(binding,client,session,'mark')
            return {'continue':False,'stopReason':'ContextRail '+code+': required exact recovery is unavailable.',
                'systemMessage':'Reconcile the bound source or budget, then retry; compaction remains pending.'}


class RequiredContextFailure(RailError):
    def __init__(self,code,message):
        self.code=code
        super().__init__(message)


def assist(path, client, payload, *, optional=False):
    try:
        return _assist(path,client,payload,optional=optional)
    except (RailError,OSError,sqlite3.Error) as exc:
        code=exc.code if isinstance(exc,RailError) else 'storage_error'
        result={'systemMessage':'ContextRail assistance '+code+': exact bound source/control could not be used.'}
        if payload.get('hook_event_name') not in {'PostCompact','PreToolUse'}:
            result['continue']=False
            result['stopReason']='ContextRail '+code+': required recovery could not be evaluated.'
        return result


def acknowledge_return(path, client, payload, result):
    """Called only after native stdout was written and flushed; not model consumption."""
    try:
        context=json.loads(result.get('hookSpecificOutput',{}).get('additionalContext','{}'))
        if not isinstance(context,dict) or context.get('schema')!='contextrail.native-context/v1':
            return
        origin=context.get('origin',{})
        if not isinstance(origin,dict) or origin.get('native_session')!=payload.get('session_id') or origin.get('client')!=client:
            return
        receipt=context.get('recovery')
        binding,query=selected(path,client,payload)
        if binding is None:return
        if context.get('task',{}).get('task_id')!=binding['scope']['task']:return
        scope=Scope(**binding['scope']);owner=Lease(**binding['owner'])
        with Store(binding['store']) as store:
            with store.transaction():
                store._lease(scope,owner)
                store._event(scope,'native_context.returned',client=client,native_session=payload['session_id'],
                    snapshot=context['task']['snapshot'],transport='stdout_flushed',model_consumption='unknown')
                if not receipt:return
                key=(scope.key,client,payload['session_id'],binding['base_snapshot'],receipt['source_event'])
                changed=store.db.execute("UPDATE native_context_deliveries SET state='returned',claim_until=NULL,updated=? WHERE scope=? AND client=? AND native_session=? AND base_snapshot=? AND source_event=? AND state='prepared' AND attempt=?",(time.time(),*key,receipt['attempt'])).rowcount
                if changed:store._event(scope,'native_compact.returned',client=client,native_session=payload['session_id'],
                    source_event=receipt['source_event'],attempt=receipt['attempt'],transport='stdout_flushed',model_consumption='unknown')
    except (OSError,ValueError,sqlite3.Error,RailError):
        # A sent context cannot be unsent. Prepared stays uncertain; never report it returned.
        return
