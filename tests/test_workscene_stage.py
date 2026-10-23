"""E02/E03/B08 consumer contract: one baseline and grouped phase acceptance."""
from dataclasses import asdict
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

from contextrail import Store,Scope,Target
from contextrail.host import ContextTools
from contextrail.errors import RailError
from contextrail.models import canonical
from contextrail.tool_results import OutputRecorder


class WorksceneStage(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='rail-scene-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.dbpath=self.root/'rail.sqlite';self.scope=Scope('local','p','w','scene')
        self.store=Store(self.dbpath);self.addCleanup(lambda:self.store.close())
        self.lease=self.store.create_task(self.scope,Target('host','local','external'),'Recover actual work',allowed_providers=('local',))
        self.tools=ContextTools(self.store,self.scope,'host')
        self.source=self.store.put(self.scope,self.lease,'user','任务[build]：构建。阻塞[build]：等待依赖。'.encode(),expected_revision=0)
        self.config=SimpleNamespace(scope=self.scope,workspace=self.root)

    def scene(self):
        try:value=ContextTools(self.store,self.scope,'host').call('context.workscene',{})
        except RailError:value={'schema':'unavailable'}
        self.assertEqual(value.get('schema'),'contextrail.workscene.v1','current reader has no recoverable workscene')
        from contextrail.workscene import WorkScene
        return WorkScene(self.store,self.scope,self.lease,self.root)

    def record(self,call='build',argv=None,code=0,partial=False,timeout=False,stdout='done',tool='exec_command',paths=None):
        return OutputRecorder(self.store,self.scope,self.lease,self.root).capture({
            'call_id':call,'tool':tool,'argv':argv or [sys.executable,'-m','build'],'cwd':str(self.root),
            'started_at':time.time()-1,'finished_at':time.time(),'exit_code':code,'partial':partial,'timed_out':timeout,
            'stdout':stdout,'stderr':'','source_paths':paths or []},budget=5000)

    def native(self,events,session='native',offset=0,limit=100):
        from contextrail.event_stream import SCHEMA,capture_record
        from contextrail.source_events import SCHEMA as SOURCE_SCHEMA,workspace_key
        from contextrail.event_bridge import import_events
        home=self.root/'memory';home.mkdir(exist_ok=True)
        with closing(sqlite3.connect(home/'state.sqlite')) as db:
            db.row_factory=sqlite3.Row
            db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,source TEXT,payload TEXT,state TEXT,created REAL,available REAL)')
            db.executescript(SOURCE_SCHEMA+SCHEMA)
            db.execute('BEGIN IMMEDIATE')
            context={}
            rows=[{'type':'session_meta','payload':{'id':session,'cwd':str(self.root)}}]+events
            for index,row in enumerate(rows):capture_record(db,'codex',row,context,str(home/(session+'.jsonl')),index,index+1,{'epoch':0},lambda v:workspace_key(str(Path(v).resolve())))
            db.commit()
        report=import_events(self.store,self.config,self.lease,home,'codex',session,offset=offset,limit=limit,budget=64000)
        self.assertGreater(report['total'],0,'native fixture did not enter the selected session')
        return report

    def call(self,name,args,identity='c1'):
        return {'type':'response_item','payload':{'type':'function_call','call_id':identity,'name':name,'arguments':json.dumps(args)}}
    def result(self,value,identity='c1'):
        return {'type':'response_item','payload':{'type':'function_call_output','call_id':identity,'output':json.dumps(value)}}
    def message(self,text,role='user',mid='u1'):
        return {'type':'response_item','payload':{'type':'message','role':role,'id':mid,'content':[{'type':'input_text' if role=='user' else 'output_text','text':text}]}}

    def test_host_result_records_workspace_and_exact_sources(self):
        self.record(argv=[sys.executable,'-m','venv','.venv'],paths=['src/app.py'])
        facts=self.store.task_state(self.scope)
        self.assertTrue(facts,'saved result did not produce source-linked workscene state')
        scene=self.scene().read();self.assertTrue(any(r['payload'].get('cwd')==str(self.root) for r in scene['entries']));self.assertIn('src/app.py',canonical(scene));self.assertTrue(all(r['sources'] for r in scene['entries']))

    def test_native_git_call_is_not_a_commit_until_matching_result(self):
        scene=self.scene()
        self.native([self.call('exec_command',{'cmd':'git commit -m demo','workdir':str(self.root)})])
        requested=scene.read();self.assertNotIn('abc1234',canonical(requested));self.assertIn('requested',canonical(requested))
        self.native([self.call('exec_command',{'cmd':'git commit -m demo','workdir':str(self.root)}),self.result({'exit_code':0,'output':'[main abc1234] demo'})])
        self.assertIn('abc1234',canonical(scene.read()));self.assertIn('source_reported',canonical(scene.read()))

    def test_cross_session_results_do_not_confirm_another_call(self):
        scene=self.scene();self.native([self.call('exec_command',{'cmd':'git commit -m demo'})],session='one')
        self.native([self.result({'exit_code':0,'output':'[main abc1234] demo'})],session='two')
        observed=[r for r in scene.read()['entries'] if r['status']=='observed' and r['category']=='git']
        self.assertEqual(observed,[])

    def test_result_before_call_and_replay_are_recoverable(self):
        scene=self.scene();events=[self.result({'exit_code':0,'output':'[main abc1234] demo'}),self.call('exec_command',{'cmd':'git commit -m demo'})]
        self.native(events,limit=2);self.native(events,offset=2,limit=2)
        a=scene.read();before=self.store.task(self.scope)['version'];self.native(events,offset=0,limit=100)
        self.assertIn('abc1234',canonical(a));self.assertEqual(self.store.task(self.scope)['version'],before)

    def test_user_rejection_and_blocker_survive_positive_assistant_reply(self):
        scene=self.scene();self.native([self.message('放弃[routeA]：旧方案不再做。\n阻塞[build]：等待依赖。'),self.message('已经搞定，一切很好。','assistant','a1')])
        topics={r['topic']:r for r in scene.read()['topics']}
        self.assertEqual(topics['routeA']['status'],'excluded');self.assertEqual(topics['build']['status'],'blocked')

    def test_task_tool_completed_is_only_a_claim(self):
        scene=self.scene();self.native([self.call('TaskUpdate',{'taskId':'build','status':'completed','subject':'完成构建'})])
        self.assertEqual(next(r for r in scene.read()['topics'] if r['topic']=='build')['status'],'completion_claim')
        self.assertTrue(next(r for r in scene.read()['topics'] if r['topic']=='build')['inferred'])

    def test_bound_completion_rejects_other_or_partial_outputs(self):
        scene=self.scene();scene.declare('build','blocked','等待构建',self.source,expected_call_id='build')
        self.record(call='other');self.record(call='build',partial=True)
        with self.assertRaises(RailError):scene.complete('build','other')
        with self.assertRaises(RailError):scene.complete('build','build')
        self.assertEqual(next(r for r in scene.read()['topics'] if r['topic']=='build')['status'],'blocked')

    def test_bound_success_completes_once_across_reopen(self):
        scene=self.scene();scene.declare('build','blocked','等待构建',self.source,expected_call_id='build');self.record()
        scene.complete('build','build');version=self.store.task(self.scope)['version']
        self.store.close();self.store=Store(self.dbpath);scene=self.scene();scene.complete('build','build')
        self.assertEqual(self.store.task(self.scope)['version'],version);self.assertEqual(next(r for r in scene.read()['topics'] if r['topic']=='build')['status'],'completed')

    def test_policy_excluded_file_body_is_not_reintroduced(self):
        scene=self.scene();(self.root/'.contextrail.toml').write_text('[capture]\nignore=["private/**"]\n',encoding='utf-8')
        self.native([self.call('Read',{'file_path':str(self.root/'private/secret.txt')}),self.result({'text':'private-body-7593'})])
        self.assertNotIn('private-body-7593',canonical(scene.read()));self.assertNotIn('secret.txt',canonical(scene.read()))

    def reserve(self,scene,identity='job',background=True):
        return scene.reserve(identity,argv=[sys.executable,'payload.py'],background=background,source=self.source)
    def process(self,code=None):
        p=subprocess.Popen([sys.executable,'-u','-c',code or "import time;print('ready',flush=True);time.sleep(60)"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),start_new_session=os.name!='nt')
        def cleanup():
            if p.poll() is None:p.kill();p.wait()
            p.stdout.close();p.stderr.close()
        self.addCleanup(cleanup)
        return p

    def test_background_choice_is_explicit_and_reservation_never_reruns(self):
        scene=self.scene()
        with self.assertRaises(RailError):scene.reserve('job',argv=['payload'],source=self.source)
        first=self.reserve(scene);again=self.reserve(scene)
        self.assertTrue(first['execute_once']);self.assertFalse(again['execute_once']);self.assertFalse(again['automatic_retry'])

    def test_live_process_partial_output_reopens_with_same_identity(self):
        scene=self.scene();self.reserve(scene);p=self.process();self.assertEqual(p.stdout.readline().strip(),'ready')
        scene.started('job',pid=p.pid,host_session='native-session',process_group=os.name!='nt');scene.partial('job',stdout='进度🙂 api_key=sk-'+'A'*40,stderr='')
        self.store.close();self.store=Store(self.dbpath);scene=self.scene();result=scene.recover('job')
        self.assertEqual(result['status'],'running');self.assertEqual(result['handle']['pid'],p.pid);self.assertNotIn('sk-'+'A'*40,canonical(result));self.assertTrue(result['partial_refs']);self.assertFalse(self.reserve(scene)['execute_once'])

    def test_stopped_without_receipt_is_unknown_until_actual_result(self):
        scene=self.scene();self.reserve(scene);p=self.process("print('ready',flush=True)");self.assertEqual(p.stdout.readline().strip(),'ready')
        # A short process may be gone before registration: a host should retain its tool session instead.
        scene.started('job',host_session='native-ended-session');p.wait()
        self.assertEqual(scene.recover('job')['status'],'unknown');self.assertFalse(self.reserve(scene)['execute_once'])
        self.record(call='job');scene.finished('job','job');self.assertEqual(scene.recover('job')['status'],'succeeded')

    def test_timeout_does_not_claim_process_stopped(self):
        scene=self.scene();self.reserve(scene);p=self.process();p.stdout.readline();scene.started('job',pid=p.pid,host_session='s',process_group=os.name!='nt')
        scene.timed_out('job');result=scene.recover('job');self.assertEqual(result['status'],'running');self.assertTrue(result['timed_out']);self.assertFalse(result['automatic_retry'])

    def test_cancel_exact_tree_preserves_unrelated_process(self):
        scene=self.scene();self.reserve(scene)
        child="import time;print('child-ready',flush=True);time.sleep(60)"
        parent="import subprocess,sys,time,json;p=subprocess.Popen([sys.executable,'-u','-c',"+repr(child)+"],stdout=subprocess.PIPE,text=True);p.stdout.readline();print(json.dumps({'child':p.pid}),flush=True);time.sleep(60)"
        p=self.process(parent);child_pid=json.loads(p.stdout.readline())['child'];other=self.process();other.stdout.readline()
        scene.started('job',pid=p.pid,host_session='s',process_group=os.name!='nt');result=scene.cancel('job')
        p.wait(timeout=10);self.assertEqual(result['status'],'cancelled');self.assertIsNone(other.poll())
        from contextrail.process_identity import inspect_process
        self.assertNotEqual(inspect_process(child_pid).get('state'),'running')

    def test_process_identity_mismatch_never_cancels_a_reused_pid(self):
        scene=self.scene();self.reserve(scene);p=self.process();p.stdout.readline();scene.started('job',pid=p.pid,host_session='s',process_group=os.name!='nt')
        with self.store.db:
            handle=json.loads(self.store.db.execute('SELECT handle FROM scene_jobs WHERE id=?',('job',)).fetchone()[0]);handle['created']='different-generation';self.store.db.execute('UPDATE scene_jobs SET handle=? WHERE id=?',(json.dumps(handle),'job'))
        result=scene.cancel('job');self.assertEqual(result['status'],'identity_mismatch');self.assertIsNone(p.poll())

    def test_exported_state_separates_unknown_and_source_linked_work(self):
        scene=self.scene();self.reserve(scene);scene.started('job',host_session='opaque-host-session');scene.declare('route','excluded','放弃旧方案',self.source)
        report=scene.export_state(self.root/'bridge-state');data=json.loads(Path(report['json']).read_text(encoding='utf-8'))
        self.assertEqual(data['jobs'][0]['status'],'unknown');self.assertEqual(data['topics'][0]['status'],'excluded');self.assertTrue(data['topics'][0]['sources'])
        text=Path(report['state']).read_text(encoding='utf-8');self.assertIn('opaque-host-session',text);self.assertIn('user',text)

    def test_reader_paginates_actual_bytes_and_cannot_cancel(self):
        scene=self.scene()
        for i in range(8):scene.declare('route'+str(i),'excluded','放弃路线🙂'+str(i),self.source)
        reader=Store.open_readonly(self.dbpath);self.addCleanup(reader.close);tools=ContextTools(reader,self.scope,'host',max_read_bytes=2300)
        page=tools.call('context.workscene',{'limit':2,'budget':2200});self.assertLessEqual(len(canonical(page).encode()),2200);self.assertIsNotNone(page['next_offset'])
        with self.assertRaises(RailError):tools.call('context.workscene',{'cancel':'job'})

    def test_existing_action_terminal_receipt_is_reconciled_as_host_attested(self):
        scene=self.scene();self.reserve(scene);scene.started('job',host_session='old-host-session')
        self.store.finish_action(self.scope,self.lease,'job',state='failed')
        recovered=scene.recover('job');self.assertEqual(recovered['status'],'failed');self.assertEqual(recovered['terminal_basis'],'original_host_action_ledger')

    def test_process_missing_after_host_releases_handle_is_stopped_unknown(self):
        scene=self.scene();self.reserve(scene);p=self.process();p.stdout.readline();scene.started('job',pid=p.pid,host_session='s',process_group=os.name!='nt')
        p.terminate();p.wait()
        if os.name=='nt':p._handle.Close()  # The original host released the terminated process object.
        recovered=scene.recover('job');self.assertEqual(recovered['status'],'stopped_outcome_unknown');self.assertFalse(self.reserve(scene)['execute_once'])

    def test_workscene_query_finds_a_file_without_scanning_every_page(self):
        self.scene();self.record(paths=['src/排障.py'])
        try:page=self.tools.call('context.workscene',{'category':'file','query':'src/排障.py','budget':3000})
        except RailError:page={'error':'no_metadata_query'}
        self.assertNotIn('error',page);self.assertEqual(len(page['entries']),1);self.assertTrue(page['entries'][0]['sources']);self.assertEqual(page['topics'],[])

    def test_host_environment_is_preserved_as_observation_not_argv_guess(self):
        scene=self.scene();data={'call_id':'env','tool':'exec_command','argv':['git','status'],'cwd':str(self.root),'started_at':time.time()-1,'finished_at':time.time(),'exit_code':0,'stdout':'','stderr':'',
            'environment':{'host_executable':sys.executable,'host_prefix':sys.prefix,'host_base_prefix':sys.base_prefix,'virtual_env':str(self.root/'.venv')}}
        try:OutputRecorder(self.store,self.scope,self.lease,self.root).capture(data,budget=5000);accepted=True
        except RailError:accepted=False
        self.assertTrue(accepted,'original receiver cannot retain the actually declared host environment')
        observations=[r for r in scene.read(category='environment')['entries'] if r['payload'].get('basis')=='host_attested_environment']
        self.assertEqual(observations[0]['payload']['environment']['host_executable'],sys.executable);self.assertTrue(observations[0]['sources'])

    def test_large_native_command_stays_importable_with_original_ref(self):
        scene=self.scene();command='echo '+('payload '*14000)
        try:self.native([self.call('exec_command',{'cmd':command})]);accepted=True
        except RailError:accepted=False
        self.assertTrue(accepted,'derived scene metadata made a valid native event unreadable')
        rows=scene.read()['entries'];self.assertTrue(rows);self.assertIn('projection_loss',canonical(rows))
        original=self.store.get(self.scope,__import__('contextrail').Ref(**rows[0]['sources'][0])).content
        self.assertEqual(json.loads(original)['data']['arguments']['cmd'],command)

    def test_workscene_navigation_does_not_accumulate_mandatory_journal_items(self):
        self.scene()
        for i in range(3):self.record(call='observed-'+str(i),paths=['src/'+str(i)+'.py'])
        active=self.store.task_state(self.scope)
        self.assertEqual(len(active),1,'every operational observation became another mandatory task-state item')
        self.assertEqual(active[0].kind,'working_scene');self.assertGreater(self.scene().read()['total'],3)

    def test_generated_workscene_and_state_do_not_reenter_automatic_learning(self):
        from contextrail.learning_origin import project_candidate
        scene=self.scene();self.record()
        view=project_candidate({'kind':'document','user':'','final':canonical(scene.read())})
        self.assertFalse(view['_origin']['eligible_for_learning'],'new derived view was classified as fresh learning')
        state=scene.export_state(self.root/'bridge-state-learning')
        page=project_candidate({'kind':'document','user':'','final':Path(state['state']).read_text(encoding='utf-8')})
        self.assertFalse(page['_origin']['eligible_for_learning'])


if __name__=='__main__':unittest.main()
