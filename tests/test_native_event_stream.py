"""E01 consumer contracts for recoverable native evidence, independent of learning."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

APP=Path.home()/'.codex/services/engineering-memory/app'
OVERLAY=Path(__file__).resolve().parents[1]/'integrations/engineering_memory'


class NativeEventStreamTests(unittest.TestCase):
    def run_memory(self,script,python=None):
        with tempfile.TemporaryDirectory(prefix='rail-native-stream-') as temp:
            code='''import json,sys,time
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path[:0]=sys.argv[1:3]
from listener import Store
home=Path(sys.argv[3]);workspace=home/'workspace';workspace.mkdir()
source=home/'native.jsonl'
def append(rows):
    with source.open('ab') as stream:
        for row in rows:stream.write((json.dumps(row)+'\\n').encode())
def codex(kind,payload,stamp='2026-10-23T04:00:00Z'):
    return {'type':kind,'timestamp':stamp,'payload':payload}
meta=codex('session_meta',{'id':'native-session','cwd':str(workspace)})
store=Store(home)
'''+script
            child=subprocess.run([str(python or sys.executable),'-c',code,str(OVERLAY),str(APP),temp],text=True,encoding='utf-8',capture_output=True,timeout=45)
            self.assertEqual(child.returncode,0,child.stderr)
            return json.loads(child.stdout)

    def test_user_request_survives_reopen_without_final_or_learning_job(self):
        result=self.run_memory('''append([meta,codex('response_item',{'type':'message','id':'user1','role':'user','content':[{'type':'input_text','text':'恢复这条没有final的中文请求🙂'}]})])
store.ingest(source,'codex');store.db.close();store=Store(home)
from event_identity import identity_status
report=identity_status(store.db)
requests=[e for e in report['entries'] if e['event']['coordinates']['namespace']=='codex/user.message']
print(json.dumps({'requests':requests,'jobs':store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]}))
''')
        self.assertEqual(len(result['requests']),1,'request without final was not durably recoverable')
        self.assertEqual(result['jobs'],0,'raw request was incorrectly promoted to a learning candidate')

    def test_exact_request_text_and_time_survive_source_removal(self):
        result=self.run_memory('''append([meta,codex('response_item',{'type':'message','id':'user1','role':'user','content':[{'type':'input_text','text':'完整中文🙂请求\\r\\npassword=FAKE_EVENT_PASS_40315'}]},None)])
store.ingest(source,'codex');source.unlink();store.db.close();store=Store(home)
from native_event_stream import list_events,get_event,read_event_range
page=list_events(store.db,store.event_workspace(str(workspace)),'codex','native-session',role='user')
event=get_event(store.db,store.event_workspace(str(workspace)),'codex','native-session',page['entries'][0]['id'])
encoded=json.dumps(event,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
start=encoded.index('中文'.encode())+1
part=read_event_range(store.db,store.event_workspace(str(workspace)),'codex','native-session',event['id'],start=start,end=start+4)
print(json.dumps({'event':event,'part':part}))
''')
        event=result['event']
        self.assertIn('完整中文🙂请求\r\n',event['data']['text'])
        self.assertFalse('FAKE_EVENT_PASS_40315' in json.dumps(result))
        self.assertEqual(event['source_time_state'],'missing')
        self.assertIsNone(event['occurred_at'])
        self.assertGreater(event['observed_at'],0)
        self.assertEqual(result['part']['encoding'],'base64')
        import base64
        self.assertEqual(base64.b64decode(result['part']['content']),'中文'.encode()[1:5])

    def test_codex_call_result_final_and_lifecycle_remain_distinct(self):
        result=self.run_memory('''append([meta,codex('event_msg',{'type':'task_started','turn_id':'turn1'}),
codex('response_item',{'type':'function_call','id':'f1','call_id':'call1','name':'exec_command','arguments':'{"command":"example","password":"FAKE_TOOL_PASS_40215"}'}),
codex('response_item',{'type':'function_call_output','id':'o1','call_id':'call1','output':'Error: ordinary output text'}),
codex('response_item',{'type':'message','id':'a1','role':'assistant','phase':'final_answer','content':[{'type':'output_text','text':'最终回复保留'}]}),
codex('event_msg',{'type':'task_complete','turn_id':'turn1','last_agent_message':'最终回复保留'})])
store.ingest(source,'codex')
from native_event_stream import list_events,get_event
page=list_events(store.db,store.event_workspace(str(workspace)),'codex','native-session',budget=65536)
events=[get_event(store.db,store.event_workspace(str(workspace)),'codex','native-session',x['id']) for x in page['entries']]
print(json.dumps({'events':events,'jobs':store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]}))
''')
        events={e['event_type']:e for e in result['events']}
        self.assertEqual(events['tool.call']['data']['tool'],'Bash')
        self.assertEqual(events['tool.call']['data']['original_tool'],'exec_command')
        self.assertEqual(events['tool.call']['call_id'],events['tool.result']['call_id'])
        self.assertEqual(events['tool.result']['data']['error_state'],'unknown')
        self.assertEqual(events['assistant.final']['data']['text'],'最终回复保留')
        self.assertTrue(all(e['turn_id']=='turn1' for e in result['events'] if e['event_type']!='session.start'))
        self.assertEqual(result['jobs'],1)
        self.assertFalse('FAKE_TOOL_PASS_40215' in json.dumps(result))

    def test_claude_tool_result_is_not_a_second_user_request(self):
        result=self.run_memory('''base={'sessionId':'claude-session','cwd':str(workspace),'timestamp':'2026-10-23T12:00:00+08:00'}
append([{**base,'type':'user','uuid':'u1','promptId':'prompt1','message':{'content':'请处理这条用户请求'}},
{**base,'type':'assistant','uuid':'a1','message':{'id':'msg1','content':[{'type':'thinking','thinking':'PRIVATE_THOUGHT_CANARY'},{'type':'tool_use','id':'call1','name':'shell_command','input':{'command':'example'}}],'stop_reason':'tool_use'}},
{**base,'type':'user','uuid':'u2','message':{'content':[{'type':'tool_result','tool_use_id':'call1','content':'failed explicitly','is_error':True}]}},
{**base,'type':'assistant','uuid':'a2','message':{'id':'msg2','content':[{'type':'text','text':'final public reply'}],'stop_reason':'end_turn'}}])
store.ingest(source,'claude')
from native_event_stream import list_events,get_event
page=list_events(store.db,store.event_workspace(str(workspace)),'claude','claude-session',budget=65536)
events=[get_event(store.db,store.event_workspace(str(workspace)),'claude','claude-session',e['id']) for e in page['entries']]
print(json.dumps({'events':events,'jobs':store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]}))
''')
        self.assertEqual([e['event_type'] for e in result['events']],['user.message','tool.call','tool.result','assistant.final'])
        self.assertEqual(result['events'][2]['data']['error_state'],'source_error')
        self.assertEqual(result['events'][0]['occurred_at'],'2026-10-23T04:00:00+00:00')
        self.assertFalse('PRIVATE_THOUGHT_CANARY' in json.dumps(result))
        self.assertEqual(result['jobs'],1)

    def test_raw_commit_failure_keeps_cursor_replayable(self):
        result=self.run_memory('''import sqlite3
append([meta,codex('response_item',{'type':'message','id':'u1','role':'user','content':'recover after rollback'})])
store.db.execute("CREATE TRIGGER fail_event BEFORE INSERT ON normalized_events WHEN NEW.role='user' BEGIN SELECT RAISE(ABORT,'event failed'); END")
try:store.ingest(source,'codex')
except sqlite3.IntegrityError:pass
failed={name:store.db.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ('source_events','normalized_events','cursors')}
store.db.execute('DROP TRIGGER fail_event');store.ingest(source,'codex');store.backfill(source,'codex')
count=store.db.execute("SELECT count(*) FROM normalized_events WHERE role='user'").fetchone()[0]
print(json.dumps({'failed':failed,'count':count}))
''')
        self.assertEqual(result['failed'],{'source_events':0,'normalized_events':0,'cursors':0})
        self.assertEqual(result['count'],1)

    def test_same_raw_event_changed_body_is_conflict_and_scopes_do_not_mix(self):
        result=self.run_memory('''user=lambda text:codex('response_item',{'type':'message','id':'u1','role':'user','content':text})
append([meta,user('first request'),user('changed request')]);store.ingest(source,'codex')
other=home/'other';other.mkdir();newmeta={**meta,'payload':{'id':'native-session','cwd':str(other)}}
append([newmeta,user('other workspace')]);store.ingest(source,'codex')
from native_event_stream import list_events,get_event
page=list_events(store.db,store.event_workspace(str(workspace)),'codex','native-session',role='user')
first=get_event(store.db,store.event_workspace(str(workspace)),'codex','native-session',page['entries'][0]['id'])
denied=False
try:get_event(store.db,store.event_workspace(str(other)),'codex','native-session',first['id'])
except ValueError:denied=True
print(json.dumps({'first':first,'total':page['total'],'denied':denied,'conflicts':store.db.execute('SELECT count(*) FROM source_event_conflicts').fetchone()[0]}))
''')
        self.assertEqual(result['total'],1)
        self.assertEqual(result['first']['data']['text'],'first request')
        self.assertTrue(result['denied'])
        self.assertEqual(result['conflicts'],1)

    def test_unknown_time_omissions_and_reasoning_are_explicit(self):
        result=self.run_memory('''append([meta,
codex('response_item',{'type':'message','id':'a0','role':'assistant','phase':'analysis','content':'PRIVATE_ANALYSIS_CANARY'}),
codex('response_item',{'type':'message','id':'u1','role':'user','content':[{'type':'image','data':'IMAGE_CANARY'},{'type':'input_text','text':'visible request'}]},'2026-10-23T12:00:00'),
codex('response_item',{'type':'message','id':'u2','role':'user','content':'next request'},'invalid time')])
store.ingest(source,'codex')
from native_event_stream import list_events,get_event
page=list_events(store.db,store.event_workspace(str(workspace)),'codex','native-session',budget=65536)
events=[get_event(store.db,store.event_workspace(str(workspace)),'codex','native-session',e['id']) for e in page['entries']]
print(json.dumps(events))
''')
        self.assertFalse('PRIVATE_ANALYSIS_CANARY' in json.dumps(result))
        self.assertFalse('IMAGE_CANARY' in json.dumps(result))
        users=[e for e in result if e['role']=='user']
        self.assertEqual([e['source_time_state'] for e in users],['ambiguous_timezone','invalid'])
        self.assertEqual(users[0]['data']['omitted_blocks'],['image'])
        self.assertEqual(users[0]['body_state'],'partial_blocks_omitted')
        self.assertTrue(all(e['occurred_at'] is None for e in users))

    def test_rail_cli_imports_request_and_rereads_exact_processed_evidence(self):
        result=self.run_memory('''append([meta,codex('response_item',{'type':'message','id':'u1','role':'user','content':'最后未完成请求🙂'})])
store.ingest(source,'codex')
import subprocess
(workspace/'.contextrail.toml').write_text('[project]\\nid="stream-fixture"\\ntask="recover"\\n',encoding='utf-8')
def cli(command,*args):
    return subprocess.run([sys.executable,'-m','contextrail',command,'--workspace',str(workspace),'--session','owner',*args],capture_output=True,text=True,encoding='utf-8',timeout=25)
init=cli('project-init','--objective','recover native input')
if init.returncode:raise RuntimeError(init.stderr)
imported=cli('event-import','--epoch','0','--memory-home',str(home),'--client','codex','--native-session','native-session')
if imported.returncode:print(json.dumps({'code':imported.returncode,'error':imported.stderr}))
else:
    receipt=json.loads(imported.stdout)
    from contextrail import Store as RailStore,ContextTools
    from contextrail.project import load_project
    config=load_project(workspace)
    with RailStore(config.store) as rail:
        tools=ContextTools(rail,config.scope,'owner')
        read=[json.loads(tools.call('context.get',e['body_ref'])['content']) for e in receipt['entries']]
    again=cli('event-import','--epoch','0','--memory-home',str(home),'--client','codex','--native-session','native-session')
    print(json.dumps({'code':0,'receipt':receipt,'read':read,'again':json.loads(again.stdout)}))
''')
        self.assertEqual(result['code'],0,result.get('error'))
        self.assertTrue(any(e['data'].get('text')=='最后未完成请求🙂' for e in result['read']))
        self.assertEqual(result['again']['imported'],0)

    def test_oversized_record_has_durable_omission_instead_of_disappearing(self):
        result=self.run_memory('''append([meta,codex('response_item',{'type':'message','id':'u1','role':'user','content':'x'*(4*1024*1024+10)})])
store.ingest(source,'codex')
from native_event_stream import list_events,get_event
page=list_events(store.db,store.event_workspace(str(workspace)),'codex','native-session',role='user')
event=get_event(store.db,store.event_workspace(str(workspace)),'codex','native-session',page['entries'][0]['id'])
print(json.dumps(event))
''')
        self.assertEqual(result['body_state'],'omitted_limit_or_format')
        self.assertEqual(result['data'],{'omitted':'capture_text_limit'})

    def test_local_stream_and_byte_reader_work_without_final(self):
        result=self.run_memory('''append([meta,codex('response_item',{'type':'message','id':'u1','role':'user','content':'recover through CLI'})]);store.ingest(source,'codex')
import subprocess,os
env=os.environ.copy();env['PYTHONPATH']=os.pathsep.join([str(Path.cwd()),sys.argv[2]])
base=[sys.executable,str(Path(sys.argv[1])/'listener.py')]
selection=['--home',str(home),'--workspace',str(workspace),'--client','codex','--native-session','native-session']
page=subprocess.run([*base,'stream',*selection,'--role','user'],env=env,capture_output=True,text=True,encoding='utf-8',timeout=15)
if page.returncode:raise RuntimeError(page.stdout+page.stderr)
event_id=json.loads(page.stdout)['entries'][0]['id']
read=subprocess.run([*base,'stream-get',*selection,'--event-id',event_id],env=env,capture_output=True,text=True,encoding='utf-8',timeout=15)
if read.returncode:raise RuntimeError(read.stdout+read.stderr)
print(read.stdout)
''')
        self.assertEqual(json.loads(result['content'])['data']['text'],'recover through CLI')

    def test_rail_import_lease_freeze_and_failed_second_artifact_are_atomic(self):
        result=self.run_memory('''append([meta,codex('response_item',{'type':'message','id':'u1','role':'user','content':'request'})]);store.ingest(source,'codex')
from contextrail import Store as RailStore,Target,Lease
from contextrail.project import load_project
from contextrail.event_bridge import import_events
from contextrail.errors import AccessDenied,Conflict,Unavailable
config=load_project(workspace);config.store.parent.mkdir(parents=True)
with RailStore(config.store) as rail:
    owner=rail.create_task(config.scope,Target('owner','host','external'),'recover',allowed_providers=('host',))
    failures=[]
    try:import_events(rail,config,Lease('other',0),home,'codex','native-session')
    except AccessDenied:failures.append('lease')
    with rail.db:rail.db.execute('UPDATE tasks SET handoff=? WHERE scope=?',('fixture-frozen',config.scope.key))
    try:import_events(rail,config,owner,home,'codex','native-session')
    except Conflict:failures.append('freeze')
    with rail.db:rail.db.execute('UPDATE tasks SET handoff=NULL WHERE scope=?',(config.scope.key,))
    rail.db.execute("CREATE TRIGGER reject_manifest BEFORE INSERT ON artifacts WHEN NEW.name LIKE '%/manifest' BEGIN SELECT RAISE(ABORT,'fixture manifest rejected'); END")
    try:import_events(rail,config,owner,home,'codex','native-session')
    except Unavailable:failures.append('atomic')
    count=rail.db.execute('SELECT count(*) FROM artifacts').fetchone()[0]
    rail.db.execute('DROP TRIGGER reject_manifest')
    first=import_events(rail,config,owner,home,'codex','native-session',limit=1)
    second=import_events(rail,config,owner,home,'codex','native-session',offset=first['next_offset'],limit=1)
    print(json.dumps({'failures':failures,'failed_count':count,'first':first,'second':second}))
''')
        self.assertEqual(result['failures'],['lease','freeze','atomic'])
        self.assertEqual(result['failed_count'],0)
        self.assertEqual(result['first']['next_offset'],1)
        self.assertIsNone(result['second']['next_offset'])

    def test_real_mcp_rereads_imported_tool_result_as_exact_event(self):
        result=self.run_memory('''append([meta,codex('response_item',{'type':'custom_tool_call_output','id':'o1','call_id':'call1','output':'精确工具返回🙂\\r\\nlast'})]);store.ingest(source,'codex')
from contextrail import Store as RailStore,Target
from contextrail.project import load_project
from contextrail.event_bridge import import_events
config=load_project(workspace);config.store.parent.mkdir(parents=True)
with RailStore(config.store) as rail:
    owner=rail.create_task(config.scope,Target('owner','host','external'),'recover',allowed_providers=('host',))
    receipt=import_events(rail,config,owner,home,'codex','native-session')
    ref=next(e['body_ref'] for e in receipt['entries'] if e['event_type']=='tool.result')
import asyncio
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
async def read():
    server=StdioServerParameters(command=sys.executable,args=['-m','contextrail','mcp','--workspace',str(workspace),'--session','owner'])
    async with stdio_client(server) as (r,w):
        async with ClientSession(r,w) as session:
            await session.initialize()
            response=await session.call_tool('context.get',ref)
            parsed=json.loads(response.content[0].text)
            print(json.dumps({'data':json.loads(parsed['content'])['data'],'trust':parsed['trust']}))
asyncio.run(read())
''',python=Path(__file__).resolve().parents[1]/'.venv/Scripts/python.exe')
        self.assertEqual(result['data']['output'],'精确工具返回🙂\r\nlast')
        self.assertEqual(result['trust'],'untrusted_evidence')

    def test_upgrade_continues_existing_cursor_without_losing_session_binding(self):
        result=self.run_memory('''append([meta])
cursor={'session':'native-session','cwd':str(workspace),'turn_id':'old-possibly-complete-turn','user':'old user'}
with store.db:
    store.db.execute('INSERT INTO cursors VALUES(?,?,?)',(str(source),source.stat().st_size,json.dumps(cursor)))
    store.db.execute('INSERT INTO jobs(id,source,payload,state,created,available) VALUES(?,?,?,?,?,?)',('legacy-job',str(source),'{}','saved',0,0))
store.db.execute('DROP TABLE normalized_events');store.db.close();store=Store(home)
append([codex('response_item',{'type':'message','id':'new-user','role':'user','content':'new request after upgrade'})])
store.ingest(source,'codex')
from native_event_stream import list_events,get_event
page=list_events(store.db,store.event_workspace(str(workspace)),'codex','native-session',role='user')
events=[get_event(store.db,store.event_workspace(str(workspace)),'codex','native-session',e['id']) for e in page['entries']]
print(json.dumps({'events':events,'old_state':store.db.execute('SELECT state FROM jobs WHERE id=?',('legacy-job',)).fetchone()[0]}))
''')
        self.assertEqual(len(result['events']),1,'existing cursor lost the native session during schema upgrade')
        self.assertEqual(result['events'][0]['data']['text'],'new request after upgrade')
        self.assertIsNone(result['events'][0]['turn_id'])
        self.assertEqual(result['old_state'],'saved')

    def test_rail_import_selects_only_workspace_client_and_native_session(self):
        result=self.run_memory('''user=lambda text:codex('response_item',{'type':'message','id':'u1','role':'user','content':text})
append([meta,user('SELECTED_REQUEST')]);store.ingest(source,'codex')
other=home/'foreign-worktree';other.mkdir()
append([codex('session_meta',{'id':'native-session','cwd':str(other)}),user('FOREIGN_WORKSPACE')]);store.ingest(source,'codex')
append([codex('session_meta',{'id':'other-session','cwd':str(workspace)}),user('FOREIGN_SESSION')]);store.ingest(source,'codex')
source=home/'claude.jsonl'
append([{'type':'user','sessionId':'native-session','cwd':str(workspace),'uuid':'u1','message':{'content':'FOREIGN_CLIENT'}}]);store.ingest(source,'claude')
from contextrail import Store as RailStore,Target,ContextTools
from contextrail.project import load_project
from contextrail.event_bridge import import_events
config=load_project(workspace);config.store.parent.mkdir(parents=True)
with RailStore(config.store) as rail:
    owner=rail.create_task(config.scope,Target('owner','host','external'),'recover',allowed_providers=('host',))
    receipt=import_events(rail,config,owner,home,'codex','native-session')
    tools=ContextTools(rail,config.scope,'owner')
    data=[json.loads(tools.call('context.get',entry['body_ref'])['content']) for entry in receipt['entries']]
    print(json.dumps(data))
''')
        serialized=json.dumps(result)
        self.assertIn('SELECTED_REQUEST',serialized)
        for foreign in ('FOREIGN_WORKSPACE','FOREIGN_SESSION','FOREIGN_CLIENT'):
            self.assertNotIn(foreign,serialized)

    def test_structured_tool_output_keeps_text_but_marks_binary_blocks_omitted(self):
        result=self.run_memory('''append([meta,codex('response_item',{'type':'function_call_output','id':'o1','call_id':'call1','output':[
{'type':'input_text','text':'structured public tool text'},
{'type':'image','data':'BINARY_IMAGE_CANARY'},
{'type':'thinking','thinking':'PRIVATE_TOOL_BLOCK_CANARY'}]})]);store.ingest(source,'codex')
from native_event_stream import list_events,get_event
page=list_events(store.db,store.event_workspace(str(workspace)),'codex','native-session',role='tool')
event=get_event(store.db,store.event_workspace(str(workspace)),'codex','native-session',page['entries'][0]['id'])
print(json.dumps(event))
''')
        self.assertNotIn('BINARY_IMAGE_CANARY',json.dumps(result))
        self.assertNotIn('PRIVATE_TOOL_BLOCK_CANARY',json.dumps(result))
        self.assertEqual(result['data']['output'][0],{'type':'input_text','text':'structured public tool text'})
        self.assertEqual(result['body_state'],'partial_blocks_omitted')

    def test_structured_tool_json_schema_is_not_misread_as_a_content_block(self):
        result=self.run_memory('''append([meta,codex('response_item',{'type':'function_call_output','id':'o1','call_id':'call1','output':{'schema':{'type':['string','null'],'description':'ordinary schema data'}}})]);store.ingest(source,'codex')
from native_event_stream import list_events,get_event
page=list_events(store.db,store.event_workspace(str(workspace)),'codex','native-session',role='tool')
events=[get_event(store.db,store.event_workspace(str(workspace)),'codex','native-session',e['id']) for e in page['entries']]
print(json.dumps(events))
''')
        self.assertEqual(len(result),1,'structured JSON output was dropped')
        self.assertEqual(result[0]['data']['output']['schema']['type'],['string','null'])


if __name__=='__main__':unittest.main()
