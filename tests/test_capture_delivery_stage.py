"""F05--F08 phase consumers against the actual existing owner and SQLite."""
import json,os
from pathlib import Path
import subprocess,sys,tempfile,unittest

APP=Path.home()/'.codex/services/engineering-memory/app'
OVERLAY=Path(__file__).resolve().parents[1]/'integrations/engineering_memory'


class CaptureDeliveryStage(unittest.TestCase):
    def run_owner(self,script,gate=True):
        with tempfile.TemporaryDirectory(prefix='rail-delivery-') as temp:
            code=r'''
import json,os,sys,time
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8');sys.path[:0]=sys.argv[1:3]
from listener import Store,Service
home=Path(sys.argv[3]);workspace=home/'workspace';workspace.mkdir()
(workspace/'.contextrail.toml').write_text(chr(10).join(('[capture]','ignore=["private/**"]','')),encoding='utf-8')
store=Store(home);source=home/'native.jsonl'
def row(typ,p):return {'type':typ,'payload':p,'timestamp':'2026-10-23T13:30:00Z'}
def meta(session='session-a'):return row('session_meta',{'id':session,'cwd':str(workspace)})
def user(text,uid='u1'):return row('response_item',{'type':'message','role':'user','id':uid,'content':[{'type':'input_text','text':text}]})
def append(records,path=source):
    with path.open('ab') as stream:
        for value in records:stream.write((json.dumps(value,ensure_ascii=False)+chr(10)).encode())
def persisted():
    return chr(10).join(str(tuple(r)) for table in ('jobs','cursors','source_events','normalized_events') for r in store.db.execute('SELECT * FROM '+table))
def privacy_gate():
    append([meta(),user('显式用户文本独立保留'),row('response_item',{'type':'function_call','call_id':'private-call','name':'Read','arguments':json.dumps({'file_path':str(workspace/'private/data.txt')})}),
        row('response_item',{'type':'function_call_output','call_id':'private-call','output':'PRIVATE_BODY_RAIL_4427'}),
        row('event_msg',{'type':'task_complete','turn_id':'t-private','last_agent_message':'PRIVATE_BODY_RAIL_4427'})])
    store.ingest(source,'codex')
    if 'PRIVATE_BODY_RAIL_4427' in persisted():print(json.dumps({'private_retained':True}));sys.exit(0)
'''
            if gate:code+='\nprivacy_gate()\n'
            code+='\n'+script
            env={**os.environ,'PYTHONPATH':os.pathsep.join((str(OVERLAY),str(APP),str(OVERLAY.parents[1])))}
            child=subprocess.run([sys.executable,'-X','utf8','-c',code,str(OVERLAY),str(APP),temp],env=env,capture_output=True,timeout=45)
            self.assertEqual(child.returncode,0,child.stderr.decode('utf-8','replace'))
            result=json.loads(child.stdout);self.assertFalse(result.get('private_retained'), 'excluded tool body reached persistent event/cursor/queue')
            return result

    def test_private_tool_and_derived_assistant_never_reach_persistence(self):
        result=self.run_owner("print(json.dumps({'text':persisted(),'states':[json.loads(r[0])['body_state'] for r in store.db.execute('SELECT metadata FROM normalized_events')]}))")
        self.assertIn('显式用户文本独立保留',result['text']);self.assertIn('policy_skip',result['states'])

    def test_metadata_and_unknown_tools_keep_no_arguments_or_output(self):
        result=self.run_owner(r'''
(workspace/'.contextrail.toml').write_text(chr(10).join(('[capture]','metadata_only=["generated/**"]','')),encoding='utf-8')
append([row('response_item',{'type':'function_call','call_id':'g1','name':'Read','arguments':json.dumps({'file_path':str(workspace/'generated/out.txt'),'extra':'META_BODY_RAIL_5531'})}),
row('response_item',{'type':'function_call_output','call_id':'g1','output':'META_BODY_RAIL_5531'}),
row('response_item',{'type':'function_call','call_id':'g2','name':'custom_tool','arguments':{'guess_path':'private/x','raw':'UNKNOWN_BODY_RAIL_5531'}}),
row('response_item',{'type':'function_call_output','call_id':'g2','output':'UNKNOWN_BODY_RAIL_5531'})]);store.ingest(source,'codex')
print(json.dumps({'text':persisted()}))
''')
        self.assertNotIn('META_BODY_RAIL_5531',result['text']);self.assertNotIn('UNKNOWN_BODY_RAIL_5531',result['text'])

    def test_invalid_project_policy_fails_closed_without_echoing_body(self):
        result=self.run_owner(r'''
(workspace/'.contextrail.toml').write_text(chr(10).join(('[capture]','ignore=[1]','')),encoding='utf-8')
append([user('INVALID_POLICY_BODY_5531','u-bad')]);store.ingest(source,'codex');print(json.dumps({'text':persisted()}))
''')
        self.assertNotIn('INVALID_POLICY_BODY_5531',result['text']);self.assertIn('invalid_policy',result['text'])

    def test_project_configuration_reuses_same_disposition_contract(self):
        result=self.run_owner(r'''
(workspace/'.contextrail.toml').write_text(chr(10).join(('[capture]','ignore=["private/**"]','metadata_only=["generated/**"]','user_mode="full"','')),encoding='utf-8')
from contextrail.project import load_project
config=load_project(workspace);print(json.dumps(config.describe()))
''')
        self.assertEqual(result['capture']['metadata_only'],['generated/**']);self.assertEqual(result['capture']['user_mode'],'full')

    def test_offline_delivery_reopen_duplicates_and_item_failures(self):
        result=self.run_owner(r'''
items=[{'id':name,'kind':'document','workspace':str(workspace),'source':str(workspace/(name+'.md')),'event_key':name,'content':'Verified because preserving one durable business effect matters. '*5,'occurred_at':'2026-10-23T13:30:00Z'} for name in ('doc-a','doc-b')]
first=store.submit_deliveries(items);store.db.close();store=Store(home)
original=store._deliver_import
def fail_second(item):
    if item['id']=='doc-b':raise ConnectionError('temporary unavailable')
    return original(item)
store._deliver_import=fail_second
a=store.drain_deliveries(limit=10);count=store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]
store._deliver_import=original
b=store.drain_deliveries(ids=['doc-b'],now=time.time()+4000);c=store.submit_deliveries(items);again=store.drain_deliveries(ids=['doc-a'])
print(json.dumps({'first':first,'a':a,'b':b,'c':c,'again':again,'count_before':count,'jobs':store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]}))
''')
        self.assertEqual([r['outcome'] for r in result['a']['entries']],['accepted','failed'])
        self.assertEqual(result['b']['entries'][0]['outcome'],'accepted');self.assertEqual(result['jobs'],2);self.assertEqual(result['count_before'],1)
        self.assertTrue(all(r['outcome']=='duplicate' for r in result['c']['entries']));self.assertEqual(result['again']['entries'][0]['outcome'],'duplicate')

    def test_delivery_id_conflict_and_hook_body_rejection(self):
        result=self.run_owner(r'''
item={'id':'d1','kind':'document','workspace':str(workspace),'source':str(workspace/'data.md'),'event_key':'revision-1','content':'Approved historical source. '*20}
store.submit_deliveries([item]);different={**item,'content':'another source'}
a=store.submit_deliveries([different])
b=store.submit_deliveries([{'id':'h-bad','kind':'native_hook','workspace':str(workspace),'client':'codex','native_session':'session-a','hook_event':'PostToolUse','body':'NOT_ALLOWED_BODY'}])
good={'id':'h-good','kind':'native_hook','workspace':str(workspace),'client':'codex','native_session':'session-a','hook_event':'PostToolUse','source_event':'call-a'}
store.submit_deliveries([good]);c=store.drain_deliveries(ids=['h-good'])
print(json.dumps({'a':a,'b':b,'c':c,'jobs':store.db.execute('SELECT count(*) FROM jobs').fetchone()[0],'text':persisted()}))
''')
        self.assertEqual(result['a']['entries'][0]['outcome'],'failed');self.assertEqual(result['b']['entries'][0]['outcome'],'failed')
        self.assertEqual(result['c']['entries'][0]['outcome'],'accepted');self.assertEqual(result['jobs'],0);self.assertNotIn('NOT_ALLOWED_BODY',result['text'])

    def test_sensitive_delivery_identity_is_rejected_without_echo(self):
        result=self.run_owner(r'''
item={'id':'sk-FAKE_CAPTURE_DELIVERY_SECRET_44332211','kind':'document','workspace':str(workspace),'source':str(workspace/'public.md'),'event_key':'r1','content':'ordinary source'}
print(json.dumps(store.submit_deliveries([item])))
''')
        self.assertEqual(result['entries'][0]['outcome'],'failed');self.assertIsNone(result['entries'][0]['id'])

    def test_model_quota_defers_without_consuming_attempt_or_dropping_job(self):
        result=self.run_owner(r'''
from history_import import enqueue
enqueue(store,workspace/'quota.md',workspace,'quota-1','Verified because queued evidence must survive a full hourly quota. '*8,None,'document')
with store.db:store.db.execute('UPDATE jobs SET available=0')
service=Service.__new__(Service);service.home=home;service.store=store;service.config={'max_calls_per_day':0,'max_calls_per_hour':0,'max_tokens_per_day':0,'vault':str(workspace)}
service.process_one();job=dict(store.db.execute('SELECT * FROM jobs').fetchone())
print(json.dumps({'job':job,'now':time.time(),'status':store.queue_status() if job['available']>time.time() else {},'calls':store.db.execute('SELECT count(*) FROM calls').fetchone()[0]}))
''',gate=False)
        self.assertGreater(result['job']['available'],result['now']);self.assertEqual(result['job']['attempts'],0);self.assertEqual(result['calls'],0)
        self.assertIn('model_budget_exhausted',json.dumps(result['status']));self.assertEqual(result['status']['jobs']['total'],1)

    def test_resource_failure_retains_backlog_and_records_retry(self):
        result=self.run_owner(r'''
from history_import import enqueue
enqueue(store,workspace/'resource.md',workspace,'resource-1','Verified because unavailable resources must retain all pending evidence. '*8,None,'document')
with store.db:store.db.execute('UPDATE jobs SET available=0')
class Runtime:
    def recall(self,*args,**kwargs):return {'results':[]}
    def observations(self,*args):return []
    def invoke(self,*args):raise ConnectionError('worker unavailable')
service=Service.__new__(Service);service.home=home;service.store=store;service.memory=Runtime();service.observer=None;service.config={'sources':[],'max_calls_per_day':100,'max_calls_per_hour':100,'max_tokens_per_day':100000,'vault':str(workspace),'resource_manager':{'enabled':True}}
service.process_one();print(json.dumps({'status':store.queue_status(),'job':dict(store.db.execute('SELECT * FROM jobs').fetchone())}))
''')
        self.assertEqual(result['job']['state'],'retry');self.assertIn('resource_unavailable',json.dumps(result['status']));self.assertEqual(result['status']['jobs']['total'],1)

    def test_shared_resource_backoff_survives_reopen_and_stops_other_model_jobs(self):
        result=self.run_owner(r'''
from history_import import enqueue
for i in range(2):enqueue(store,workspace/(str(i)+'.md'),workspace,'shared-'+str(i),'Verified because shared provider backpressure must preserve queued work without retry storms. '*5,None,'document')
with store.db:store.db.execute('UPDATE jobs SET available=0')
class Runtime:
    def __init__(self):self.calls=0
    def recall(self,*args,**kwargs):return {'results':[]}
    def observations(self,*args):return []
    def invoke(self,*args):self.calls+=1;raise ConnectionError('shared provider unavailable')
runtime=Runtime();service=Service.__new__(Service);service.home=home;service.store=store;service.memory=runtime;service.observer=None
service.config={'sources':[],'max_calls_per_day':100,'max_calls_per_hour':100,'max_tokens_per_day':100000,'vault':str(workspace),'resource_manager':{'enabled':True}}
service.process_one();store.db.close();store=Store(home);service.store=store;service.process_one()
print(json.dumps({'model_calls':runtime.calls,'status':store.queue_status(),'reserved_calls':store.db.execute('SELECT count(*) FROM calls').fetchone()[0]}))
''')
        self.assertEqual(result['model_calls'],1);self.assertEqual(result['reserved_calls'],1);self.assertEqual(result['status']['jobs']['total'],2)
        self.assertTrue(result['status']['shared_backpressure']['judge']['active'])

    def test_explicit_job_cancellation_preserves_other_work_and_source(self):
        result=self.run_owner(r'''
from history_import import enqueue
for i in range(2):enqueue(store,workspace/(str(i)+'.md'),workspace,'cancel-'+str(i),'Verified because canceling one queued item must preserve other evidence. '*5,None,'document')
ids=[r[0] for r in store.db.execute('SELECT id FROM jobs ORDER BY created')]
result=store.cancel_jobs([ids[0]],'explicit user cancellation')
print(json.dumps({'result':result,'states':[r[0] for r in store.db.execute('SELECT state FROM jobs ORDER BY created')],'sources':store.db.execute('SELECT count(*) FROM source_events').fetchone()[0],'queue':store.queue_status()}))
''')
        self.assertEqual(result['states'],['cancelled','queued']);self.assertGreater(result['sources'],1)
        self.assertEqual(result['queue']['jobs']['entries'][0]['stage'],'cancelled')

    def test_installed_owner_honors_cooperative_cancel_after_reply(self):
        result=self.run_owner(r'''
import importlib.util
spec=importlib.util.spec_from_file_location('deployed_listener',Path(sys.argv[2])/'listener.py');deployed=importlib.util.module_from_spec(spec);spec.loader.exec_module(deployed)
from history_import import enqueue
enqueue(store,workspace/'cooperative.md',workspace,'cooperative-1','Verified because in-flight cancellation must preserve usage and stop subsequent saving. '*6,None,'document')
with store.db:store.db.execute('UPDATE jobs SET available=0')
jid=store.db.execute('SELECT id FROM jobs').fetchone()[0]
class Runtime:
    def recall(self,*args,**kwargs):return {'results':[]}
    def observations(self,*args):return []
    def invoke(self,*args):
        store.cancel_jobs([jid],'cancel while model is replying')
        return {'notes':[]},{'input_tokens':40,'output_tokens':4}
service=deployed.Service.__new__(deployed.Service);service.home=home;service.store=store;service.memory=Runtime();service.observer=None
service.config={'sources':[],'max_calls_per_day':100,'max_calls_per_hour':100,'max_tokens_per_day':100000,'vault':str(workspace),'resource_manager':{'enabled':True}}
service.process_one();print(json.dumps({'state':store.db.execute('SELECT state FROM jobs WHERE id=?',(jid,)).fetchone()[0],'calls':store.db.execute('SELECT count(*),sum(tokens) FROM calls').fetchone()[:]}))
''')
        self.assertEqual(result['state'],'cancelled');self.assertEqual(result['calls'],[1,44])

    def test_same_size_replacement_does_not_reuse_old_session(self):
        result=self.run_owner(r'''
append([meta('session-a'),user('source A','u-A')]);store.ingest(source,'codex')
replacement=home/'new.jsonl';append([meta('session-b'),user('source B','u-B')],replacement);os.replace(replacement,source);store.ingest(source,'codex')
print(json.dumps({'sessions':[r[0] for r in store.db.execute("SELECT native_session FROM normalized_events WHERE role='user' ORDER BY sequence")]}))
''',gate=False)
        self.assertEqual(result['sessions'],['session-a','session-b'])

    def test_partial_line_survives_reopen_and_reports_wait(self):
        result=self.run_owner(r'''
append([meta('partial-session')]);raw=(json.dumps(user('late complete user','u-part'))+chr(10)).encode();split=len(raw)-8
with source.open('ab') as f:f.write(raw[:split])
store.ingest(source,'codex');waiting=store.stream_status();store.db.close();store=Store(home)
with source.open('ab') as f:f.write(raw[split:])
store.ingest(source,'codex');print(json.dumps({'waiting':waiting,'users':store.db.execute("SELECT count(*) FROM normalized_events WHERE native_session='partial-session' AND role='user'").fetchone()[0]}))
''')
        self.assertIn('partial_line',json.dumps(result['waiting']));self.assertEqual(result['users'],1)

    def test_rotated_old_tail_survives_new_file_first_and_non_jsonl_extension(self):
        result=self.run_owner(r'''
append([meta('rotate-old'),user('first old','old-1')]);late=(json.dumps(user('last old','old-2'))+chr(10)).encode()
with source.open('ab') as f:f.write(late[:-7])
store.ingest(source,'codex')
with source.open('ab') as f:f.write(late[-7:])
moved=home/'native.jsonl.1';source.rename(moved);append([meta('rotate-new'),user('first new','new-1')]);store.ingest(source,'codex')
store.ingest_rotated(source,moved,'codex');store.ingest_rotated(source,moved,'codex')
print(json.dumps({'users':[(r[0],json.loads(r[1])['data']['text']) for r in store.db.execute("SELECT n.native_session,json_object('data',json_extract(s.content,'$.final')) FROM normalized_events n JOIN source_events s ON n.event_id=s.id WHERE n.role='user' ORDER BY n.sequence")]}))
''')
        self.assertEqual(sorted(result['users']),sorted([['session-a','显式用户文本独立保留'],['rotate-old','first old'],['rotate-old','last old'],['rotate-new','first new']]))

    def test_failed_generation_transaction_retries_from_original_position(self):
        result=self.run_owner(r'''
import listener
append([meta('transaction-old'),user('old','tx-old')]);store.ingest(source,'codex')
replacement=home/'tx-new.jsonl';append([meta('transaction-new'),user('new','tx-new')],replacement);os.replace(replacement,source)
original=listener.capture_record
def fault(*args,**kwargs):
    original(*args,**kwargs);raise RuntimeError('interrupted capture transaction')
listener.capture_record=fault
try:store.ingest(source,'codex')
except RuntimeError:pass
listener.capture_record=original;store.ingest(source,'codex')
print(json.dumps({'users':[(r[0],r[1]) for r in store.db.execute("SELECT native_session,count(*) FROM normalized_events WHERE role='user' AND native_session LIKE 'transaction-%' GROUP BY native_session")]}))
''')
        self.assertEqual(sorted(result['users']),[['transaction-new',1],['transaction-old',1]])

    def test_oversized_line_does_not_block_following_valid_record(self):
        result=self.run_owner(r'''
append([meta('large-session')]);large=(json.dumps(user('x'*2000,'too-large'))+chr(10)).encode()
with source.open('ab') as f:f.write(large)
append([user('after oversized record','after-large')])
for _ in range(20):store.ingest(source,'codex',line_bytes=512)
zero=home/'zero-offset.jsonl';zero.write_bytes(b'x'*2048);store.ingest(zero,'codex',line_bytes=512)
print(json.dumps({'status':store.stream_status(),'pending':store.stream_status()['entries'][0]['state'],'users':store.db.execute("SELECT count(*) FROM normalized_events WHERE native_session='large-session' AND role='user'").fetchone()[0]}))
''')
        self.assertIn('oversized_line',json.dumps(result['status']));self.assertEqual(result['users'],1)
        self.assertEqual(result['pending'],'oversized_line_pending')

    def test_private_explicit_document_is_filtered_before_job_creation(self):
        result=self.run_owner(r'''
from history_import import enqueue
count=enqueue(store,workspace/'private/import.md',workspace,'private-import','PRIVATE_DOCUMENT_BODY_7781 '*40,None,'document')
print(json.dumps({'count':count,'text':persisted()}))
''')
        self.assertEqual(result['count'],0);self.assertNotIn('PRIVATE_DOCUMENT_BODY_7781',result['text'])

    def test_real_cli_queue_status_is_content_free(self):
        result=self.run_owner(r'''
command=[sys.executable,'-X','utf8',str(Path(sys.argv[1])/'listener.py'),'queue-status','--home',str(home),'--workspace',str(workspace)]
child=__import__('subprocess').run(command,capture_output=True,env=os.environ,timeout=25)
print(json.dumps({'code':child.returncode,'output':child.stdout.decode('utf-8'),'error':child.stderr.decode('utf-8')}))
''')
        self.assertEqual(result['code'],0,result['error']);payload=json.loads(result['output']);self.assertIn('jobs',payload);self.assertNotIn('PRIVATE_BODY_RAIL_4427',result['output'])

    def test_installed_learning_owner_excludes_new_queue_projection(self):
        result=self.run_owner(r'''
view=store.queue_status();raw=json.dumps(view,ensure_ascii=False)
script='import sys,json;sys.path.insert(0,sys.argv[1]);from learning_origin import project_candidate;v=json.load(sys.stdin);print(json.dumps(project_candidate({"kind":"document","final":json.dumps(v,ensure_ascii=False)})))'
child=__import__('subprocess').run([sys.executable,'-X','utf8','-c',script,sys.argv[2]],input=raw.encode(),capture_output=True,timeout=20)
if child.returncode:raise RuntimeError(child.stderr.decode())
print(child.stdout.decode())
''')
        self.assertFalse(result['_origin']['eligible_for_learning']);self.assertEqual(result['final'],'')


if __name__=='__main__':unittest.main()
