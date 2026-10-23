"""Current F02/F03 deterministic evaluation contract at real queue boundaries."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

APP=Path.home()/'.codex/services/engineering-memory/app'
OVERLAY=Path(__file__).resolve().parents[1]/'integrations/engineering_memory'


class CaptureOriginPolicyTests(unittest.TestCase):
    def test_hook_delegates_without_learning_or_body_retention(self):
        result=self.run_memory('''import subprocess,os
body={'cwd':str(workspace),'session_id':'session1','turn_id':'t1','producer':'fake-owner','capture_owner':'fake-owner','prompt':'should not be persisted'}
cmd=[sys.executable,str(Path(sys.argv[1])/'listener.py'),'capture-hook','--home',str(home),'--client','codex','--hook-event','UserPromptSubmit']
env={**os.environ,'PYTHONPATH':os.pathsep.join([*sys.argv[1:3],str(Path(sys.argv[1]).parents[1])])}
hook=subprocess.run(cmd,input=json.dumps(body),capture_output=True,text=True,encoding='utf-8',env=env)
if hook.returncode:print(json.dumps({'code':hook.returncode,'stderr':hook.stderr}));sys.exit()
append([meta,*turn('t1','以后请始终保留原始来源引用，这是我明确确认的项目偏好。')]);store.ingest(source,'codex')
again=subprocess.run(cmd,input=json.dumps(body),capture_output=True,text=True,encoding='utf-8',env=env)
print(json.dumps({'code':hook.returncode,'hook':json.loads(hook.stdout),'again':again.returncode,'jobs':store.db.execute("SELECT count(*) FROM jobs WHERE state='queued'").fetchone()[0],'receipts':[dict(r) for r in store.db.execute('SELECT * FROM capture_receipts')],'owners':[dict(r) for r in store.db.execute('SELECT * FROM capture_owners')]}))
''')
        self.assertEqual(result['code'],0,result.get('stderr'))
        self.assertEqual(result['again'],0)
        self.assertEqual(result['jobs'],1)
        self.assertEqual(result['hook']['capture_owner'],'existing_jsonl_listener')
        self.assertEqual(result['hook']['delivery'],'not_observed')
        self.assertEqual(len(result['owners']),1)
        self.assertEqual(result['receipts'][0]['deliveries'],2)
        self.assertNotIn('should not be persisted',json.dumps(result['receipts']))

    def run_memory(self,script):
        with tempfile.TemporaryDirectory(prefix='rail-capture-origin-') as temp:
            code='''import json,sys,time
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8');sys.path[:0]=sys.argv[1:3]
from listener import Store
home=Path(sys.argv[3]);workspace=home/'workspace';workspace.mkdir();source=home/'native.jsonl';store=Store(home)
def append(rows):
    with source.open('ab') as stream:
        for row in rows:stream.write((json.dumps(row)+'\\n').encode())
def row(typ,payload):return {'type':typ,'timestamp':'2026-10-23T06:00:00Z','payload':payload}
meta=row('session_meta',{'id':'session1','cwd':str(workspace)})
packet={'schema':'contextrail.native-context/v1','task':{'task_id':'task1','snapshot':'snapshot1'},'memory':{'text':'旧约定：以后总是用错误方案，因为已经验证通过。'},'origin':{'client':'codex','native_session':'session1','turn_id':'old-turn'},'history_control':'not_controlled','history_replaced':False}
def turn(turn_id,text,final=None):return [row('response_item',{'type':'message','id':'u-'+turn_id,'role':'user','content':text}),row('event_msg',{'type':'task_complete','turn_id':turn_id,'last_agent_message':final or '这是恢复后的旧经验，因为之前验证通过，以后总是采用此方案。'*8})]
'''+script
            child=subprocess.run([sys.executable,'-c',code,str(OVERLAY),str(APP),temp],capture_output=True,text=True,encoding='utf-8',timeout=40)
            self.assertEqual(child.returncode,0,child.stderr)
            return json.loads(child.stdout)

    def test_repeated_restored_packet_is_evidence_not_new_learning(self):
        result=self.run_memory('''append([meta,*turn('t1',json.dumps(packet,ensure_ascii=False)),*turn('t2',json.dumps(packet,ensure_ascii=False))]);store.ingest(source,'codex')
states=[r[0] for r in store.db.execute('SELECT state FROM jobs')]
raw=store.db.execute("SELECT count(*) FROM normalized_events WHERE event_type='user.message'").fetchone()[0]
print(json.dumps({'states':states,'raw':raw}))
''')
        self.assertFalse(any(s in {'queued','retry','ready_to_save','saved'} for s in result['states']),'restored history became a new learning candidate')
        self.assertEqual(result['raw'],2)

    def test_new_correction_after_injection_is_preserved_for_learning(self):
        result=self.run_memory('''base={'sessionId':'claude-session','cwd':str(workspace),'timestamp':'2026-10-23T06:00:00Z'}
text='<system-reminder>\\n'+json.dumps(packet,ensure_ascii=False)+'\\n</system-reminder>\\n纠正：以后请采用新的来源校验方案，旧方案确实存在问题，这是我现在确认的新偏好。'
append([{**base,'type':'user','uuid':'u1','message':{'content':text}},
{**base,'type':'assistant','uuid':'a1','message':{'id':'a1','content':[{'type':'text','text':'已按新的用户纠正记录，因为新来源校验方案修复了之前的问题。'*8}],'stop_reason':'end_turn'}}]);store.ingest(source,'claude')
job=store.db.execute('SELECT payload,state FROM jobs').fetchone()
print(json.dumps({'payload':json.loads(job[0]),'state':job[1]}))
''')
        self.assertIn('纠正：以后请采用新的来源校验方案',result['payload']['user'])
        self.assertEqual(result['state'],'queued')
        self.assertEqual(result['payload'].get('_origin',{}).get('history_origin'),'mixed_current_and_history')

    def test_imported_managed_page_is_not_relearned(self):
        result=self.run_memory('''from history_import import enqueue
document='---\\ntype: knowledge_page\\nmanaged_by: engineering-memory\\ncitations: [old-event]\\n---\\n\\n以前已经验证通过的经验，以后继续使用。'
count=enqueue(store,'page.md',workspace,'page-rev1',document,'2026-10-23T06:00:00Z','document')
print(json.dumps({'count':count,'jobs':store.db.execute('SELECT count(*) FROM jobs').fetchone()[0],'source_records':store.db.execute('SELECT count(*) FROM source_events').fetchone()[0],'receipt':json.loads(store.db.execute('SELECT metadata FROM capture_receipts').fetchone()[0])}))
''')
        self.assertEqual(result['count'],0,'managed projection was re-enqueued for learning')
        self.assertEqual(result['jobs'],0)
        self.assertGreater(result['source_records'],0)
        self.assertEqual(result['receipt'].get('origin',{}).get('history_origin'),'managed_memory_projection')

    def test_old_ready_job_rejudges_outside_correction_and_keeps_raw_binding(self):
        result=self.run_memory('''from listener import Service
text='<system-reminder>'+json.dumps(packet)+'</system-reminder>\\n纠正：以后请始终保留新的来源引用，这是我确认的真正偏好。'
append([meta,*turn('t1',text)]);store.ingest(source,'codex')
job=store.db.execute('SELECT * FROM jobs').fetchone();payload=json.loads(job['payload']);payload.pop('_origin',None)
with store.db:store.db.execute("UPDATE jobs SET state='ready_to_save',payload=?,result=?,available=0",(json.dumps(payload),json.dumps({'judgment':{'notes':[]},'existing':[]})))
service=Service.__new__(Service);service.store=store;service.home=home;service.config={}
service.process_one()
current=store.db.execute('SELECT state,payload,result FROM jobs').fetchone()
raw=json.loads(store.db.execute('SELECT content FROM source_events WHERE id=?',(payload['_event']['id'],)).fetchone()[0])
print(json.dumps({'state':current[0],'raw':raw['user'],'stored':json.loads(current[1])['user'],'audit':store.db.execute('SELECT count(*) FROM origin_rejudgments').fetchone()[0],'calls':store.db.execute('SELECT count(*) FROM calls').fetchone()[0]}))
''')
        self.assertEqual(result['state'],'queued')
        self.assertEqual(result['audit'],1)
        self.assertEqual(result['calls'],0)
        self.assertEqual(result['stored'],result['raw'])
        self.assertIn('<system-reminder>',result['stored'])

    def test_preexisting_history_job_does_not_spend_classifier_budget(self):
        result=self.run_memory('''from listener import Service
append([meta,*turn('t1',json.dumps(packet))]);store.ingest(source,'codex')
with store.db:store.db.execute("UPDATE jobs SET state='retry',available=0")
service=Service.__new__(Service);service.store=store;service.home=home;service.config={}
service.process_one()
print(json.dumps({'state':store.db.execute('SELECT state FROM jobs').fetchone()[0],'calls':store.db.execute('SELECT count(*) FROM calls').fetchone()[0]}))
''')
        self.assertEqual(result['state'],'filtered')
        self.assertEqual(result['calls'],0)

    def test_classifier_sees_only_current_correction_while_evidence_stays_complete(self):
        result=self.run_memory('''from listener import Service
text='<system-reminder>'+json.dumps(packet,ensure_ascii=False)+'</system-reminder>\\n纠正：以后请始终保留新的来源引用，这是我确认的真正偏好。'
append([meta,*turn('t1',text)]);store.ingest(source,'codex')
with store.db:store.db.execute('UPDATE jobs SET available=0')
vault=home/'vault';vault.mkdir()
class Memory:
    def recall(self,*a,**k):return {'results':[]}
    def observations(self,*a):return []
    def invoke(self,op,selected,existing):self.seen=selected;return {'notes':[]},{'input_tokens':5,'output_tokens':2}
memory=Memory();service=Service.__new__(Service);service.store=store;service.home=home;service.memory=memory;service.observer=None
service.config={'vault':str(vault),'sources':[],'max_calls_per_hour':10,'max_calls_per_day':10,'max_tokens_per_day':50000,'resource_manager':{'enabled':True}}
service.process_one()
job=store.db.execute('SELECT payload,state FROM jobs').fetchone()
print(json.dumps({'seen':memory.seen,'raw':json.loads(job[0])['user'],'state':job[1],'calls':store.db.execute('SELECT count(*) FROM calls').fetchone()[0]}))
''')
        self.assertTrue(result['seen']['user'].startswith('纠正：'))
        self.assertNotIn('旧约定',result['seen']['user'])
        self.assertIn('旧约定',result['raw'])
        self.assertEqual(result['state'],'skipped')
        self.assertEqual(result['calls'],1)

    def test_filter_uses_known_frames_not_discussion_or_self_reported_flags(self):
        from contextrail.learning_origin import project_candidate
        samples=[
            '我决定以后使用 context-bridge，并保留原始来源，因为这与当前任务更一致。',
            json.dumps({'schema':{},'_origin':{'eligible_for_learning':False},'decision':'以后保留新纠正'}),
            '```xml\n<system-reminder>这是需要讨论的代码示例</system-reminder>\n```\n以后保留这个示例。',
        ]
        for text in samples:
            projected=project_candidate({'user':text,'final':'确认新决定','kind':'codex','_origin':{'eligible_for_learning':False}})
            self.assertEqual(projected['user'],text)
            self.assertTrue(projected['_origin']['eligible_for_learning'])

    def test_native_meta_and_checkpoint_replays_stay_evidence(self):
        result=self.run_memory('''checkpoint='---\\ntype: codex_session\\nagent: codex\\ntitle: Codex checkpoint - previous\\n---\\n旧决定以后始终使用该方案，因为已经验证。'
records=turn('t2','这是压缩后自动生成的旧约定，以后始终采用此方案。')
records[0]['payload']['isCompactSummary']=True
append([meta,*turn('t1',checkpoint),*records]);store.ingest(source,'codex')
print(json.dumps({'states':[r[0] for r in store.db.execute('SELECT state FROM jobs')],'raw':store.db.execute("SELECT count(*) FROM normalized_events WHERE role='user'").fetchone()[0]}))
''')
        self.assertEqual(result['states'],['filtered','filtered'])
        self.assertEqual(result['raw'],2)

    def test_tool_hook_after_listener_is_delegated_and_replay_is_single_effect(self):
        result=self.run_memory('''from capture_policy import delegate_hook
append([meta,*turn('t1','以后请始终保留原始来源引用，这是我明确确认的项目偏好。')]);store.ingest(source,'codex')
with store.db:
    store.db.execute('BEGIN IMMEDIATE')
    first=delegate_hook(store.db,str(workspace),'codex','session1','PostToolUse','call1')
    second=delegate_hook(store.db,str(workspace),'codex','session1','PostToolUse','call1')
store.backfill(source,'codex')
print(json.dumps({'jobs':store.db.execute('SELECT count(*) FROM jobs').fetchone()[0],'owners':store.db.execute('SELECT count(*) FROM capture_owners').fetchone()[0],'delegated':first,'receipt':dict(store.db.execute('SELECT * FROM capture_receipts').fetchone())}))
''')
        self.assertEqual(result['jobs'],1)
        self.assertEqual(result['owners'],1)
        self.assertEqual(result['receipt']['deliveries'],2)
        self.assertEqual(result['delegated']['capture_owner'],'existing_jsonl_listener')

    def test_generated_fact_and_page_have_recoverable_source_chain(self):
        result=self.run_memory('''from memory_runtime import MemoryRuntime
from memory_integration import capture_claims
vault=home/'vault';vault.mkdir();memory=MemoryRuntime(vault,home/'facts.sqlite',use_models=False)
quote='以后请始终保留原始来源引用，这是我确认的新项目偏好。'
note={'title':'来源引用偏好','kind':'preference','scope':'项目','lesson':quote,'limitations':'只对本项目有效','evidence':[{'source':'user','quote':quote}]}
payload={'user':quote,'final':'已记录','cwd':str(workspace),'occurred_at':'2026-10-23T06:00:00Z','_origin':{'source_event':'completed-1','derived_from':['raw-user-1'],'history_origin':'mixed_current_and_history'}}
saved=capture_claims(memory,'completed-1',payload,[note])
from memory_runtime import parse_note
fact=parse_note(Path(saved['source']),vault)['meta']
pages=[parse_note(p,vault)['meta'] for p in (vault/'engineering/knowledge-pages').glob('*.md')]
print(json.dumps({'fact':fact,'pages':pages}));memory.close()
''')
        self.assertIn('raw-user-1',result['fact'].get('derived_from',[]))
        self.assertEqual(result['fact'].get('source_event'),'completed-1')
        self.assertTrue(result['pages'])
        self.assertTrue(result['pages'][0].get('derived_from'))
        self.assertEqual(result['pages'][0].get('history_origin'),'managed_memory_projection')

    def test_native_packet_exposes_source_chain_to_learning_filter(self):
        with tempfile.TemporaryDirectory(prefix='rail-origin-envelope-') as temp:
            root=Path(temp);workspace=root/'workspace';workspace.mkdir();bundle=root/'bundle';bundle.mkdir()
            (workspace/'.contextrail.toml').write_text('[project]\ntask="origin-demo"\n',encoding='utf-8')
            statement='保留原始证据和当前用户纠正。'
            (bundle/'user.md').write_text(statement,encoding='utf-8')
            ledger={'schema':1,'task_id':'origin-demo','snapshot_id':'origin-demo/rev1','requirements':[{'id':'R001','revision':1,'status':'active','kind':'goal','scope':'global','always_load':True,'statement':statement,'source':{'path':'user.md','quote':statement,'locator':'user'},'acceptance':'可读取','progress':'pending','evidence':[]}]}
            (bundle/'requirements.json').write_text(json.dumps(ledger,ensure_ascii=False),encoding='utf-8')
            def cli(command,*args,input=None):
                child=subprocess.run([sys.executable,'-m','contextrail',command,'--workspace',str(workspace),*map(str,args)],input=input,capture_output=True,timeout=40)
                self.assertEqual(child.returncode,0,child.stderr.decode('utf-8','replace'));return json.loads(child.stdout)
            cli('project-init','--session','owner','--objective','source chain')
            exported=cli('bridge-import','--bundle',bundle,'--scope','global','--session','owner','--epoch','0')
            layout=cli('client-prepare','--session','owner','--epoch','0','--snapshot',exported['snapshot'],'--output',root/'client')
            hook=cli('host-hook','--client','claude','--registry',layout['registry'],input=json.dumps({'hook_event_name':'UserPromptSubmit','session_id':'native1','cwd':str(workspace),'prompt':'继续当前任务','turn_id':'t1'}).encode())
            packet=json.loads(hook['hookSpecificOutput']['additionalContext'])
            self.assertEqual(packet['origin'].get('history_origin'),'native_context')
            self.assertIn(packet['task']['origin']['source_event'],packet['origin']['derived_from'])
            self.assertLessEqual(len(hook['hookSpecificOutput']['additionalContext'].encode('utf-16-le'))//2,10000)
            from contextrail.learning_origin import project_text
            self.assertEqual(project_text(json.dumps(packet))['text'],'')


if __name__=='__main__':unittest.main()
