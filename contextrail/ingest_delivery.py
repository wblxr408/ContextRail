"""Durable selected delivery and job controls in the existing M database."""
import json,time
from .sanitization import sanitize_capture,require_safe_identity
from .source_events import canonical,workspace_key
from .capture_rules import load_policy,decide

SCHEMA='''
CREATE TABLE IF NOT EXISTS delivery_inbox(id TEXT PRIMARY KEY,workspace TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,
 created REAL NOT NULL,available REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,outcome TEXT,error TEXT);
CREATE INDEX IF NOT EXISTS delivery_due ON delivery_inbox(state,available);
CREATE TABLE IF NOT EXISTS job_control(job_id TEXT PRIMARY KEY,reason TEXT,next_retry REAL,first_wait REAL,stage TEXT,cancel_requested INTEGER NOT NULL DEFAULT 0,updated REAL);
CREATE TABLE IF NOT EXISTS job_actions(id INTEGER PRIMARY KEY,job_id TEXT,action TEXT,prior_state TEXT,reason TEXT,at REAL);
UPDATE job_control SET stage='cancelled',cancel_requested=0,next_retry=NULL WHERE (stage!='cancelled' OR cancel_requested!=0)
 AND job_id IN (SELECT id FROM jobs WHERE state='cancelled');
'''


def prepared(item):
    if not isinstance(item,dict):raise ValueError('Delivery item must be an object')
    kind=item.get('kind');base={'id','kind','workspace'}
    allowed=base|({'source','event_key','content','occurred_at'} if kind in {'document','git'} else {'client','native_session','hook_event','source_event'})
    required=base|({'source','event_key','content'} if kind in {'document','git'} else {'client','native_session','hook_event'})
    if kind not in {'document','git','native_hook'} or set(item)-allowed or not required<=item.keys():raise ValueError('Unsupported delivery fields')
    for key in required-{'kind','content'}:require_safe_identity(item[key])
    value=dict(item);value['workspace']=workspace_key(item['workspace'])
    if kind=='native_hook':
        if item['client'] not in {'codex','claude'} or item['hook_event'] not in {'UserPromptSubmit','PostToolUse','Stop','SessionStart','PreCompact'}:raise ValueError('Invalid native delegation')
        if item.get('source_event') is not None:require_safe_identity(item['source_event'])
    else:
        if not isinstance(item['content'],str):raise ValueError('Import delivery requires text')
        policy=load_policy(item['workspace'])
        disposition=decide(policy,'tool','tool.call',{'tool':'Read','arguments':{'file_path':item['source']}},item['workspace']) if kind=='document' else {'mode':'metadata_only' if policy['invalid'] else policy['default_mode'],'reason':'invalid_policy' if policy['invalid'] else 'explicit_git_import'}
        if disposition['mode']!='full':value['content']=''
        value['_disposition']=disposition
    value=sanitize_capture(value,'document').value
    if len(canonical(value).encode())>2*1024*1024:raise ValueError('Delivery item exceeds 2 MiB')
    return value


def submit(store,items):
    if not isinstance(items,list) or not 1<=len(items)<=100:raise ValueError('Delivery batch requires 1..100 items')
    entries=[]
    for item in items:
        identity=item.get('id') if isinstance(item,dict) else None
        try:
            value=prepared(item);raw=canonical(value);now=time.time()
            with store.db:
                store.db.execute('BEGIN IMMEDIATE')
                prior=store.db.execute('SELECT payload,state FROM delivery_inbox WHERE id=?',(value['id'],)).fetchone()
                if prior:
                    if prior['payload']!=raw:raise ValueError('Delivery identity conflicts')
                    outcome,state='duplicate',prior['state']
                else:
                    store.db.execute("INSERT INTO delivery_inbox(id,workspace,payload,state,created,available) VALUES(?,?,?,'queued',?,?)",(value['id'],value['workspace'],raw,now,now));outcome,state='accepted','queued'
            entries.append({'id':value['id'],'outcome':outcome,'delivery_state':state,'ack':'durable_inbox_not_learning_completion'})
        except (ValueError,TypeError):
            try:require_safe_identity(identity);safe_id=identity
            except (ValueError,TypeError):safe_id=None
            entries.append({'id':safe_id,'outcome':'failed','error':'invalid_or_conflicting_delivery'})
    return {'schema':'contextrail.delivery-submit.v1','entries':entries,'retention':'no_automatic_expiration_or_capacity_drop'}


