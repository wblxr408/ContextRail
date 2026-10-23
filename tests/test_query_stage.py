"""One C06/C08/H01 phase contract; run as a group after implementation."""
import json
from pathlib import Path
import shutil
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import unittest

from contextrail import Scope,Store,Target
from contextrail.errors import RailError
from contextrail.host import ContextTools


class QueryStageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='rail-query-stage-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.home=self.root/'memory';(self.home/'app').mkdir(parents=True)
        installed=Path.home()/'.codex/services/engineering-memory/app'
        shutil.copy2(installed/'resource_client.py',self.home/'app/resource_client.py')
        self.scope=Scope('local','p','w','stage')
        self.store=Store(self.root/'rail.sqlite');self.addCleanup(self.store.close)
        self.lease=self.store.create_task(self.scope,Target('reader','host','external'),'ORBIT current task',allowed_providers=('host',))
        self.store.put(self.scope,self.lease,'contract','ORBIT current evidence: preserve exact references.'.encode(),expected_revision=0)
        self.populate_native('native1','2026-10-22T10:00:00Z','ORBIT actual event before correction',initial=True)
        self.calls=[];self.revision='catalog1';self.fail_memory=False
        owner=self
        class Handler(socketserver.BaseRequestHandler):
            def exact(self,n):
                out=b''
                while len(out)<n:
                    part=self.request.recv(n-len(out))
                    if not part:raise ConnectionError()
                    out+=part
                return out
            def handle(self):
                length=struct.unpack('!I',self.exact(4))[0];wire=json.loads(self.exact(length));message=wire['message']
                params=message['params'];name=params['name']
                if owner.fail_memory:response={'error':{'message':'fixture source unavailable'}}
                else:
                    if name=='query_revision':value={'revision':owner.revision}
                    elif name=='recall':
                        owner.calls.append(params)
                        value={'results':[{'title':'ORBIT old preference','permalink':'main/history/orbit','path':'history/orbit.md','excerpt':'ORBIT historical note, check applicability.','date':'2026-10-23T00:00:00+00:00','status':'accepted','scope':str(owner.root),'score':12.0,'time_provenance':{'occurred_at':None,'state':'date_only','source_date':'2026-10-23','basis':'date'},'source_session':'older-session'}], 'total':1,'mode':'text','catalog_revision':owner.revision}
                    else:value={}
                    response={'result':{'structuredContent':{'result':value},'isError':False}}
                body=json.dumps(response).encode();self.request.sendall(struct.pack('!I',len(body))+body)
        self.server=socketserver.ThreadingTCPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        (self.home/'resource-endpoint.json').write_text(json.dumps({'port':self.server.server_address[1],'token':'local-test-fixture'}),encoding='utf-8')
        self.binding={'schema':1,'workspace':str(self.root),'task':'stage','memory_home':str(self.home),'native_sessions':[{'client':'codex','session':'native1'}],'memory_mode':'text','timeout_seconds':5}
        self.tools=ContextTools(self.store,self.scope,'reader');self.tools.query_binding=self.binding

    def populate_native(self,session,when,text,initial=False):
        root=Path(__file__).resolve().parents[1];overlay=root/'integrations/engineering_memory';app=Path.home()/'.codex/services/engineering-memory/app'
        script='''import sys,json
from pathlib import Path
sys.path[:0]=sys.argv[1:3]
from listener import Store
home=Path(sys.argv[3]);workspace=sys.argv[4];session=sys.argv[5];when=sys.argv[6];text=sys.argv[7];source=home/(session+'.jsonl')
store=Store(home)
rows=[]
if sys.argv[8]=='1':rows.append({'type':'session_meta','timestamp':when,'payload':{'id':session,'cwd':workspace}})
rows.append({'type':'response_item','timestamp':when,'payload':{'type':'message','role':'user','id':text,'content':text}})
with source.open('a',encoding='utf-8') as f:
 for row in rows:f.write(json.dumps(row)+'\\n')
store.ingest(source,'codex')
'''
        child=subprocess.run([sys.executable,'-c',script,str(overlay),str(app),str(self.home),str(self.root),session,when,text,'1' if initial else '0'],capture_output=True,timeout=20)
        self.assertEqual(child.returncode,0,child.stderr.decode('utf-8','replace'))

    def ask(self,**arguments):
        try:return self.tools.call('context.query',arguments)
        except RailError as exc:self.fail('Unified query did not satisfy its public contract: '+str(exc))

    def test_all_sources_batch_and_historical_role(self):
        result=self.ask(queries=['ORBIT','ORBIT'],view='relevance',budget=20000)
        first=result['queries'][0]
        self.assertEqual({h['origin'] for h in first['hits']},{'task','events','memory'})
        self.assertEqual(len(self.calls),1,'duplicate batch query called memory twice')
        self.assertEqual(result['queries'][1]['coalesced_with'],0)
        for hit in first['hits']:
            self.assertTrue(hit['ref']);self.assertIn('version',hit);self.assertIn('time',hit)
            if hit['origin']!='task':self.assertFalse(hit['current_task_membership'])
        self.assertEqual(self.calls[0]['kwargs']['scope'],str(self.root))

    def test_timeline_does_not_turn_note_day_into_precise_occurrence(self):
        result=self.ask(queries=['ORBIT'],view='timeline',budget=20000)
        hits=result['queries'][0]['hits']
        self.assertEqual(hits[0]['origin'],'events')
        historical=next(h for h in hits if h['origin']=='memory')
        self.assertIsNone(historical['time']['occurred_at'])
        self.assertEqual(historical['time']['state'],'date_only')
        self.assertEqual(historical['version']['state'],'unavailable')

    def test_failed_source_keeps_other_results_and_is_not_fresh_cache(self):
        self.ask(queries=['ORBIT'],budget=20000)
        self.fail_memory=True
        result=self.ask(queries=['ORBIT'],budget=20000)
        sources=result['queries'][0]['sources']
        self.assertEqual(sources['memory']['status'],'unavailable')
        self.assertTrue(result['queries'][0]['hits'])
        self.assertTrue(result['queries'][0]['partial'])

    def test_repetition_refresh_and_source_version_changes(self):
        first=self.ask(queries=['ORBIT'],budget=20000)
        second=self.ask(queries=['ORBIT'],budget=20000)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(second['queries'][0]['repeat_of'],first['request_id'])
        self.assertTrue(all(h['preview']=='' for h in second['queries'][0]['hits']))
        self.revision='catalog2'
        self.populate_native('native1','2026-10-23T10:00:00Z','ORBIT newer correction')
        changed=self.ask(queries=['ORBIT'],budget=20000)
        self.assertEqual(len(self.calls),2)
        self.assertGreater(changed['queries'][0]['sources']['events']['total'],first['queries'][0]['sources']['events']['total'])
        fresh=self.ask(queries=['ORBIT'],refresh=True,budget=20000)
        self.assertEqual(len(self.calls),3)
        self.assertTrue(any(h['preview'] for h in fresh['queries'][0]['hits']))

    def test_status_distinguishes_raw_capture_learning_and_outbound_control(self):
        try:status=self.tools.call('context.status',{'budget':16000})
        except RailError as exc:self.fail('Unified status unavailable: '+str(exc))
        self.assertEqual(status['task']['owner'],'reader')
        self.assertGreater(status['capture']['raw_events'],0)
        self.assertEqual(status['learning']['saved_jobs'],0)
        self.assertEqual(status['capabilities']['history_control'],'not_controlled')
        self.assertEqual(self.calls,[],'status invoked a knowledge model/recall operation')

    def test_response_budget_scope_override_and_exact_task_read(self):
        result=self.ask(queries=['ORBIT'],limits={'task':1,'events':1,'memory':1},budget=7000)
        self.assertLessEqual(len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode()),7000)
        task=next(h for h in result['queries'][0]['hits'] if h['origin']=='task')
        ref={k:v for k,v in task['ref'].items() if k!='reader'}
        self.assertIn('ORBIT',self.tools.call('context.get',ref)['content'])
        with self.assertRaises(RailError):self.tools.call('context.query',{'queries':['ORBIT'],'memory_home':str(self.root/'other')})

    def test_real_stdio_query_and_status_use_the_bound_sources(self):
        import asyncio
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        binding=self.root/'binding.json';binding.write_text(json.dumps(self.binding),encoding='utf-8')
        script='''import asyncio,json,sys
from contextrail import Store,Scope
from contextrail.host import ContextTools
from contextrail.mcp_server import run_stdio
with Store(sys.argv[1]) as store:
 tools=ContextTools(store,Scope('local','p','w','stage'),'reader',query_binding=json.load(open(sys.argv[2])))
 asyncio.run(run_stdio(tools))
'''
        async def consume():
            params=StdioServerParameters(command=sys.executable,args=['-c',script,str(self.root/'rail.sqlite'),str(binding)])
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as client:
                    await client.initialize()
                    answer=await client.call_tool('context.query',{'queries':['ORBIT'],'budget':20000})
                    self.assertFalse(answer.isError)
                    payload=json.loads(answer.content[0].text)
                    self.assertEqual({h['origin'] for h in payload['queries'][0]['hits']},{'task','events','memory'})
                    state=await client.call_tool('context.status',{'budget':16000})
                    self.assertFalse(state.isError)
                    self.assertGreater(json.loads(state.content[0].text)['capture']['raw_events'],0)
        asyncio.run(consume())

    def test_existing_runtime_emits_time_precision_and_opt_in_catalog_version(self):
        root=Path(__file__).resolve().parents[1];overlay=root/'integrations/engineering_memory';app=Path.home()/'.codex/services/engineering-memory/app'
        script='''import json,sys
from pathlib import Path
sys.path[:0]=sys.argv[1:3]
from memory_runtime import MemoryRuntime
home=Path(sys.argv[3]);vault=home/'vault';vault.mkdir()
(vault/'day.md').write_text('---\\ntitle: ORBIT day\\ndate: 2026-10-23\\ncwd: '+str(home)+'\\n---\\nORBIT confirmed note',encoding='utf-8')
memory=MemoryRuntime(vault,home/'runtime.sqlite',use_models=False)
result=memory.recall('ORBIT',scope=str(home),mode='text',include_revision=True)
print(json.dumps(result));memory.close()
'''
        child=subprocess.run([sys.executable,'-c',script,str(overlay),str(app),str(self.root)],capture_output=True,timeout=25)
        self.assertEqual(child.returncode,0,child.stderr.decode('utf-8','replace'))
        result=json.loads(child.stdout)
        self.assertEqual(result['results'][0].get('time_provenance',{}).get('state'),'date_only')
        self.assertIsNone(result['results'][0]['time_provenance']['occurred_at'])
        self.assertTrue(result.get('catalog_revision'))

    def test_partial_budget_page_is_repeatable_without_losing_omitted_evidence(self):
        for i in range(6):self.store.put(self.scope,self.lease,'doc'+str(i),('ORBIT '+'details '*40+str(i)).encode(),expected_revision=0)
        small=self.ask(queries=['ORBIT'],limits={'task':5,'events':1,'memory':1},budget=3400)
        large=self.ask(queries=['ORBIT'],limits={'task':5,'events':1,'memory':1},budget=20000)
        self.assertGreater(len(large['queries'][0]['hits']),len(small['queries'][0]['hits']))
        new_ids={h['id'] for h in large['queries'][0]['hits']}-{h['id'] for h in small['queries'][0]['hits']}
        self.assertTrue(any(h['preview'] for h in large['queries'][0]['hits'] if h['id'] in new_ids))

    def test_task_and_event_outages_do_not_hide_available_main_memory(self):
        with self.store.transaction():self.store.db.execute('DROP TABLE evidence_fts')
        (self.home/'state.sqlite').rename(self.home/'state.offline.sqlite')
        result=self.ask(queries=['ORBIT'],budget=12000)
        item=result['queries'][0]
        self.assertEqual(item['sources']['task']['status'],'unavailable')
        self.assertEqual(item['sources']['events']['status'],'unavailable')
        self.assertEqual(item['sources']['memory']['status'],'ok')
        self.assertEqual({h['origin'] for h in item['hits']},{'memory'})
