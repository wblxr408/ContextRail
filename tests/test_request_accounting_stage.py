"""One D07/H03/D08 phase contract; baseline then grouped acceptance."""
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from contextrail import Compiler,Store,Target,Selection
from contextrail.errors import RailError
from contextrail.host import ContextTools
from contextrail.project import load_project


class RequestAccountingStage(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='rail-accounting-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'.contextrail.toml').write_text('[project]\ntask="metrics-stage"\n',encoding='utf-8')
        self.config=load_project(self.root);self.scope=self.config.scope;self.config.store.parent.mkdir()
        self.store=Store(self.config.store);self.addCleanup(self.store.close)
        self.lease=self.store.create_task(self.scope,Target('host','local','declared-model'),'Keep actual request accounting',allowed_providers=('local',))
        self.ref=self.store.put(self.scope,self.lease,'evidence',('证据🙂'*1000).encode(),expected_revision=0)
        self.sid=self.store.snapshot(self.scope,self.lease,(Selection(self.ref),))
        self.packet=Compiler(self.store).compile(self.scope,self.sid,'host',budget=18000)
        self.tools=ContextTools(self.store,self.scope,'host')

    def metrics(self,**kwargs):return self.tools.call('context.metrics',{'budget':20000,**kwargs})
    def accounting(self):
        self.metrics()  # public contract missing at baseline, not an import failure
        from contextrail.accounting import HostAccounting
        return HostAccounting(self.store,self.scope,'host')

    def test_final_request_differs_from_packet_and_missing_usage_is_unknown(self):
        meter=self.accounting();a=meter.record_request(self.packet,observation_id='host-1',final_request=b'{}',components={'system':[0,1]},reserves={'unit':'tokens','output':100,'safety':50})
        b=meter.record_request(self.packet,observation_id='host-2',final_request=('中文'+(' ' *300)).encode(),components={})
        result=self.metrics();rows={r['observation']['id']:r for r in result['requests']['entries']}
        self.assertEqual(rows['host-1']['observation']['final_request_bytes'],2)
        self.assertGreater(rows['host-2']['observation']['final_request_bytes'],300)
        self.assertIsNone(rows['host-1']['input_units']);self.assertIsNone(rows['host-1']['observation']['input_tokens'])
        self.assertIsNone(result['comparison']['provider_input_savings'])
        self.assertEqual(a['id'],meter.record_request(self.packet,observation_id='host-1',final_request=b'{}',components={'system':[0,1]},reserves={'unit':'tokens','output':100,'safety':50})['id'])
        self.assertEqual(self.metrics()['requests']['total'],2)
        with self.assertRaises(RailError):meter.record_request(self.packet,observation_id='host-1',final_request=b'different')

    def test_real_counter_and_usage_subsets_without_reasoning_double_count(self):
        meter=self.accounting();usage={'input_tokens':50,'input_tokens_details':{'cached_tokens':20},'output_tokens':13,'output_tokens_details':{'reasoning_tokens':8},'total_tokens':63}
        meter.record_request(self.packet,observation_id='reported',final_request=b'final-payload',usage=usage,usage_format='openai_responses',request_id='provider-1')
        calls=[]
        def counter(raw):calls.append(raw);return len(raw)
        meter.record_request(self.packet,observation_id='counted',final_request=b'abc',token_counter=counter,counter_id='trusted-counter-fixture')
        rows={r['observation']['id']:r for r in self.metrics()['requests']['entries']}
        self.assertEqual((rows['reported']['input_units'],rows['reported']['cached_input_units'],rows['reported']['output_units']),(50,20,13))
        self.assertEqual(rows['reported']['observation']['reasoning_output_tokens'],8)
        self.assertEqual(rows['counted']['observation']['input_tokens'],3);self.assertEqual(calls,[b'abc'])

    def test_components_reject_overlap_and_counts_reject_invalid_before_write(self):
        meter=self.accounting()
        for parts in ({'system':[0,4],'history':[3,6]},{'tools':[0,99]}):
            with self.assertRaises(RailError):meter.record_request(self.packet,observation_id='invalid',final_request=b'abcdef',components=parts)
        with self.assertRaises(RailError):meter.record_request(self.packet,observation_id='invalid',usage={'input_tokens':True},usage_format='openai_responses')
        self.assertEqual(self.metrics()['requests']['total'],0)

    def test_readback_persists_across_reopen_and_request_link_is_explicit(self):
        meter=self.accounting();meter.record_request(self.packet,observation_id='req-read',final_request=None)
        result=meter.call_tool(self.tools,'context.get',{**asdict(self.ref),'start':0,'end':100},observation_id='read-1',request_observation_id='req-read')
        self.assertTrue(result['content']);meter.call_tool(self.tools,'context.get',{**asdict(self.ref),'start':0,'end':100},observation_id='read-1',request_observation_id='req-read')
        self.store.close();self.store=Store(self.config.store);self.addCleanup(self.store.close);self.tools=ContextTools(self.store,self.scope,'host')
        result=self.metrics();self.assertEqual(result['tools']['total'],1)
        row=result['tools']['entries'][0];self.assertEqual(row['readback_bytes'],100);self.assertEqual(row['request_observation_id'],'req-read')
        self.assertGreater(row['returned_bytes'],100)

    def test_failure_is_counted_without_inventing_successful_read(self):
        meter=self.accounting()
        with self.assertRaises(RailError):meter.call_tool(self.tools,'context.get',{'name':'missing','revision':1},observation_id='failed')
        result=self.metrics();row=result['tools']['entries'][0]
        self.assertEqual(row['status'],'failed');self.assertIsNone(row['readback_bytes']);self.assertIsNone(row['returned_bytes'])
        self.assertEqual(result['tools']['unknown_returned_bytes'],1)

    def test_efficiency_unknown_short_task_and_break_even(self):
        self.metrics()
        request={'action':'compaction','trigger':'budget_pressure','unit':'tokens','before_input':1000,'after_input':500,'future_turns':1,
                 'generation_cost':600,'validation_cost':50,'expected_recovery_cost':100,'cache_loss_cost':50,'cache_write_cost':0}
        short=self.tools.call('context.efficiency',request);self.assertEqual(short['decision'],'keep_current');self.assertEqual(short['net_savings'],-300)
        long=self.tools.call('context.efficiency',{**request,'future_turns':3});self.assertEqual(long['decision'],'consider');self.assertEqual(long['break_even_turns'],2)
        missing=self.tools.call('context.efficiency',{**request,'cache_loss_cost':None});self.assertEqual(missing['decision'],'insufficient_data')
        untriggered=self.tools.call('context.efficiency',{**request,'trigger':'turn_count'});self.assertEqual(untriggered['decision'],'keep_current')
        self.assertEqual(long['model_calls'],0)

    def test_real_stdio_reconnect_keeps_actual_tool_observations(self):
        self.metrics();self.store.close()
        async def invoke():
            from mcp import ClientSession,StdioServerParameters
            from mcp.client.stdio import stdio_client
            args=StdioServerParameters(command=sys.executable,args=['-m','contextrail','mcp','--workspace',str(self.root),'--session','host'])
            async with stdio_client(args) as (read,write):
                async with ClientSession(read,write) as client:
                    await client.initialize();result=await client.call_tool('context.get',{**asdict(self.ref),'start':0,'end':100});self.assertFalse(result.isError)
            async with stdio_client(args) as (read,write):
                async with ClientSession(read,write) as client:
                    await client.initialize();result=await client.call_tool('context.metrics',{'budget':20000});self.assertFalse(result.isError)
                    report=json.loads(result.content[0].text);self.assertGreaterEqual(report['tools']['total'],1);self.assertEqual(report['requests']['total'],0)
        asyncio.run(invoke())

    def test_native_usage_replay_null_and_latest_cumulative_not_sum(self):
        self.metrics()
        home=self.root/'native';request=self.root/'native-rows.json'
        def usage(total):return {'type':'event_msg','timestamp':'2026-10-23T12:00:00Z','payload':{'type':'token_count','info':{
            'last_token_usage':{'input_tokens':10,'cached_input_tokens':3,'output_tokens':3,'reasoning_output_tokens':2,'total_tokens':13},
            'total_token_usage':{'input_tokens':total,'cached_input_tokens':6,'output_tokens':6,'reasoning_output_tokens':4,'total_tokens':total+6}}}}
        rows=[{'type':'session_meta','payload':{'id':'native-meter','cwd':str(self.root),'model':'reported-model'}},usage(10),usage(10),
              {'type':'event_msg','payload':{'type':'token_count','info':None}},usage(20)]
        request.write_text(json.dumps(rows),encoding='utf-8')
        app=Path.home()/'.codex/services/engineering-memory/app';repo=Path(__file__).resolve().parents[1]
        code='''import sys,json
from pathlib import Path
sys.path[:0]=sys.argv[1:3]
import listener
from contextrail.event_stream import capture_record
listener.capture_record=capture_record
home=Path(sys.argv[3]);home.mkdir();rows=json.loads(Path(sys.argv[4]).read_text(encoding='utf-8'));path=home/'rollout.jsonl'
path.write_text(''.join(json.dumps(r)+'\\n' for r in rows),encoding='utf-8')
s=listener.Store(home);s.ingest(path,'codex');s.backfill(path,'codex')
print(s.db.execute('SELECT count(*) FROM jobs').fetchone()[0]);s.db.close()
'''
        process=subprocess.run([sys.executable,'-c',code,str(repo),str(app),str(home),str(request)],capture_output=True,timeout=25)
        self.assertEqual(process.returncode,0,process.stderr.decode('utf-8','replace'));self.assertEqual(int(process.stdout),0)
        self.tools.query_binding={'schema':1,'task':self.scope.task,'workspace':str(self.root),'memory_home':str(home),'native_sessions':[{'client':'codex','session':'native-meter'}]}
        value=self.metrics()['native_usage'];self.assertEqual(value['total_reports'],4)  # distinct physical reports; replay adds none
        source=value['sessions'][0];self.assertEqual(source['latest_cumulative']['input_tokens'],20)
        self.assertEqual(source['latest_cumulative']['output_tokens'],6);self.assertEqual(source['unknown_reports_in_window'],1)
        self.assertIn('event_id',source['latest_report_ref'])

    def test_budget_unit_mismatch_rejected(self):
        self.metrics()
        from contextrail.controller import RequestBudget,ContextController
        compiler=Compiler(self.store,measure=lambda text:len(text),unit='actual-counter-unit')
        with self.assertRaises(RailError):ContextController(compiler,self.scope,self.sid,'host',budget=RequestBudget(30000,unit='utf8_bytes'))
        context=ContextController(compiler,self.scope,self.sid,'host',budget=RequestBudget(30000,unit='actual-counter-unit')).compile('explore')
        self.assertEqual(context.packet.unit,'actual-counter-unit')

    def test_capture_projection_and_repeated_readback_are_separate_costs(self):
        meter=self.accounting()
        from contextrail.tool_results import OutputRecorder
        body='中文🙂'*3000
        data={'call_id':'large-output','tool':'shell','argv':['fixture-command'],'cwd':str(self.root),
              'started_at':1000,'finished_at':1001,'exit_code':0,'stdout':body,'stderr':''}
        recorder=OutputRecorder(self.store,self.scope,self.lease,self.root)
        projected=recorder.capture(data,budget=2048);recorder.capture(data,budget=2048)
        for i in range(2):meter.call_tool(self.tools,'context.get',{**projected['stdout']['ref'],'start':0,'end':5000},observation_id='large-read-'+str(i))
        result=self.metrics()
        self.assertEqual(result['storage']['unique_captured_raw_bytes'],len(body.encode()))
        self.assertEqual(result['tools']['known_readback_bytes'],10000)
        self.assertGreater(result['tools']['known_returned_bytes'],10000)
        self.assertIsNone(result['comparison']['provider_input_savings'])

    def test_schema9_migration_preserves_existing_request_and_snapshot(self):
        self.accounting()
        from contextrail.policy import UsageLedger
        UsageLedger(self.store).record_request(self.scope,self.packet,unit='tokens',input_units=20,output_units=4,request_id='legacy-provider')
        self.store.db.execute('DROP INDEX request_usage_observation')
        self.store.db.execute('ALTER TABLE request_usage DROP COLUMN observation_json')
        self.store.db.execute('PRAGMA user_version=9');self.store.close()
        self.store=Store(self.config.store);self.addCleanup(self.store.close);self.tools=ContextTools(self.store,self.scope,'host')
        self.assertEqual(self.store.get(self.scope,self.ref).content,('证据🙂'*1000).encode())
        self.assertEqual(self.store.load_snapshot(self.scope,self.sid)['version'],self.store.task(self.scope)['version'])
        result=self.metrics();row=result['requests']['entries'][0]
        self.assertEqual((row['input_units'],row['output_units']),(20,4));self.assertIsNone(row['observation'])
        self.assertEqual(result['requests']['unknown_final_payloads_in_page'],1)

    def test_cli_record_request_metrics_and_efficiency(self):
        self.metrics();packet=self.root/'packet.json';packet.write_text(json.dumps(asdict(self.packet),ensure_ascii=False),encoding='utf-8')
        final=self.root/'final.bin';final.write_bytes(b'actual-serialized-payload')
        receipt=self.root/'receipt.json';receipt.write_text(json.dumps({'observation_id':'cli-observation','components':{'system':[0,6]}}),encoding='utf-8')
        self.store.close()
        args=[sys.executable,'-m','contextrail','record-request','--workspace',str(self.root),'--session','host','--packet',str(packet),'--input',str(receipt),'--final-request',str(final)]
        one=subprocess.run(args,capture_output=True,timeout=30);self.assertEqual(one.returncode,0,one.stderr.decode('utf-8','replace'))
        two=subprocess.run(args,capture_output=True,timeout=30);self.assertEqual(two.returncode,0,two.stderr.decode('utf-8','replace'));self.assertEqual(json.loads(one.stdout)['id'],json.loads(two.stdout)['id'])
        report=subprocess.run([sys.executable,'-m','contextrail','metrics','--workspace',str(self.root),'--session','host'],capture_output=True,timeout=30)
        self.assertEqual(report.returncode,0,report.stderr.decode('utf-8','replace'));row=json.loads(report.stdout)['requests']['entries'][0]
        self.assertEqual(row['observation']['final_request_bytes'],len(final.read_bytes()));self.assertIsNone(row['observation']['input_tokens'])

    def test_concurrent_transport_retry_has_one_request_effect(self):
        self.metrics()
        from concurrent.futures import ThreadPoolExecutor
        import threading
        from contextrail.accounting import HostAccounting
        gate=threading.Barrier(6)
        def send(_):
            with Store(self.config.store) as store:
                meter=HostAccounting(store,self.scope,'host');gate.wait(timeout=20)
                return meter.record_request(self.packet,observation_id='racing-receipt',final_request=b'host-payload')['id']
        with ThreadPoolExecutor(max_workers=6) as pool:values=list(pool.map(send,range(6)))
        self.assertEqual(len(set(values)),1);self.assertEqual(self.metrics()['requests']['total'],1)

    def test_compaction_advice_controls_existing_preparation_without_model_calls(self):
        self.metrics()
        from contextrail.compaction import CompactionLifecycle
        proposal={'action':'compaction','trigger':'explicit_request','unit':'tokens','before_input':1000,'after_input':500,'future_turns':1,
                 'generation_cost':600,'validation_cost':50,'expected_recovery_cost':100,'cache_loss_cost':50,'cache_write_cost':0}
        lifecycle=CompactionLifecycle(self.store)
        kept=lifecycle.begin_if_efficient(self.scope,self.lease,self.sid,proposal)
        self.assertEqual(kept['status'],'kept_current');self.assertIsNone(kept['compaction'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM compactions WHERE scope=?',(self.scope.key,)).fetchone()[0],0)
        prepared=lifecycle.begin_if_efficient(self.scope,self.lease,self.sid,{**proposal,'future_turns':3})
        self.assertEqual(prepared['status'],'prepared')
        self.assertEqual(self.store.compaction(self.scope,prepared['compaction']['id']).phase,'prepared')
        self.assertEqual(prepared['advice']['model_calls'],0)

    def test_cache_advice_controls_layout_without_claiming_provider_hit(self):
        self.metrics()
        from contextrail.policy import CacheLayoutPolicy
        from contextrail.models import CacheLayout
        proposal={'action':'cache','trigger':'source_changed','unit':'tokens','before_input':1000,'after_input':500,'future_turns':1,
                  'generation_cost':600,'validation_cost':50,'expected_recovery_cost':100,'cache_loss_cost':50,'cache_write_cost':0}
        policy=CacheLayoutPolicy(CacheLayout('opt-in-prefix'))
        kept=policy.compile_if_efficient(Compiler(self.store),self.scope,self.sid,'host',budget=18000,proposal=proposal)
        self.assertFalse(kept['cache_layout_applied']);self.assertIsNone(kept['packet'].cache_key)
        chosen=policy.compile_if_efficient(Compiler(self.store),self.scope,self.sid,'host',budget=18000,proposal={**proposal,'future_turns':3})
        self.assertTrue(chosen['cache_layout_applied']);self.assertEqual(chosen['packet'].cache_key,'opt-in-prefix')
        self.assertEqual(chosen['provider_cache_hit'],'unknown')


if __name__=='__main__':unittest.main()
