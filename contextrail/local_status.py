"""Observed task/capture/learning state without model calls or health probes."""
import json
import sqlite3
from contextlib import closing
from .capabilities import HostCapabilities
from .errors import BudgetExceeded,RailError
from .models import canonical
from .source_events import workspace_key
from .federation import bound_sources
from .query_sources import event_selection


def status(tools,*,snapshot=None,budget=8192):
    from .models import integer
    integer(budget,1)
    if budget<2048 or budget>tools.max_read_bytes:raise BudgetExceeded('Status budget must be 2048 bytes up to the bound reader limit')
    binding=bound_sources(tools)
    with tools.store.read_transaction():
        task=tools.store.task(tools.scope)
        result={'schema':'contextrail.local-status.v1','task':task,'scope':{'task':tools.scope.task,'reader_session':tools.session_id},
            'requirements':{'objective':task['objective'],'constraints':task['constraints'],'acceptance':task['acceptance'],
                'source':'current_stored_task','external_source_current':'not_checked'},
            'capture':{'status':'unknown','raw_events':None,'source':'not_configured'},
            'learning':{'status':'unknown','saved_jobs':None,'scope':'workspace_jobs_not_task','states':{}},
            'source_errors':list(getattr(getattr(tools,'_query_state',None),'errors',[])),
            'capabilities':HostCapabilities.read_only().describe(),
            'process_liveness':'not_observed_by_this_status_read','model_calls':0}
        row=tools.store.db.execute("SELECT sequence,at,metadata FROM events WHERE scope=? AND kind='native_context.prepared' ORDER BY sequence DESC LIMIT 1",(tools.scope.key,)).fetchone()
        result['injection']={'status':'prepared_receipt_observed' if row else 'unknown','model_consumption':'unknown'}
        if row:
            meta=json.loads(row['metadata']);result['injection'].update(sequence=row['sequence'],observed_at=row['at'],
                snapshot=meta.get('snapshot'),client=meta.get('client'),returned_bytes=meta.get('returned_bytes'),
                memory_status=meta.get('memory_status'))
        from .indexing import status as index_status
        result['task_index']=index_status(tools.store,tools.scope)
    selected=snapshot or (binding.snapshot if binding else None)
    if selected:
        try:
            from .bridge import BridgeEvidence
            packet=BridgeEvidence(tools.store,tools.scope,tools.session_id).packet(selected,budget=8192)
            result['requirements'].update(snapshot=selected,required_ids=packet['required_ids'],manifest_ref=packet['manifest_ref'],external_source_current=True)
            if tools.store.task(tools.scope)['version']!=task['version']:
                result['requirements']['external_source_current']='task_changed_during_status_read'
        except RailError as exc:
            result['requirements'].update(snapshot=selected,external_source_current=False,error={'code':exc.code})
    if binding and binding.memory_home:
        try:
            path=binding.memory_home/'state.sqlite'
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
                db.row_factory=sqlite3.Row;db.execute('BEGIN')
                if binding.native_sessions:
                    where,args=event_selection(binding)
                    row=db.execute('SELECT count(*),max(json_extract(n.metadata,\'$.observed_at\')) FROM normalized_events n WHERE '+where,args).fetchone()
                    result['capture']={'status':'capture_observed' if row[0] else 'no_capture_observed','raw_events':row[0],
                        'latest_observed_at':row[1],'scope':'bound_workspace_and_exact_sessions','process_liveness':'unknown'}
                workspace=workspace_key(binding.workspace)
                states=dict(db.execute("SELECT state,count(*) FROM jobs WHERE lower(replace(json_extract(payload,'$.cwd'),'\\','/'))=? GROUP BY state",(workspace,)).fetchall())
                result['learning']={'status':'workspace_queue_observed','saved_jobs':states.get('saved',0),'states':states,
                    'pending_jobs':sum(states.get(s,0) for s in ('queued','retry','processing','ready_to_save','awaiting_index')),
                    'scope':'workspace_jobs_not_task','raw_capture_is_not_a_learned_fact':True}
                errors=db.execute("SELECT event_id,reason,changed_fields,first_seen FROM source_event_conflicts WHERE json_extract(incoming,'$.workspace')=? ORDER BY id DESC LIMIT 5",(workspace,)).fetchall()
                result['source_errors']=(result['source_errors']+[dict(r) for r in errors])[-10:]
        except (OSError,sqlite3.Error,ValueError):
            result['memory_source_error']={'code':'existing_memory_state_unavailable','retryable':True}
    # Large task text remains in its authoritative store/Bridge; status is a view.
    result['display_truncated']=False
    while len(canonical(result).encode())>budget:
        if result['source_errors']:result['source_errors'].pop();result['display_truncated']=True;continue
        if result['requirements'].get('constraints'):
            result['requirements']['constraints']=[];result['display_truncated']=True;continue
        if result['requirements'].get('acceptance'):
            result['requirements']['acceptance']=[];result['display_truncated']=True;continue
        # The full task is already available through the existing status/navigation
        # and exact Bridge refs; do not silently truncate IDs or requirement refs.
        if 'constraints' in result['task']:
            result['task']={k:v for k,v in result['task'].items() if k not in {'constraints','acceptance'}};result['display_truncated']=True;continue
        raise BudgetExceeded('Status references and metadata exceed budget; increase it')
    return result
