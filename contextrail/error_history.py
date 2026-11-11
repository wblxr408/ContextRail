"""Source-bound attempts over the original Rail evidence/task-state owner."""
from dataclasses import asdict
import json
from uuid import uuid5, NAMESPACE_URL

from .errors import InvalidRequest, Conflict, BudgetExceeded
from .models import Ref, canonical, identifier, integer
from .sanitization import sanitize_capture, require_safe_identity
from ._vendor.context_mode_errors import CATEGORIES, classify_error
from ._vendor.context_mode_scene import call_facts, result_info


TABLES=(
    'CREATE TABLE IF NOT EXISTS repair_results(scope TEXT,session TEXT,call_id TEXT,payload TEXT,PRIMARY KEY(scope,session,call_id))',
    'CREATE TABLE IF NOT EXISTS repair_cases(scope TEXT,id TEXT,session TEXT,call_id TEXT,category TEXT,payload TEXT,PRIMARY KEY(scope,id))',
    'CREATE TABLE IF NOT EXISTS repair_attempts(scope TEXT,id TEXT,case_id TEXT,session TEXT,treatment TEXT,verification TEXT,payload TEXT,PRIMARY KEY(scope,id))',
)


class ErrorHistory:
    def __init__(self,store,scope,lease,workspace):
        from .workscene import WorkScene
        self.scene=WorkScene(store,scope,lease,workspace)
        self.store,self.scope,self.lease=store,scope,lease

    def ensure(self):
        for statement in TABLES:self.store.db.execute(statement)

    def observe(self,session,call,tool,request,result,refs,*,status=None):
        """Called only with original frozen/host result evidence, in its transaction."""
        from .workscene_extract import bounded
        with self.scene.tx():
            self.store._lease(self.scope,self.lease);self.ensure();db=self.store.db
            for ref in refs:self.store.get(self.scope,ref)
            facts=call_facts({'tool':tool,'arguments':request})
            command=next((v for v in facts if v['category']=='command'),{})
            paths=[v['path'] for v in facts if v['category']=='file' and isinstance(v.get('path'),str)]
            if isinstance(request,dict) and isinstance(request.get('source_paths'),list):paths.extend(p for p in request['source_paths'] if isinstance(p,str))
            info=result_info(result);output=result.get('output');details=output
            if isinstance(details,str):
                try:details=json.loads(details)
                except (ValueError,RecursionError):details={}
            if not isinstance(details,dict):details={}
            terminal=not details.get('partial') and not details.get('timed_out')
            observed='unknown' if not terminal else 'succeeded' if info['status']=='observed' else info['status']
            if status is not None:observed=status
            safe=sanitize_capture({'request':request,'result':result},'evidence').value
            text=canonical(safe['result'])
            value={'call_id':call,'session':session,'status':observed,'exit_code':info['exit_code'],
                'command':bounded(command),'paths':paths[:32],'preview':text[:1024],
                'ref':asdict(refs[-1]),'sources':[asdict(r) for r in refs]}
            key=(self.scope.key,session,call)
            prior=db.execute('SELECT payload FROM repair_results WHERE scope=? AND session=? AND call_id=?',key).fetchone()
            if prior:
                if prior[0]!=canonical(value):raise Conflict('Result call identity has conflicting recovery evidence')
                return
            db.execute('INSERT INTO repair_results VALUES(?,?,?,?)',(*key,canonical(value)))
            if observed=='failed' or details.get('timed_out'):
                category='timeout' if details.get('timed_out') else classify_error(text[:65536],tool)
                identity=uuid5(NAMESPACE_URL,canonical([*key,'error'])).hex
                payload={**value,'id':identity,'error_category':category,'resolution':'unconfirmed'}
                db.execute('INSERT INTO repair_cases VALUES(?,?,?,?,?,?)',(self.scope.key,identity,session,call,category,canonical(payload)))
                self.scene._record('error:'+identity,'error','error:'+identity,'unconfirmed',payload,refs)
            for attempt in db.execute('SELECT id,payload FROM repair_attempts WHERE scope=? AND session=? AND verification=?',(self.scope.key,session,call)).fetchall():
                self.scene._record('verification:'+attempt['id'],'repair_verification','error:'+json.loads(attempt['payload'])['case_id'],
                    'verification_passed' if observed=='succeeded' else observed,{'attempt':attempt['id'],'verification':value,'meaning':'only_this_check_result_not_proof_of_resolution'},refs)

    def attempt(self,case_id,attempt_id,text,treatment_call,verification_call,source):
        for value in (case_id,attempt_id):identifier(value)
        for value in (treatment_call,verification_call):require_safe_identity(value)
        if not isinstance(text,str) or not text.strip() or len(text)>4096:raise InvalidRequest('Describe the attempted treatment')
        with self.scene.tx():
            self.store._lease(self.scope,self.lease);self.ensure();db=self.store.db
            case=db.execute('SELECT session,call_id FROM repair_cases WHERE scope=? AND id=?',(self.scope.key,case_id)).fetchone()
            if not case:raise InvalidRequest('Select an exact existing error case')
            self.store.get(self.scope,source)
            payload=sanitize_capture({'id':attempt_id,'case_id':case_id,'text':text,'treatment_call':treatment_call,'verification_call':verification_call,'source':asdict(source)},'evidence').value
            old=db.execute('SELECT payload FROM repair_attempts WHERE scope=? AND id=?',(self.scope.key,attempt_id)).fetchone()
            if old:
                if old[0]!=canonical(payload):raise Conflict('Attempt identity was already used')
                return {'attempt':attempt_id,'replayed':True}
            if db.execute('SELECT 1 FROM repair_results WHERE scope=? AND session=? AND call_id=?',(self.scope.key,case['session'],verification_call)).fetchone():raise Conflict('Verification evidence predates its binding')
            if verification_call==case['call_id'] or treatment_call==verification_call:raise InvalidRequest('Treatment and fresh verification must be distinct calls')
            if db.execute('SELECT 1 FROM repair_attempts WHERE scope=? AND session=? AND verification=?',(self.scope.key,case['session'],verification_call)).fetchone():raise Conflict('Verification is already bound to an attempt')
            db.execute('INSERT INTO repair_attempts VALUES(?,?,?,?,?,?,?)',(self.scope.key,attempt_id,case_id,case['session'],treatment_call,verification_call,canonical(payload)))
            self.scene._record('attempt:'+attempt_id,'repair_attempt','error:'+case_id,'awaiting',payload,(source,))
            return {'attempt':attempt_id,'replayed':False}


