"""Task-bound timeline projections and explicit bounded reindexing."""
from dataclasses import asdict
import json

from .errors import BudgetExceeded,InvalidRequest,RailError
from .models import Ref,integer
from . import timeline_index as index

SCHEMA_V9=index.SCHEMA


def candidate(name):
    return name.startswith('native-events/') and name.endswith('/body') or name.startswith('tool-result/') and name.endswith('/result')


def initialize(store):
    store.db.execute("INSERT OR IGNORE INTO timeline_state SELECT h.scope,h.name,h.revision,'pending','not_backfilled' FROM heads h WHERE (h.name LIKE 'native-events/%/body' OR h.name LIKE 'tool-result/%/result')")
    store.db.execute("UPDATE timeline_state SET status='pending',reason='indexer_changed' WHERE status='indexed' AND EXISTS(SELECT 1 FROM timeline_entries e WHERE e.partition=timeline_state.partition AND e.name=timeline_state.name AND json_extract(e.entry,'$.indexer')!=?)",(index.INDEXER,))


def _stream_prefix(store,scope,descriptor):
    ref=Ref(**descriptor['ref'])
    row=store.db.execute('SELECT substr(b.content,1,65536) FROM artifacts a JOIN blobs b ON b.scope=a.scope AND b.sha256=a.sha256 WHERE a.scope=? AND a.name=? AND a.revision=? AND a.invalidated=0 AND (a.expires IS NULL OR a.expires>?)',(scope.key,ref.name,ref.revision,store.clock())).fetchone()
    if row is None:raise ValueError('Tool stream is unavailable')
    return bytes(row[0] or b'').decode('utf-8','replace')


def update(store,scope,name,revision,content):
    if not candidate(name):return
    if len(content)>index.MAX_SOURCE:return index.set_state(store.db,scope.key,name,revision,'excluded','source_over_4mib')
    try:
        value=json.loads(content)
        if not isinstance(value,dict):raise ValueError()
        ref=asdict(Ref(name,revision))
        if value.get('schema')=='contextrail.native-event.v1':
            index.index_native(store.db,value,partition=scope.key,name=name,revision=revision,ref=ref)
        elif value.get('schema')=='contextrail.tool-result.v1':
            # Only explicit task-owned stream refs may supply classifier text.
            err=_stream_prefix(store,scope,value['stderr']);out=_stream_prefix(store,scope,value['stdout'])
            category,rule=index.classification(err+'\n'+out,value.get('tool',''),value['status'])
            entry={'source_event':value['call_id'],'origin':'tool_result','native_session':value['source_session'],
                'client':'trusted_host','role':'tool','event_type':'tool.result','call_id':value['call_id'],
                'occurred_at':value.get('finished_at'),'started_at':value.get('started_at'),
                'status':value['status'],'exit_code':value.get('exit_code'),'error_category':category,'classification':rule,
                'ref':ref,'preview':(err or out)[:320],'preview_truncated':bool(value['stderr'].get('bytes',0)+value['stdout'].get('bytes',0)>320),
                'body_state':'partial' if value.get('partial') else 'complete'}
            index.put_entry(store.db,scope.key,name,revision,entry,index.source_paths(value,value.get('cwd','')))
        else:index.set_state(store.db,scope.key,name,revision,'excluded','unsupported_source_schema')
    except (ValueError,TypeError,KeyError,UnicodeError,RailError):
        index.set_state(store.db,scope.key,name,revision,'unavailable','invalid_or_unavailable_source')


def coverage(store,scope):
    counts=dict(store.db.execute('SELECT status,count(*) FROM timeline_state WHERE partition=? GROUP BY status',(scope.key,)).fetchall())
    gaps=[dict(r) for r in store.db.execute("SELECT name,revision,status,reason FROM timeline_state WHERE partition=? AND status!='indexed' ORDER BY name LIMIT 10",(scope.key,))]
    return {'states':counts,'complete':not any(counts.get(s,0) for s in ('pending','unavailable','excluded')),
        'gaps':gaps,'gaps_truncated':sum(counts.get(s,0) for s in ('pending','unavailable','excluded'))>10,
        'scope':'explicit_task_artifacts_only_not_all_native_history'}


def timeline(store,scope,session,**options):
    with store.read_transaction():
        store.session(scope,session)
        try:result=index.query(store.db,scope.key,coverage=coverage(store,scope),**options)
        except ValueError as exc:
            if 'budget' in str(exc):raise BudgetExceeded(str(exc)) from None
            raise InvalidRequest(str(exc)) from None
        for entry in result['entries']:
            ref=entry['ref']
            available=store.db.execute('SELECT 1 FROM artifacts WHERE scope=? AND name=? AND revision=? AND invalidated=0 AND (expires IS NULL OR expires>?)',(scope.key,ref['name'],ref['revision'],store.clock())).fetchone()
            if not available:
                entry.update(preview='',availability={'status':'unavailable','error':'source_unavailable'})
                result['coverage']['complete']=False
            else:entry['availability']={'status':'available'}
        result.update(task=scope.task,task_version=store.task(scope)['version'])
        # Reserve for final availability/task fields before returning the real envelope.
        budget=options.get('budget',8192)
        while result['entries'] and len(index.canonical(result).encode('utf-8'))>budget:
            result['entries'].pop();result['next_offset']=result['offset']+len(result['entries'])
        if result['total']>result['offset'] and not result['entries'] or len(index.canonical(result).encode('utf-8'))>budget:raise BudgetExceeded('Timeline response budget cannot preserve source references')
        return result


def reindex(store,scope,lease,*,limit=20):
    integer(limit,1)
    if limit>100:raise InvalidRequest('Timeline rebuild limit exceeds 100')
    with store.transaction():
        store._lease(scope,lease)
        rows=store.db.execute("SELECT name,revision FROM timeline_state WHERE partition=? AND status='pending' ORDER BY name LIMIT ?",(scope.key,limit)).fetchall()
        used=0;processed=0
        for row in rows:
            try:
                metadata=store.db.execute('SELECT size FROM artifacts WHERE scope=? AND name=? AND revision=?',(scope.key,row['name'],row['revision'])).fetchone()
                if metadata and metadata['size']>index.MAX_SOURCE:
                    index.set_state(store.db,scope.key,row['name'],row['revision'],'excluded','source_over_4mib');processed+=1;continue
                artifact=store.get(scope,Ref(row['name'],row['revision']))
                if used+artifact.size>16*1024*1024 and processed:break
                update(store,scope,row['name'],row['revision'],artifact.content);used+=artifact.size
            except RailError:
                index.set_state(store.db,scope.key,row['name'],row['revision'],'unavailable','source_unavailable')
            processed+=1
        return {'processed':processed,'source_bytes':used,'coverage':coverage(store,scope),'task_version':store.task(scope)['version']}