def drain(store,*,ids=None,limit=20,now=None):
    if type(limit) is not int or not 1<=limit<=100:raise ValueError('Delivery limit requires 1..100')
    now=time.time() if now is None else now
    if ids is not None:
        if not isinstance(ids,list) or not 1<=len(ids)<=100:raise ValueError('Exact delivery IDs required')
        for identity in ids:require_safe_identity(identity)
        rows=[store.db.execute('SELECT * FROM delivery_inbox WHERE id=?',(i,)).fetchone() for i in dict.fromkeys(ids)]
    else:rows=store.db.execute("SELECT * FROM delivery_inbox WHERE state IN ('queued','retry') AND available<=? ORDER BY created,id LIMIT ?",(now,limit)).fetchall()
    entries=[]
    for row in rows:
        if row is None:entries.append({'id':None,'outcome':'failed','error':'unknown_delivery'});continue
        identity=row['id']
        if row['state']=='delivered':entries.append({'id':identity,'outcome':'duplicate','effect':'already_acknowledged'});continue
        if row['available']>now:entries.append({'id':identity,'outcome':'failed','error':'deferred','available':row['available']});continue
        try:
            item=json.loads(row['payload'])
            if item['kind']=='native_hook':
                from .capture_policy import delegate_hook
                with store.db:
                    store.db.execute('BEGIN IMMEDIATE')
                    delegate_hook(store.db,item['workspace'],item['client'],item['native_session'],item['hook_event'],item.get('source_event'))
                count=None;effect='delegation_only_native_body_owned_by_jsonl'
            else:count=store._deliver_import(item);effect='original_import_queue'
            outcome='duplicate' if count==0 and row['attempts'] else 'accepted'
            with store.db:store.db.execute("UPDATE delivery_inbox SET state='delivered',attempts=attempts+1,outcome=?,error=NULL WHERE id=?",(outcome,identity))
            entries.append({'id':identity,'outcome':outcome,'effect':effect,'new_jobs':count})
        except Exception:
            available=now+min(3600,60*2**min(row['attempts'],6))
            with store.db:store.db.execute("UPDATE delivery_inbox SET state='retry',attempts=attempts+1,available=?,error='delivery_failed' WHERE id=?",(available,identity))
            entries.append({'id':identity,'outcome':'failed','error':'delivery_failed','available':available})
    return {'schema':'contextrail.delivery-drain.v1','entries':entries,'remaining':store.db.execute("SELECT count(*) FROM delivery_inbox WHERE state!='delivered'").fetchone()[0]}


def defer(store,jid,reason,delay=300,*,stage='waiting',now=None,shared_stage=None):
    now=time.time() if now is None else now;available=now+max(1,delay)
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        store.db.execute('UPDATE jobs SET available=max(available,?) WHERE id=?',(available,jid))
        store.db.execute('INSERT INTO job_control(job_id,reason,next_retry,first_wait,stage,updated) VALUES(?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET reason=excluded.reason,next_retry=excluded.next_retry,stage=excluded.stage,updated=excluded.updated',
            (jid,reason,available,now,stage,now))
        if shared_stage is not None:
            if shared_stage not in {'judge','index'}:raise ValueError('Unknown shared resource stage')
            key='capture_backpressure:'+shared_stage
            old=store.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
            if old:available=max(available,json.loads(old[0])['until'])
            store.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,canonical({'until':available,'reason':reason})))
    return available


def backpressure(store,now=None):
    now=time.time() if now is None else now;result={}
    for stage in ('judge','index'):
        row=store.db.execute('SELECT value FROM meta WHERE key=?',('capture_backpressure:'+stage,)).fetchone()
        value=json.loads(row[0]) if row else {'until':None,'reason':None}
        result[stage]={**value,'active':value['until'] is not None and value['until']>now}
    return result