def read(store,scope,*,category=None,case_id=None,offset=0,limit=20,budget=8192,attempt_offset=0,attempt_limit=3):
    integer(offset);integer(limit,1);integer(budget,1)
    integer(attempt_offset);integer(attempt_limit,1)
    if case_id is not None:identifier(case_id)
    if limit>100 or attempt_limit>20 or category is not None and category not in CATEGORIES:raise InvalidRequest('Invalid error history filter')
    store._task(scope);db=store.db
    present=db.execute("SELECT 1 FROM sqlite_master WHERE name='repair_cases'").fetchone()
    where='scope=?'+(' AND category=?' if category else '');args=(scope.key,category) if category else (scope.key,)
    if case_id is not None:where+=' AND id=?';args=(*args,case_id)
    total=db.execute('SELECT count(*) FROM repair_cases WHERE '+where,args).fetchone()[0] if present else 0
    result={'schema':'contextrail.error-history.v1','entries':[],'total':total,'offset':offset,'next_offset':None,'authority':'recorded_attempts_and_check_results_not_resolution_proof'}
    cases=db.execute('SELECT * FROM repair_cases WHERE '+where+' ORDER BY rowid DESC LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall() if present else []
    for case in cases:
        value=json.loads(case['payload']);attempts=[]
        attempt_total=db.execute('SELECT count(*) FROM repair_attempts WHERE scope=? AND case_id=?',(scope.key,case['id'])).fetchone()[0]
        for row in db.execute('SELECT * FROM repair_attempts WHERE scope=? AND case_id=? ORDER BY rowid LIMIT ? OFFSET ?',(scope.key,case['id'],attempt_limit,attempt_offset)):
            attempt=json.loads(row['payload']);attempt.update(status='awaiting',treatment=None,verification=None)
            for name,column in (('treatment','treatment'),('verification','verification')):
                found=db.execute('SELECT payload FROM repair_results WHERE scope=? AND session=? AND call_id=?',(scope.key,row['session'],row[column])).fetchone()
                if found:attempt[name]=json.loads(found[0])
            if attempt['verification']:
                state=attempt['verification']['status'];attempt['status']='verification_passed' if state=='succeeded' else state
            attempts.append(attempt)
        value.update(attempts=attempts,attempt_total=attempt_total,attempt_offset=attempt_offset,
            next_attempt_offset=attempt_offset+len(attempts) if attempt_offset+len(attempts)<attempt_total else None,related_results=[])
        for row in db.execute('SELECT payload FROM repair_results WHERE scope=? AND session=? AND call_id<>? ORDER BY rowid DESC LIMIT 200',(scope.key,case['session'],case['call_id'])):
            candidate=json.loads(row[0]);same_command=bool(value['command']) and candidate['command']==value['command'];same_path=bool(set(value['paths'])&set(candidate['paths']))
            if same_command or same_path:
                value['related_results'].append({'call_id':candidate['call_id'],'ref':candidate['ref'],'status':candidate['status'],'basis':'candidate_same_command_or_file_not_causal'})
            if len(value['related_results'])==8:break
        value['related_scan_limit']=200;result['entries'].append(value)
        result['next_offset']=offset+len(result['entries']) if offset+len(result['entries'])<total else None
        if len(canonical(result).encode())>budget:result['entries'].pop();break
    result['next_offset']=offset+len(result['entries']) if offset+len(result['entries'])<total else None
    if len(canonical(result).encode())>budget or cases and not result['entries']:raise BudgetExceeded('Error history case cannot fit; increase budget')
    return result
