"""Source-linked projections over the original task_state and actions owners."""
from contextlib import nullcontext
from dataclasses import asdict
import json
from pathlib import Path
from uuid import uuid4,uuid5,NAMESPACE_URL

from .errors import InvalidRequest,Conflict,BudgetExceeded
from .models import Ref,canonical,identifier,integer
from .sanitization import sanitize_capture,require_safe_identity
from ._vendor.context_mode_scene import call_facts,message_facts,result_info

TABLES=(
    'CREATE TABLE IF NOT EXISTS scene_observations(scope TEXT,id TEXT,category TEXT,topic TEXT,status TEXT,inferred INTEGER,payload TEXT,sources TEXT,created REAL,PRIMARY KEY(scope,id))',
    'CREATE TABLE IF NOT EXISTS scene_topics(scope TEXT,topic TEXT,status TEXT,text TEXT,inferred INTEGER,sources TEXT,item TEXT,PRIMARY KEY(scope,topic))',
    'CREATE TABLE IF NOT EXISTS scene_expected(scope TEXT,topic TEXT,call_id TEXT,PRIMARY KEY(scope,topic))',
    'CREATE TABLE IF NOT EXISTS scene_inputs(scope TEXT,id TEXT,session TEXT,call_id TEXT,type TEXT,payload TEXT,ref TEXT,PRIMARY KEY(scope,id))',
    'CREATE TABLE IF NOT EXISTS scene_jobs(scope TEXT,id TEXT,owner TEXT,request TEXT,handle TEXT,status TEXT,timed_out INTEGER,partial_refs TEXT,sources TEXT,updated REAL,PRIMARY KEY(scope,id))',
)


