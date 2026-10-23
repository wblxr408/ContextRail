"""E08 current consumer contracts; no earlier test suites are exercised."""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys

from contextrail import Scope,Store,Target
from contextrail.host import ContextTools
from contextrail.tool_results import OutputRecorder


class EventTimelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='rail-timeline-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'rail.sqlite');self.addCleanup(self.store.close)
        self.scope=Scope('local','project','worktree','task');self.lease=self.store.create_task(self.scope,Target('owner','host','external'),'Browse attempted fixes',allowed_providers=('host',))
        self.tools=ContextTools(self.store,self.scope,'owner')

    def output(self,call,when,error='',code=0):
        return OutputRecorder(self.store,self.scope,self.lease,self.root).capture({'call_id':call,'tool':'shell','argv':['python','build.py'],'cwd':str(self.root),'started_at':when,'finished_at':when+1,'exit_code':code,'stdout':'attempt '+call,'stderr':error,'source_paths':['src/修复.py']},budget=4096)

    def native(self,event,when,text,*,session='native1',event_type='user.message',role='user',call_id=None,data=None):
        value={'schema':'contextrail.native-event.v1','id':event,'client':'codex','workspace':str(self.root),'native_session':session,'role':role,'event_type':event_type,'turn_id':event,'call_id':call_id,'message_id':event,'occurred_at':when,'source_time_state':'provided' if when else 'missing','observed_at':9999999999,'source':{'path':'source.jsonl'},'data':data or {'text':text}}
        return self.store.put(self.scope,self.lease,'native-events/'+event+'/body',json.dumps(value,ensure_ascii=False).encode(),expected_revision=0,media_type='application/json')

    def test_late_arrivals_and_unknown_times_do_not_replace_occurrence(self):
        self.native('later','2026-10-23T02:00:00Z','纠正：以后使用新的来源路径')
        self.native('unknown',None,'missing time')
        self.native('earlier','2026-10-23T09:00:00+08:00','try original path')
        page=self.tools.call('context.timeline',{'limit':20})
        self.assertEqual([e['source_event'] for e in page['entries']],['earlier','later','unknown'])
        self.assertIsNone(page['entries'][-1]['occurred_at'])
        self.assertEqual(page['entries'][-1]['source_time_state'],'missing')
        ref=page['entries'][0]['ref']
        self.assertIn('try original path',self.tools.call('context.get',ref)['content'])

    def test_error_file_filter_and_neighborhood_recover_attempt_then_correction(self):
        first=self.output('attempt1',100)
        failed=self.output('failed2',200,'Permission denied: protected file',1)
        correction=self.native('correction','1970-01-01T00:05:00Z','纠正：以后请在可写目录中构建。')
        errors=self.tools.call('context.timeline',{'file':'src/修复.py','error_category':'permission_denied'})
        self.assertEqual(len(errors['entries']),1)
        error=errors['entries'][0]
        self.assertEqual(error['status'],'failed')
        self.assertEqual(error['classification']['basis'],'source_declared_failure_and_text_heuristic')
        nearby=self.tools.call('context.timeline',{'around':error['id'],'limit':3})
        self.assertEqual([e['source_event'] for e in nearby['entries']],['attempt1','failed2','correction'])
        self.assertEqual(nearby['entries'][0]['ref'],first['result_ref'])
        self.assertEqual(nearby['entries'][1]['ref'],failed['result_ref'])
        self.assertEqual(nearby['entries'][2]['ref'],asdict(correction))

    def test_original_memory_timeline_reads_declared_errors_and_links_call_file(self):
        app=Path.home()/'.codex/services/engineering-memory/app';overlay=Path(__file__).resolve().parents[1]/'integrations/engineering_memory'
        code='''import json,sys,subprocess,os
from pathlib import Path
sys.path[:0]=sys.argv[1:3]
from listener import Store
home=Path(sys.argv[3]);workspace=home/'work';workspace.mkdir();source=home/'source.jsonl';store=Store(home)
def row(time,payload):return {'type':'response_item','timestamp':time,'payload':payload}
records=[{'type':'session_meta','timestamp':'2026-10-23T00:00:00Z','payload':{'id':'s1','cwd':str(workspace)}},
row('2026-10-23T00:02:00Z',{'type':'function_call_output','call_id':'c1','is_error':True,'output':'Permission denied: restricted file'}),
row('2026-10-23T00:01:00Z',{'type':'function_call','name':'Read','call_id':'c1','arguments':{'file_path':str(workspace/'src/a.py')}}),
row('2026-10-23T00:03:00Z',{'type':'message','id':'u1','role':'user','content':'Correct the location before trying again'})]
source.write_text(''.join(json.dumps(r)+'\\n' for r in records),encoding='utf-8');store.ingest(source,'codex')
cmd=[sys.executable,str(Path(sys.argv[1])/'listener.py'),'timeline','--home',str(home),'--workspace',str(workspace),'--client','codex','--native-session','s1','--file','src/a.py','--error-category','permission_denied']
env={**os.environ,'PYTHONPATH':os.pathsep.join([*sys.argv[1:3],str(Path(sys.argv[1]).parents[1])])}
run=subprocess.run(cmd,capture_output=True,env=env)
if run.returncode:print(json.dumps({'code':run.returncode,'out':run.stdout.decode(),'err':run.stderr.decode()}));sys.exit()
with store.db:
    store.db.execute('DROP TABLE timeline_paths');store.db.execute('DROP TABLE timeline_entries');store.db.execute('DROP TABLE timeline_state')
store.db.close();store=Store(home)
from native_event_stream import native_timeline,reindex_timeline
pending=native_timeline(store.db,str(workspace),'codex','s1')['coverage']
built=reindex_timeline(store.db,str(workspace),'codex','s1',limit=100)
print(json.dumps({'code':run.returncode,'out':run.stdout.decode(),'err':run.stderr.decode(),'pending':pending,'rebuilt':built,'jobs':store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]}))
'''
        child=subprocess.run([sys.executable,'-c',code,str(overlay),str(app),str(self.root)],capture_output=True,timeout=30)
        self.assertEqual(child.returncode,0,child.stderr.decode('utf-8','replace'))
        result=json.loads(child.stdout)
        self.assertEqual(result['code'],0,result['err'])
        page=json.loads(result['out']);self.assertEqual(len(page['entries']),1)
        event=page['entries'][0]
        self.assertEqual(event['error_category'],'permission_denied')
        self.assertEqual(event['ref']['native_session'],'s1')
        self.assertEqual(event['ref']['reader'],'stream-get')
        self.assertEqual(result['pending']['states']['pending'],4)
        self.assertEqual(result['rebuilt']['processed'],4)
        self.assertTrue(result['rebuilt']['coverage']['complete'])
        self.assertEqual(result['jobs'],0)

    def test_old_sources_expose_pending_then_explicit_rebuild_preserves_version(self):
        from contextrail.timeline import reindex
        self.output('old',100,'Tests failed: assertion missing',1)
        with self.store.transaction():
            self.store.db.execute('DROP TABLE timeline_paths');self.store.db.execute('DROP TABLE timeline_entries');self.store.db.execute('DROP TABLE timeline_state');self.store.db.execute('PRAGMA user_version=8')
        version=self.store.task(self.scope)['version'];self.store.close()
        self.store=Store(self.root/'rail.sqlite');self.addCleanup(self.store.close);self.tools=ContextTools(self.store,self.scope,'owner')
        page=self.tools.call('context.timeline',{})
        self.assertEqual(page['coverage']['states']['pending'],1)
        self.assertFalse(page['coverage']['complete'])
        rebuilt=reindex(self.store,self.scope,self.lease,limit=1)
        self.assertEqual(rebuilt['processed'],1)
        self.assertEqual(rebuilt['task_version'],version)
        self.assertEqual(self.tools.call('context.timeline',{})['entries'][0]['error_category'],'test_failed')

    def test_scoped_page_time_filter_and_budget_do_not_fake_complete_view(self):
        from contextrail.errors import InvalidRequest
        self.native('x','2026-10-23T01:00:00Z','one',session='s1')
        self.native('y','2026-10-23T02:00:00Z','two',session='s2')
        self.native('z',None,'unknown',session='s1')
        first=self.tools.call('context.timeline',{'limit':1,'budget':2500})
        self.assertLessEqual(len(json.dumps(first,ensure_ascii=False,separators=(',',':')).encode()),2500)
        self.assertEqual(first['next_offset'],1)
        page=self.tools.call('context.timeline',{'native_session':'s1','since':'2026-10-23T00:00:00Z','until':'2026-10-23T02:00:00Z'})
        self.assertEqual([e['source_event'] for e in page['entries']],['x'])
        with self.assertRaises(InvalidRequest):self.tools.call('context.timeline',{'since':'2026-10-23T00:00:00'})

    def test_successful_error_quotation_is_not_classified_as_failure(self):
        self.output('quoted',10,'Example: Permission denied means access was refused',0)
        self.assertEqual(self.tools.call('context.timeline',{'error_category':'permission_denied'})['entries'],[])
        entry=self.tools.call('context.timeline',{})['entries'][0]
        self.assertEqual(entry['status'],'succeeded')
        self.assertIsNone(entry['error_category'])

    def test_file_association_does_not_cross_native_sessions_with_reused_call_id(self):
        self.native('call1','2026-10-23T01:00:00Z','',session='s1',event_type='tool.call',role='tool',call_id='reused',data={'arguments':{'file_path':'src/private.py'}})
        self.native('result2','2026-10-23T02:00:00Z','',session='s2',event_type='tool.result',role='tool',call_id='reused',data={'output':'Permission denied','error_state':'source_error'})
        page=self.tools.call('context.timeline',{'file':'src/private.py','native_session':'s2'})
        self.assertEqual(page['entries'],[],'file relation leaked from another native session')

    def test_oversized_pending_source_is_excluded_without_reading_its_body(self):
        from contextrail.timeline import reindex
        name='native-events/oversized/body'
        self.store.put(self.scope,self.lease,name,b'x'*(5*1024*1024),expected_revision=0)
        with self.store.transaction():self.store.db.execute("UPDATE timeline_state SET status='pending' WHERE name=?",(name,))
        original=self.store.get
        def bounded_read(scope,ref):
            if ref.name==name:self.fail('oversized source body was loaded during metadata-only exclusion')
            return original(scope,ref)
        self.store.get=bounded_read
        result=reindex(self.store,self.scope,self.lease,limit=1)
        self.assertEqual(result['processed'],1)
        self.assertEqual(result['source_bytes'],0)
        self.assertEqual(result['coverage']['states']['excluded'],1)

    def test_unavailable_source_stays_visible_without_losing_other_entries(self):
        a=self.native('removed','2026-10-23T01:00:00Z','old')
        self.native('available','2026-10-23T02:00:00Z','current')
        with self.store.transaction():self.store.db.execute('UPDATE artifacts SET invalidated=1 WHERE scope=? AND name=?',(self.scope.key,a.name))
        page=self.tools.call('context.timeline',{})
        self.assertEqual(len(page['entries']),2)
        self.assertEqual(page['entries'][0]['availability']['status'],'unavailable')
        self.assertEqual(page['entries'][0]['preview'],'')
        self.assertEqual(page['entries'][1]['availability']['status'],'available')
        self.assertFalse(page['coverage']['complete'])

    def test_other_task_history_is_not_part_of_current_timeline(self):
        other=Scope('local','project','worktree','other-task')
        lease=self.store.create_task(other,Target('other-owner','host','external'),'Other work',allowed_providers=('host',))
        OutputRecorder(self.store,other,lease,self.root).capture({'call_id':'private','tool':'shell','argv':['python'],'cwd':str(self.root),'started_at':10,'finished_at':11,'exit_code':1,'stdout':'','stderr':'Permission denied for other task'},budget=4096)
        self.native('ours','2026-10-23T01:00:00Z','current task')
        page=self.tools.call('context.timeline',{})
        self.assertEqual([e['source_event'] for e in page['entries']],['ours'])

    def test_cli_timeline_filters_current_task(self):
        from contextrail.project import load_project
        workspace=self.root/'cli-project';workspace.mkdir()
        config=load_project(workspace,overrides={'task':'cli-demo'});config.store.parent.mkdir(parents=True)
        with Store(config.store) as store:
            lease=store.create_task(config.scope,Target('cli-owner','host','external'),'CLI timeline',allowed_providers=('host',))
            OutputRecorder(store,config.scope,lease,workspace).capture({'call_id':'cli-fail','tool':'shell','argv':['python'],'cwd':str(workspace),'started_at':10,'finished_at':11,'exit_code':1,'stdout':'','stderr':'EACCES: restricted path'},budget=4096)
        child=subprocess.run([sys.executable,'-m','contextrail','timeline','--workspace',str(workspace),'--task','cli-demo','--session','cli-owner','--error-category','permission_denied'],capture_output=True,timeout=30)
        self.assertEqual(child.returncode,0,child.stderr.decode('utf-8','replace'))
        page=json.loads(child.stdout)
        self.assertEqual(page['entries'][0]['source_event'],'cli-fail')

    def test_real_mcp_timeline_and_original_read(self):
        import asyncio
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        self.output('mcp-fail',100,'Timeout: operation deadline exceeded',1)
        script='''import asyncio,sys
from contextrail import Store,Scope
from contextrail.host import ContextTools
from contextrail.mcp_server import run_stdio
with Store(sys.argv[1]) as store:asyncio.run(run_stdio(ContextTools(store,Scope('local','project','worktree','task'),'owner')))
'''
        async def run():
            params=StdioServerParameters(command=sys.executable,args=['-c',script,str(self.root/'rail.sqlite')])
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as client:
                    await client.initialize()
                    page=await client.call_tool('context.timeline',{'error_category':'timeout'})
                    self.assertFalse(page.isError)
                    payload=json.loads(page.content[0].text)
                    self.assertEqual(payload['entries'][0]['source_event'],'mcp-fail')
                    original=await client.call_tool('context.get',payload['entries'][0]['ref'])
                    self.assertFalse(original.isError)
                    self.assertEqual(json.loads(json.loads(original.content[0].text)['content'])['exit_code'],1)
        asyncio.run(run())

    def test_codex_exec_envelope_preserves_real_subcommand_exit_outcomes(self):
        blocks=[{'type':'input_text','text':'Script completed\nOutput:\n'},
            {'type':'input_text','text':json.dumps({'chunk_id':'ok1','wall_time_seconds':1.0,'exit_code':0,'output':'Example: permission denied'})},
            {'type':'input_text','text':json.dumps({'chunk_id':'bad2','wall_time_seconds':1.2,'exit_code':1,'output':'Tests failed: assertion expected true'})}]
        self.native('aggregate','2026-10-23T01:00:00Z','',event_type='tool.result',role='tool',data={'output':blocks,'error_state':'unknown'})
        page=self.tools.call('context.timeline',{'error_category':'test_failed'})
        self.assertEqual(len(page['entries']),1,'actual exec exit codes were ignored because outer native is_error was absent')
        entry=page['entries'][0]
        self.assertEqual(entry['status'],'failed')
        self.assertEqual(entry['native_error_state'],'unknown')
        self.assertEqual(entry['exit_codes'],[0,1])
        self.assertEqual(entry['outcome_basis'],'codex_exec_result_envelope')


if __name__=='__main__':unittest.main()
