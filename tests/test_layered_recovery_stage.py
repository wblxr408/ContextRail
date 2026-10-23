"""D01/D05/H02 current phase consumers; run together after implementation."""
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from contextrail import Store,Target,Ref,Selection,SelectionGroup
from contextrail.context import Compiler
from contextrail.host import ContextTools
from contextrail.errors import RailError
from contextrail.project import load_project


class LayeredRecoveryStage(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='rail-layered-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'.contextrail.toml').write_text('[project]\ntask="stage"\n',encoding='utf-8')
        self.config=load_project(self.root);self.scope=self.config.scope
        self.config.store.parent.mkdir(parents=True)
        self.store=Store(self.config.store);self.addCleanup(self.store.close)
        self.lease=self.store.create_task(self.scope,Target('host','local','external'),'Keep exact requirement REQUIRED-17',allowed_providers=('local',))
        self.required=self.put('contract','REQUIRED-17: preserve source revisions.\n')
        self.docs=[self.put('notes/'+str(i), '# Heading\n\n**Status:** Accepted\n\n'+('材料'+str(i)+'，保留唯一细节TAIL-'+str(i)+'.\n')*600) for i in range(4)]
        self.sid=self.snapshot()
        self.tools=ContextTools(self.store,self.scope,'host')

    def put(self,name,text):return self.store.put(self.scope,self.lease,name,text.encode() if isinstance(text,str) else text,expected_revision=0)
    def snapshot(self,groups=()):return self.store.snapshot(self.scope,self.lease,(Selection(self.required,required=True),*(Selection(r,priority=20-i) for i,r in enumerate(self.docs))),groups=groups)
    def brief(self,**kwargs):return self.tools.call('context.brief',{'snapshot':self.sid,'budget':10000,**kwargs})

    def test_coverage_before_long_body_and_exact_readback(self):
        value=self.brief()
        self.assertIn('REQUIRED-17',value['required'][0]['text'])
        self.assertEqual(len(value['optional']),4)
        self.assertTrue(all(e['tier'] in {'L0','L1'} for e in value['optional']))
        for e in value['optional']:
            self.assertIn('材料',e['text']);self.assertNotIn('**Status:**',e['text'])
            raw=self.tools.call('context.get',e['ref'])['content'];self.assertIn('TAIL-',raw)
        loss=value['loss']['task_evidence']
        self.assertEqual((loss['total'],loss['selected'],loss['omitted'],loss['truncated']),(5,1,4,4))
        self.assertLessEqual(len(json.dumps(value,ensure_ascii=False,separators=(',',':'),sort_keys=True).encode()),10000)

    def test_overlapping_groups_requested_are_whole_or_error(self):
        self.docs=[self.put('group/'+str(i),'完整依赖'+str(i)*700) for i in range(3)]
        groups=(SelectionGroup('a',tuple(self.docs[:2])),SelectionGroup('b',tuple(self.docs[1:])))
        self.sid=self.snapshot(groups)
        value=self.brief(requested=[asdict(self.docs[0])],budget=20000)
        self.assertEqual({e['tier'] for e in value['optional']},{'L2'})
        self.assertTrue(all(e['reason']=='requested_group' for e in value['optional']))
        with self.assertRaises(RailError):self.brief(requested=[asdict(self.docs[0])],budget=2400)

    def test_binary_group_remains_directory_and_never_half_loaded(self):
        self.docs=[self.put('binary',b'\xff\x00'),self.put('text-in-group','valid short body')]
        self.sid=self.snapshot((SelectionGroup('mixed',tuple(self.docs)),))
        value=self.brief(budget=16000)
        self.assertEqual({e['tier'] for e in value['optional']},{'directory'})
        self.assertTrue(all(e['reason']=='group_contains_unsupported' for e in value['optional']))
        self.assertEqual(value['loss']['task_evidence']['unsupported'],1)
        self.assertEqual(self.tools.call('context.get',asdict(self.docs[0]))['encoding'],'base64')

    def test_directory_pagination_and_unknown_history(self):
        self.docs=[self.put('many/'+str(i),'long page '+str(i)+' '+('Z'*12000)) for i in range(12)]
        self.sid=self.snapshot();seen=[]
        for offset in range(0,12,3):
            value=self.brief(offset=offset,limit=3,budget=10000);seen.extend(e['ref']['name'] for e in value['optional'])
            self.assertEqual(value['navigation']['total'],12)
            self.assertEqual(value['loss']['task_evidence']['total'],13)
            self.assertIsNone(value['loss']['native_history']['total'])
            self.assertIsNone(value['loss']['main_history']['total'])
        self.assertEqual(len(set(seen)),12)
        foreign=self.put('foreign','not in snapshot')
        with self.assertRaises(RailError):self.brief(requested=[asdict(foreign)])

    def bridge(self):
        from contextrail.bridge import BridgeEvidence
        bundle=self.root/'bundle';bundle.mkdir();(bundle/'user.md').write_text('当前要求必须保留\n',encoding='utf-8')
        def req(rid,scope,always):return {'id':rid,'revision':1,'status':'active','kind':'constraint' if always else 'deliverable','scope':scope,'always_load':always,'statement':'当前要求必须保留','source':{'path':'user.md','quote':'当前要求必须保留','locator':'line1'},'acceptance':'完整回读要求','progress':'pending','evidence':[]}
        ledger={'schema':1,'task_id':'stage','snapshot_id':'stage/rev1','requirements':[req('R001','global',True),req('R002','later',False),req('R003','implementation',False)]}
        (bundle/'requirements.json').write_text(json.dumps(ledger,ensure_ascii=False),encoding='utf-8')
        exported=BridgeEvidence(self.store,self.scope,'host').import_bundle(bundle,'implementation',self.lease)
        self.sid=exported['snapshot'];return bundle

    def test_bridge_requirement_coverage_and_stale_authority(self):
        bundle=self.bridge();value=self.brief(budget=15000)
        self.assertEqual(value['bridge']['required_ids'],['R001'])
        self.assertEqual(value['loss']['bridge_requirements']['total'],3)
        self.assertEqual(value['loss']['bridge_requirements']['omitted'],1)
        rows={r['id']:r for r in value['bridge']['requirements']}
        self.assertEqual(rows['R002']['reason'],'outside_bridge_scope')
        (bundle/'user.md').write_text('要求已变，不能恢复旧版本',encoding='utf-8')
        with self.assertRaises(RailError):self.brief(budget=15000)

    def test_explain_actions_and_observed_receipt_without_consumption_claim(self):
        self.store.begin_action(self.scope,self.lease,'apply-change');self.store.finish_action(self.scope,self.lease,'apply-change',state='succeeded')
        self.sid=self.snapshot()
        with self.store.transaction():self.store._event(self.scope,'native_context.prepared',snapshot=self.sid,selected_refs=[asdict(self.required)],loss={'memory':{'unknown':None}})
        value=self.tools.call('context.explain',{'snapshot':self.sid,'budget':12000})
        self.assertEqual(value['explanation']['actions']['entries'][0]['state'],'succeeded')
        self.assertEqual(value['explanation']['last_prepared']['metadata']['snapshot'],self.sid)
        self.assertIsNone(value['explanation']['last_prepared']['loss']['memory']['unknown'])
        self.assertEqual(value['explanation']['model_consumption'],'unknown')
        self.assertEqual(value['explanation']['timeline']['tool'],'context.timeline')

    def test_cli_state_export_and_real_mcp_brief(self):
        self.store.close()
        dest=self.root/'STATE.md'
        args=[sys.executable,'-m','contextrail','brief','--workspace',str(self.root),'--session','host','--snapshot',self.sid,'--budget','12000','--state-output',str(dest)]
        child=subprocess.run(args,capture_output=True,timeout=30)
        self.assertEqual(child.returncode,0,child.stderr.decode('utf-8','replace'))
        value=json.loads(child.stdout);body=dest.read_text(encoding='utf-8')
        self.assertIn(self.sid,body);self.assertIn('task_evidence',body);self.assertIn('context.get',body)
        again=subprocess.run(args,capture_output=True,timeout=30);self.assertNotEqual(again.returncode,0)
        self.assertEqual(dest.read_text(encoding='utf-8'),body)
        async def roundtrip():
            from mcp import ClientSession,StdioServerParameters
            from mcp.client.stdio import stdio_client
            server=StdioServerParameters(command=sys.executable,args=['-m','contextrail','mcp','--workspace',str(self.root),'--session','host'])
            async with stdio_client(server) as (read,write):
                async with ClientSession(read,write) as session:
                    await session.initialize();response=await session.call_tool('context.brief',{'snapshot':self.sid,'budget':12000})
                    self.assertFalse(response.isError);result=json.loads(response.content[0].text)
                    self.assertEqual(result['snapshot'],self.sid);self.assertEqual(result['loss'],value['loss'])
        asyncio.run(roundtrip())

    def test_import_loss_counts_files_and_preserves_queued_boundary(self):
        # The same phase API must exist before setting up a real original queue.
        self.brief()
        from contextrail.directory_sync import DirectorySync
        from contextrail.directory_memory import enqueue_directory
        app=Path.home()/'.codex/services/engineering-memory/app';overlay=Path(__file__).resolve().parents[1]/'integrations/engineering_memory'
        home=self.root/'existing-memory';home.mkdir()
        code='import sys;from pathlib import Path;sys.path[:0]=sys.argv[1:3];from listener import Store;s=Store(Path(sys.argv[3]));s.db.close()'
        process=subprocess.run([sys.executable,'-c',code,str(overlay),str(app),str(home)],capture_output=True,timeout=20)
        self.assertEqual(process.returncode,0,process.stderr.decode('utf-8','replace'))
        folder=self.root/'docs';folder.mkdir();(folder/'one.md').write_text('Ordinary factual source document.',encoding='utf-8');(folder/'two.py').write_text('a=1',encoding='utf-8')
        page=DirectorySync(self.store,self.config,self.lease).sync(root='docs')
        result=enqueue_directory(self.store,self.config,self.lease,page['run'],home,code=app,budget=8000)
        loss=result['loss']['memory_import']
        self.assertEqual((loss['total'],loss['selected'],loss['unsupported']),(2,1,1))
        self.assertEqual(loss['unit'],'files');self.assertEqual(result['knowledge_saved'],'unknown_not_observed')

    def test_native_loss_distinguishes_independent_unknown_and_observed_truncation(self):
        from contextrail.native_clients import prepare_clients,host_hook
        self.bridge()
        layout=prepare_clients(self.store,self.config,self.lease,self.sid,self.root/'clients',clients=('codex',),
            native_memory_clients=('codex',),context_bytes=10000,hook_bytes=16000)
        payload={'hook_event_name':'UserPromptSubmit','session_id':'native-phase','cwd':str(self.root),'prompt':'继续实现阶段'}
        value=host_hook(layout['registry'],'codex',payload,native_memory=True)
        self.assertNotEqual(value.get('decision'),'block',value)
        context=json.loads(value['hookSpecificOutput']['additionalContext'])
        self.assertIsNone(context['loss']['memory']['total'])
        self.assertIsNone(context['loss']['memory']['selected'])
        self.assertIsNone(context['loss']['memory']['truncated'])
        self.assertIn('task_evidence',context['task']['loss'])
        # Actual foreground hook subprocess, with bounded observable history.
        command=[sys.executable,'-c','import json;print(json.dumps({"hookSpecificOutput":{"additionalContext":"历史🙂"*8000}}))']
        registry=Path(layout['registry']);data=json.loads(registry.read_text(encoding='utf-8'))
        data['memory']={'command':command,'timeout_seconds':10};registry.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        value=host_hook(registry,'codex',payload)
        self.assertNotEqual(value.get('decision'),'block',value)
        context=json.loads(value['hookSpecificOutput']['additionalContext']);loss=context['loss']['memory']
        self.assertEqual(loss['total'],len(('历史🙂'*8000).encode()))
        self.assertGreater(loss['omitted'],0);self.assertEqual(loss['truncated'],loss['omitted'])

    def test_event_import_loss_tracks_committed_page_and_replay(self):
        from contextrail.event_bridge import import_events
        app=Path.home()/'.codex/services/engineering-memory/app';home=self.root/'native-import'
        script='''import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from listener import Store
home=Path(sys.argv[2]);home.mkdir();source=home/'source.jsonl';workspace=sys.argv[3]
rows=[{'type':'session_meta','payload':{'id':'phase-import','cwd':workspace}}]
rows.extend({'type':'response_item','timestamp':'2026-10-23T12:00:00Z','payload':{'type':'message','role':'user','id':'u'+str(i),'content':'Native event '+str(i)}} for i in range(2))
source.write_text(''.join(json.dumps(r)+'\\n' for r in rows),encoding='utf-8')
s=Store(home);s.ingest(source,'codex');s.db.close()
'''
        process=subprocess.run([sys.executable,'-c',script,str(app),str(home),str(self.root)],capture_output=True,timeout=20)
        self.assertEqual(process.returncode,0,process.stderr.decode('utf-8','replace'))
        value=import_events(self.store,self.config,self.lease,home,'codex','phase-import',limit=1,budget=2048)
        loss=value['loss']['native_import'];self.assertEqual(loss['total'],3)  # includes session lifecycle
        self.assertEqual((loss['selected'],loss['omitted']),(1,2));self.assertEqual(value['next_offset'],1)
        before=self.store.task(self.scope)['version']
        again=import_events(self.store,self.config,self.lease,home,'codex','phase-import',limit=1,budget=2048)
        self.assertEqual(again['imported'],0);self.assertEqual(self.store.task(self.scope)['version'],before)
        self.assertEqual(again['loss']['native_import'],loss)

    def test_exported_state_and_brief_remain_history_in_existing_import_queue(self):
        from contextrail.layered import state_markdown
        import shutil
        value=self.brief();generated=self.root/'generated';generated.mkdir()
        shutil.copy2(Path(__file__).resolve().parents[1]/'contextrail/learning_origin.py',generated/'learning_origin.py')
        request=self.root/'derived.json';request.write_text(json.dumps([json.dumps(value,ensure_ascii=False),state_markdown(value)],ensure_ascii=False),encoding='utf-8')
        app=Path.home()/'.codex/services/engineering-memory/app';home=self.root/'derived-queue'
        script='''import json,sys
from pathlib import Path
sys.path[:0]=sys.argv[1:3]
from listener import Store
from history_import import enqueue
home=Path(sys.argv[3]);home.mkdir();store=Store(home);texts=json.loads(Path(sys.argv[4]).read_text(encoding='utf-8'))
counts=[]
for i,text in enumerate(texts):counts.append(enqueue(store,home/('state'+str(i)+'.md'),sys.argv[5],'derived-'+str(i),text,'2026-10-23T12:00:00Z','document'))
print(json.dumps({'queued':counts,'jobs':store.db.execute('SELECT count(*) FROM jobs').fetchone()[0],'sources':store.db.execute('SELECT count(*) FROM source_events').fetchone()[0]}))
store.db.close()
'''
        child=subprocess.run([sys.executable,'-c',script,str(generated),str(app),str(home),str(request),str(self.root)],capture_output=True,timeout=20)
        self.assertEqual(child.returncode,0,child.stderr.decode('utf-8','replace'))
        result=json.loads(child.stdout);self.assertEqual(result['queued'],[0,0]);self.assertEqual(result['jobs'],0);self.assertEqual(result['sources'],2)


if __name__=='__main__':unittest.main()