class WorkScene:
    def __init__(self,store,scope,lease,workspace):
        self.store,self.scope,self.lease,self.workspace=store,scope,lease,Path(workspace).resolve()
    def tx(self):return nullcontext() if self.store.db.in_transaction else self.store.transaction()
    def ensure(self):
        for statement in TABLES:self.store.db.execute(statement)
    def _record(self,key,category,topic,status,payload,sources,*,inferred=False):
        db=self.store.db;self.ensure();identity=uuid5(NAMESPACE_URL,key).hex
        if db.execute('SELECT 1 FROM scene_observations WHERE scope=? AND id=?',(self.scope.key,identity)).fetchone():return False
        for ref in sources:self.store.get(self.scope,ref)
        from .workscene_extract import bounded
        safe=bounded(sanitize_capture(payload,'evidence').value);refs=canonical([asdict(r) for r in sources]);now=self.store.clock()
        db.execute('INSERT INTO scene_observations VALUES(?,?,?,?,?,?,?,?,?)',(self.scope.key,identity,category,topic,status,int(inferred),canonical(safe),refs,now))
        text=safe.get('text') or canonical(safe)
        item=uuid4().hex;prior=None
        if category=='task':
            prior=db.execute('SELECT item FROM scene_topics WHERE scope=? AND topic=?',(self.scope.key,topic)).fetchone()
            db.execute('INSERT OR REPLACE INTO scene_topics VALUES(?,?,?,?,?,?,?)',(self.scope.key,topic,status,text,int(inferred),refs,item))
        else:
            prior=db.execute("SELECT id AS item FROM task_state_items WHERE scope=? AND kind='working_scene' AND status='active' ORDER BY created_at DESC LIMIT 1",(self.scope.key,)).fetchone()
            text='工作现场已保存；使用context.workscene按文件/类别/任务回读，来源见本项Ref。操作日志不全部装入必需上下文。'
        if prior:db.execute("UPDATE task_state_items SET status='superseded' WHERE scope=? AND id=?",(self.scope.key,prior['item']))
        db.execute('INSERT INTO task_state_items VALUES(?,?,?,?,?,?,?,?,?)',(self.scope.key,item,status if category=='task' else 'working_scene',text,refs,'active',int(inferred),canonical([prior['item']] if prior else []),now))
        db.execute('UPDATE tasks SET version=version+1 WHERE scope=?',(self.scope.key,))
        self.store._event(self.scope,'workscene.projected',observation=identity,category=category,status=status,inferred=inferred)
        return True

    def host_result(self,manifest_ref):
        value=json.loads(self.store.get(self.scope,manifest_ref).content)
        if value.get('schema')!='contextrail.tool-result.v1':raise InvalidRequest('Expected original result manifest')
        call=value['call_id'];base={'argv':value['argv'],'cwd':value['cwd'],'status':value['status'],'call_id':call}
        with self.tx():
            self.store._lease(self.scope,self.lease);self.ensure()
            state='observed' if value['status']=='succeeded' else 'unknown' if value['status'] in {'partial','unknown','timed_out'} else 'failed'
            facts=call_facts({'tool':value['tool'],'arguments':{'argv':value['argv'],'cwd':value['cwd']}})
            for path in value['source_paths']:facts.append({'category':'file','path':path,'operation':'host_declared_involvement'})
            if value.get('environment'):facts.append({'category':'environment','environment':value['environment'],'basis':'host_attested_environment'})
            for index,fact in enumerate(facts):
                if fact['category']=='git' and value['exit_code']==0:
                    text=self.store.get(self.scope,Ref(**value['stdout']['ref'])).content.decode('utf-8','replace')
                    fact.update(result_info({'output':{'exit_code':0,'stdout':text}}))
                self._record('host:'+value['source_session']+':'+call+':'+str(index),fact['category'],'call:'+call,state,{**base,**fact},(manifest_ref,))
            job=self.store.db.execute('SELECT status,owner FROM scene_jobs WHERE scope=? AND id=?',(self.scope.key,call)).fetchone()
            if job and job['owner']==self.lease.session and job['status'] not in {'succeeded','failed','cancelled'} and value['exit_code'] is not None and not value['partial'] and not value['timed_out']:
                self.finished(call,call)

    def native(self,event,ref):
        frozen=json.loads(self.store.get(self.scope,ref).content)
        if frozen!=event:raise Conflict('Native projection differs from frozen source')
        with self.tx():
            self.store._lease(self.scope,self.lease);self.ensure();db=self.store.db
            session=canonical([event['client'],event['native_session'],event['workspace']]);kind=event['event_type'];call=event.get('call_id')
            db.execute('INSERT OR IGNORE INTO scene_inputs VALUES(?,?,?,?,?,?,?)',(self.scope.key,event['id'],session,call,kind,canonical(event),canonical(asdict(ref))))
            if kind=='tool.call':
                for i,fact in enumerate(call_facts(event['data'])):
                    category=fact['category'];status=fact.get('status','requested')
                    self._record(event['id']+':request:'+str(i),category,fact.get('topic','call:'+str(call)),status,fact,(ref,),inferred=fact.get('inferred',False))
            elif event['role'] in {'user','assistant'}:
                for i,fact in enumerate(message_facts(event['data'],event['role'])):
                    self._record(event['id']+':message:'+str(i),'task',fact['topic'],fact['status'],fact,(ref,),inferred=fact['inferred'])
            if call and kind in {'tool.call','tool.result'}:
                calls=db.execute("SELECT payload,ref FROM scene_inputs WHERE scope=? AND session=? AND call_id=? AND type='tool.call' ORDER BY rowid LIMIT 1",(self.scope.key,session,call)).fetchone()
                results=db.execute("SELECT payload,ref FROM scene_inputs WHERE scope=? AND session=? AND call_id=? AND type='tool.result' ORDER BY rowid LIMIT 1",(self.scope.key,session,call)).fetchone()
                if calls and results:
                    source=json.loads(calls['payload']);result=json.loads(results['payload']);status=result_info(result['data'])
                    refs=(Ref(**json.loads(calls['ref'])),Ref(**json.loads(results['ref'])))
                    for i,fact in enumerate(call_facts(source['data'])):
                        if fact['category']=='task':continue
                        payload={**fact,'result':status,'cwd':source['workspace'],'call_id':call}
                        self._record(source['id']+':result:'+result['id']+':'+str(i),fact['category'],'call:'+call,status['status'],payload,refs)

    def declare(self,topic,status,text,source,*,expected_call_id=None,inferred=False):
        identifier(topic)
        if status not in {'planned','blocked','excluded','decision'}:raise InvalidRequest('Unsupported declared task status')
        with self.tx():
            self.store._lease(self.scope,self.lease);self.ensure()
            if expected_call_id:
                require_safe_identity(expected_call_id)
                if self.store.db.execute('SELECT 1 FROM heads WHERE scope=? AND name=?',(self.scope.key,'tool-result/'+expected_call_id+'/result')).fetchone():raise Conflict('Completion evidence predates its task binding')
                self.store.db.execute('INSERT OR REPLACE INTO scene_expected VALUES(?,?,?)',(self.scope.key,topic,expected_call_id))
            return self._record('declare:'+topic+':'+status+':'+canonical(asdict(source)),'task',topic,status,{'text':text},(source,),inferred=inferred)

    def _result(self,call):
        require_safe_identity(call);ref=Ref('tool-result/'+call+'/result',1);value=json.loads(self.store.get(self.scope,ref).content)
        if value.get('call_id')!=call or value.get('source_session')!=self.lease.session:raise Conflict('Result is not from this host owner')
        if value.get('exit_code') is None or value.get('partial') or value.get('timed_out'):raise Conflict('Result does not confirm a terminal effect')
        return value,ref
    def complete(self,topic,call):
        with self.tx():
            self.store._lease(self.scope,self.lease);self.ensure()
            bound=self.store.db.execute('SELECT call_id FROM scene_expected WHERE scope=? AND topic=?',(self.scope.key,topic)).fetchone()
            if not bound or bound[0]!=call:raise Conflict('Task completion requires its previously bound result call')
            value,ref=self._result(call)
            if value['exit_code']!=0:raise Conflict('Failed output does not complete the task')
            return self._record('completed:'+topic+':'+call,'task',topic,'completed',{'text':'已由绑定输出确认：'+topic,'call_id':call},(ref,))

    def reserve(self,identity,*,argv,source,background=None):
        require_safe_identity(identity)
        if type(background) is not bool or not isinstance(argv,list) or not argv or len(argv)>256 or any(not isinstance(a,str) for a in argv):raise InvalidRequest('Explicit foreground/background choice and argv required')
        request=sanitize_capture({'argv':argv,'cwd':str(self.workspace),'background':background},'evidence').value
        if len(canonical(request).encode())>65536:raise InvalidRequest('Host action request too large')
        with self.tx():
            self.store._lease(self.scope,self.lease);self.ensure();self.store.get(self.scope,source)
            row=self.store.db.execute('SELECT state FROM actions WHERE scope=? AND id=?',(self.scope.key,identity)).fetchone()
            if row:return {'action':identity,'execute_once':False,'automatic_retry':False,'state':row[0]}
            self.store.db.execute("INSERT INTO actions VALUES(?,?,'pending')",(self.scope.key,identity))
            self.store.db.execute('INSERT INTO scene_jobs VALUES(?,?,?,?,?,?,?,?,?,?)',(self.scope.key,identity,self.lease.session,canonical(request),'{}','reserved',0,'[]',canonical([asdict(source)]),self.store.clock()))
            self.store._event(self.scope,'action.reserved',action=identity,execution_mode='background' if background else 'foreground')
        return {'action':identity,'execute_once':True,'automatic_retry':False}
    def _job(self,identity):
        self.store._lease(self.scope,self.lease);self.ensure()
        row=self.store.db.execute('SELECT * FROM scene_jobs WHERE scope=? AND id=?',(self.scope.key,identity)).fetchone()
        if not row or row['owner']!=self.lease.session:raise Conflict('Action is not owned by this host session')
        return row
    def started(self,identity,*,host_session,pid=None,process_group=False):
        require_safe_identity(host_session);handle={'host_session':host_session}
        if pid is not None:
            from .process_identity import inspect_process
            current=inspect_process(pid)
            if current.get('state')!='running' or not current.get('created'):raise InvalidRequest('Register a live exact process or retain its opaque host session')
            handle.update(pid=pid,created=current['created'],process_group=bool(process_group))
        with self.tx():
            row=self._job(identity)
            if row['status']!='reserved':
                if json.loads(row['handle'])==handle:return
                raise Conflict('Action was already bound to another handle')
            self.store.db.execute('UPDATE scene_jobs SET handle=?,status=?,updated=? WHERE scope=? AND id=?',(canonical(handle),'running' if pid else 'unknown',self.store.clock(),self.scope.key,identity))
    def partial(self,identity,*,stdout,stderr,part_id=None):
        safe=sanitize_capture({'stdout':stdout,'stderr':stderr},'tool').value
        with self.tx():
            row=self._job(identity);refs=json.loads(row['partial_refs']);number=part_id or str(len(refs))
            require_safe_identity(number);name='host-actions/'+identity+'/partial/'+number
            old=self.store.db.execute('SELECT revision FROM heads WHERE scope=? AND name=?',(self.scope.key,name)).fetchone()
            if old:
                if self.store.get(self.scope,Ref(name,old[0])).content!=canonical(safe).encode():raise Conflict('Partial identity differs')
                return Ref(name,old[0])
            ref=self.store._put(self.scope,name,canonical(safe).encode(),expected_revision=0,media_type='application/json')
            refs.append(asdict(ref));self.store.db.execute('UPDATE scene_jobs SET partial_refs=?,updated=? WHERE scope=? AND id=?',(canonical(refs),self.store.clock(),self.scope.key,identity));return ref
    def timed_out(self,identity):
        with self.tx():self._job(identity);self.store.db.execute('UPDATE scene_jobs SET timed_out=1,updated=? WHERE scope=? AND id=?',(self.store.clock(),self.scope.key,identity))
    def recover(self,identity):
        with self.tx():
            row=self._job(identity);value=job_value(row);status=row['status']
            legacy=self.store.db.execute('SELECT state FROM actions WHERE scope=? AND id=?',(self.scope.key,identity)).fetchone()
            if status not in {'succeeded','failed','cancelled'} and legacy and legacy[0] in {'succeeded','failed','cancelled'}:
                request=json.loads(row['request']);request['terminal_basis']='original_host_action_ledger'
                self.store.db.execute('UPDATE scene_jobs SET status=?,request=? WHERE scope=? AND id=?',(legacy[0],canonical(request),self.scope.key,identity))
                return {**value,'status':legacy[0],'terminal_basis':'original_host_action_ledger'}
            if status in {'succeeded','failed','cancelled'}:return value
            handle=value['handle']
            if 'pid' in handle:
                from .process_identity import inspect_process
                actual=inspect_process(handle['pid'])
                status='identity_mismatch' if actual.get('created') and actual['created']!=handle['created'] else 'running' if actual.get('state')=='running' else 'stopped_outcome_unknown' if actual.get('state') in {'stopped','absent'} else 'unknown'
            else:status='unknown'
            self.store.db.execute('UPDATE scene_jobs SET status=?,updated=? WHERE scope=? AND id=?',(status,self.store.clock(),self.scope.key,identity))
            return {**value,'status':status,'observation':'host_handle_checked_now; effect_unknown_without_receipt'}
    def finished(self,identity,call):
        with self.tx():
            row=self._job(identity)
            if call!=identity:raise Conflict('Result call does not match this action')
            value,ref=self._result(call);status='succeeded' if value['exit_code']==0 else 'failed'
            if row['status'] in {'succeeded','failed','cancelled'}:
                if row['status']==status:return
                raise Conflict('Action terminal outcome differs')
            self.store.db.execute('UPDATE actions SET state=? WHERE scope=? AND id=?',(status,self.scope.key,identity))
            self.store.db.execute('UPDATE scene_jobs SET status=?,sources=?,updated=? WHERE scope=? AND id=?',(status,canonical([*json.loads(row['sources']),asdict(ref)]),self.store.clock(),self.scope.key,identity))
            self.store.db.execute('UPDATE tasks SET version=version+1 WHERE scope=?',(self.scope.key,))
            self.store._event(self.scope,'action.finished',action=identity,state=status)
    def cancel(self,identity):
        row=self._job(identity)
        if row['status'] in {'succeeded','failed','cancelled'}:return job_value(row)
        handle=json.loads(row['handle'])
        if 'pid' not in handle:outcome={'status':'cancel_requested'}
        else:
            from .process_identity import cancel_tree
            try:outcome=cancel_tree(handle)
            except (OSError,TimeoutError):outcome={'status':'cancel_requested','reason':'host_control_unavailable'}
        with self.tx():
            row=self._job(identity);status=outcome['status'];request=json.loads(row['request']);request['cancel_requested']=True
            self.store.db.execute('UPDATE scene_jobs SET status=?,request=?,updated=? WHERE scope=? AND id=?',(status,canonical(request),self.store.clock(),self.scope.key,identity))
            if status=='cancelled':
                self.store.db.execute("UPDATE actions SET state='cancelled' WHERE scope=? AND id=?",(self.scope.key,identity))
                self.store.db.execute('UPDATE tasks SET version=version+1 WHERE scope=?',(self.scope.key,))
            self.store._event(self.scope,'action.cancel_observed',action=identity,state=status)
        return {**self.recover(identity),'cancel_observation':outcome}
    def read(self,**options):return read(self.store,self.scope,**options)
    def export_state(self,output):
        self.store._lease(self.scope,self.lease)
        output=Path(output).resolve()
        if not output.is_relative_to(self.workspace):raise InvalidRequest('STATE export must stay in declared workspace')
        data=self.read(limit=1000,budget=16*1024*1024)
        if data['next_offset'] is not None:raise BudgetExceeded('Export is incomplete; select a smaller workscene')
        output.mkdir(parents=True,exist_ok=True);target=output/'STATE.md';marker='<!-- contextrail-workscene:v1 -->'
        if target.exists():
            old=target.read_text(encoding='utf-8')
            if not (old.startswith(marker) or old.startswith('---\nmanaged_by: context-rail\ntype: context_state_projection\n') and marker in old[:2048]):raise Conflict('Refusing to replace human STATE')
        row=self.store.db.execute('SELECT id FROM snapshots WHERE scope=? AND version=? ORDER BY rowid DESC LIMIT 1',(self.scope.key,self.store.task(self.scope)['version'])).fetchone()
        snapshot=row[0] if row else self.store.snapshot(self.scope,self.lease,())
        text='---\nmanaged_by: context-rail\ntype: context_state_projection\nsource_snapshot: '+json.dumps(snapshot)+'\n---\n'+marker+'\n# 工作现场\n\n'
        for group,rows in [('Verified/source declarations',[r for r in data['entries'] if not r['inferred']]),('Inferred',[r for r in data['entries'] if r['inferred']]),('Topics',data['topics']),('Running/unknown jobs',data['jobs'])]:text+='## '+group+'\n\n'+canonical(rows)+'\n\n'
        for path,body in ((target,text.encode()),(output/'scene.json',canonical(data).encode())):
            temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp');temporary.write_bytes(body);temporary.replace(path)
        return {'state':str(target),'json':str(output/'scene.json'),'scope':asdict(self.scope)}