def cancel(store,ids,reason):
    if not isinstance(ids,list) or not 1<=len(ids)<=100 or not isinstance(reason,str) or not reason.strip():raise ValueError('Cancellation needs exact IDs and reason')
    reason=sanitize_capture(reason,'lifecycle').value[:1000];entries=[];now=time.time()
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        rows=[]
        for identity in dict.fromkeys(ids):
            require_safe_identity(identity);row=store.db.execute('SELECT * FROM jobs WHERE id=?',(identity,)).fetchone()
            if row is None:raise ValueError('Unknown cancellation target')
            rows.append(row)
        for row in rows:
            prior=row['state'];control=store.db.execute('SELECT * FROM job_control WHERE job_id=?',(row['id'],)).fetchone()
            if prior in {'saved','skipped','filtered','cancelled'}:status='already_terminal'
            elif control and control['stage']=='saving':status='too_late_to_cancel_saving'
            else:
                status='cancel_requested' if prior=='processing' else 'cancelled'
                if status=='cancelled':store.db.execute("UPDATE jobs SET state='cancelled' WHERE id=?",(row['id'],))
                store.db.execute("INSERT INTO job_control(job_id,reason,first_wait,stage,cancel_requested,updated) VALUES(?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET reason=excluded.reason,stage=excluded.stage,cancel_requested=excluded.cancel_requested,next_retry=NULL,updated=excluded.updated",(row['id'],reason,now,status,1 if status=='cancel_requested' else 0,now))
                store.db.execute('INSERT INTO job_actions(job_id,action,prior_state,reason,at) VALUES(?,?,?,?,?)',(row['id'],status,prior,reason,now))
            entries.append({'id':row['id'],'status':status})
    return {'schema':'contextrail.job-cancel.v1','entries':entries,'external_effects':'not_rolled_back; in_flight_cancellation_cooperative'}


def checkpoint(store,jid,stage):
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        row=store.db.execute('SELECT cancel_requested FROM job_control WHERE job_id=?',(jid,)).fetchone()
        if row and row[0]:
            store.db.execute("UPDATE jobs SET state='cancelled' WHERE id=?",(jid,))
            store.db.execute("UPDATE job_control SET stage='cancelled',cancel_requested=0,next_retry=NULL,updated=? WHERE job_id=?",(time.time(),jid));return True
        store.db.execute('INSERT INTO job_control(job_id,stage,updated) VALUES(?,?,?) ON CONFLICT(job_id) DO UPDATE SET stage=excluded.stage,reason=NULL,next_retry=NULL,updated=excluded.updated',(jid,stage,time.time()))
    return False


def status(store,workspace=None,*,limit=20):
    if type(limit) is not int or not 1<=limit<=100:raise ValueError('Queue status limit requires 1..100')
    db=store.db;now=time.time();db.create_function('capture_workspace',1,lambda v:workspace_key(v) if v else '')
    where=' WHERE capture_workspace(json_extract(j.payload,\'$.cwd\'))=?' if workspace is not None else ''
    args=[workspace_key(workspace)] if workspace is not None else []
    states=dict(db.execute('SELECT j.state,count(*) FROM jobs j'+where+' GROUP BY j.state',args).fetchall())
    pending_where=where+(' AND ' if where else ' WHERE ')+"j.state IN ('queued','retry','processing','ready_to_save','awaiting_index')"
    pending=db.execute('SELECT min(j.created),min(j.available),count(*) FROM jobs j'+pending_where,args).fetchone()
    entries=[dict(r) for r in db.execute('SELECT j.id,j.state,j.attempts,j.created,j.available,c.reason,c.stage,c.cancel_requested FROM jobs j LEFT JOIN job_control c ON j.id=c.job_id'+where+' ORDER BY j.created LIMIT ?',[*args,limit])]
    reasons=dict(db.execute("SELECT c.reason,count(*) FROM jobs j JOIN job_control c ON j.id=c.job_id"+pending_where+" AND c.reason IS NOT NULL GROUP BY c.reason",args).fetchall())
    dw=' WHERE workspace=?' if workspace is not None else '';delivery_states=dict(db.execute('SELECT state,count(*) FROM delivery_inbox'+dw+' GROUP BY state',args).fetchall())
    return {'schema':'contextrail.capture-queue.v1','workspace':workspace_key(workspace) if workspace is not None else None,
        'jobs':{'total':sum(states.values()),'states':states,'pending':pending[2],'oldest_pending_at':pending[0],
                'oldest_wait_seconds':max(0,now-pending[0]) if pending[0] is not None else None,'next_available':pending[1],
                'blocked_reasons':reasons,'entries':entries},'deliveries':{'states':delivery_states,'total':sum(delivery_states.values())},
        'shared_backpressure':backpressure(store,now),'retention':'unassessed_jobs_and_failed_deliveries_are_retained','observation':'current_original_queue_not_completion_or_all_native_history'}