def job_value(row):
    value=dict(row)
    for key in ('request','handle','partial_refs','sources'):value[key]=json.loads(value[key])
    value.update(automatic_retry=False,timed_out=bool(value['timed_out']));return value


def read(store,scope,*,offset=0,limit=20,budget=8192,query=None,category=None,topic=None):
    integer(offset);integer(limit,1);integer(budget,1)
    if limit>1000:raise InvalidRequest('Workscene limit exceeds 1000')
    for value,maximum in ((query,2048),(category,128),(topic,512)):
        if value is not None and (not isinstance(value,str) or not value.strip() or len(value)>maximum):raise InvalidRequest('Invalid workscene filter')
    store._task(scope);exists=store.db.execute("SELECT 1 FROM sqlite_master WHERE name='scene_observations'").fetchone();all_rows=[]
    if exists:
        for r in store.db.execute('SELECT * FROM scene_observations WHERE scope=? ORDER BY created,rowid',(scope.key,)):
            all_rows.append(('entries',{**dict(r),'payload':json.loads(r['payload']),'sources':json.loads(r['sources']),'inferred':bool(r['inferred'])}))
        for r in store.db.execute('SELECT * FROM scene_topics WHERE scope=? ORDER BY topic',(scope.key,)):all_rows.append(('topics',{**dict(r),'sources':json.loads(r['sources']),'inferred':bool(r['inferred'])}))
        for r in store.db.execute('SELECT * FROM scene_jobs WHERE scope=? ORDER BY id',(scope.key,)):all_rows.append(('jobs',job_value(r)))
    def strings(value):
        if isinstance(value,str):return [value]
        if isinstance(value,dict):return [s for v in value.values() for s in strings(v)]
        if isinstance(value,list):return [s for v in value for s in strings(v)]
        return []
    all_rows=[(g,v) for g,v in all_rows if (category is None or v.get('category', 'task' if g=='topics' else 'job')==category)
              and (topic is None or v.get('topic',v.get('id'))==topic)
              and (query is None or any(query.casefold() in s.casefold() for s in strings(v)))]
    result={'schema':'contextrail.workscene.v1','entries':[],'topics':[],'jobs':[],'total':len(all_rows),'offset':offset,'next_offset':None,'observation':'stored_scene_not_fresh_process_liveness; host_recover_checks_handle','automatic_retry':False}
    selected=0
    for group,value in all_rows[offset:offset+limit]:
        result[group].append(value);selected+=1;result['next_offset']=offset+selected if offset+selected<len(all_rows) else None
        if len(canonical(result).encode())>budget:result[group].pop();selected-=1;break
    result['next_offset']=offset+selected if offset+selected<len(all_rows) else None
    if len(canonical(result).encode())>budget or not selected and offset<len(all_rows):raise BudgetExceeded('Workscene entry cannot fit; increase response budget')
    return result
