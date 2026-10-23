"""E05/E06/I02 frozen consumer contract, accepted as one phase."""
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from contextrail import Lease, Ref, Store, Target
from contextrail.bridge import BridgeEvidence
from contextrail.errors import RailError, InvalidRequest, BudgetExceeded
from contextrail.models import canonical
from contextrail.project import load_project


class PreciseResumeStage(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='rail-precise-resume-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'.contextrail.toml').write_text('[project]\ntask="resume-stage"\n',encoding='utf-8')
        self.config=load_project(self.root);self.scope=self.config.scope;self.config.store.parent.mkdir()
        self.store=Store(self.config.store);self.addCleanup(self.store.close)
        self.owner=self.store.create_task(self.scope,Target('host','local','external'),'initial placeholder',allowed_providers=('local',))
        self.bundle=self.root/'bundle';self.bundle.mkdir()
        self.goal='按 TODO 持续完善自己的 demo。';self.old='这一阶段先输出报告。';self.latest='改为交付源码模块，集中验收后上传仓库。'
        self.raw=(self.goal+'\r\n'+self.old+'\r\n'+('中间的材料🙂\r\n'*3000)+self.latest+'\r\n').encode()
        (self.bundle/'user.md').write_bytes(self.raw)
        rows=[]
        for rid,text,kind,status in [('R001',self.goal,'goal','active'),('R002',self.old,'deliverable','superseded'),('R003',self.latest,'deliverable','active')]:
            row={'id':rid,'revision':1,'statement':text,'kind':kind,'status':status,'scope':'implementation','always_load':True,
                 'source':{'path':'user.md','quote':text,'locator':'explicit user request'},'acceptance':'遵循当前有效交付形式','progress':'pending','evidence':[]}
            if status=='superseded':row['superseded_by']='R003'
            rows.append(row)
        def anchor(rid,text):
            start=self.raw.index(text.encode());return {'requirement_id':rid,'start':start,'end':start+len(text.encode())}
        self.ledger={'schema':1,'task_id':self.scope.task,'snapshot_id':self.scope.task+'/rev-1','requirements':rows,
            'resume_intent':{'original_goal':anchor('R001',self.goal),'latest_request':anchor('R003',self.latest),'latest_correction':anchor('R003',self.latest)}}

    def export(self):
        (self.bundle/'requirements.json').write_text(canonical(self.ledger),encoding='utf-8')
        self.bridge=BridgeEvidence(self.store,self.scope,'host')
        result=self.bridge.import_bundle(self.bundle,'implementation',self.owner)
        self.sid=result['snapshot'];self.version=self.store.task(self.scope)['version'];return result

    def intent(self):
        self.export();result=self.bridge.packet(self.sid,budget=16000)
        self.assertEqual(result.get('resume_intent',{}).get('effective_request'),self.latest,'recovery lost the late delivery correction')
        return result

    def recovery(self,session='host'):
        from contextrail.recovery import Recovery
        return Recovery(self.store,self.config,session)

    def offer(self):
        self.intent();return self.recovery().offer(self.sid,self.version,self.owner,Target('receiver','local','external'),operation_id='offer-1',budget=16000)

    def receipt(self,offer):
        return {'offer_id':offer['offer_id'],'snapshot':offer['snapshot'],'version':offer['version'],'target_session':'receiver','delivered':True,
                'checks':[dict(item,interpretation='遵循对应原文及当前纠正',next_action=self.latest) for item in offer['required']]}

    def test_long_tail_start_and_required_quote_are_exactly_recoverable(self):
        packet=self.intent();intent=packet['resume_intent']
        self.assertEqual(intent['next_action'],self.latest)
        self.assertEqual(intent['anchors']['original_goal']['quote'],self.goal)
        for entry in intent['anchors'].values():
            self.assertEqual(self.store.get(self.scope,Ref(**entry['ref'])).content.decode(),entry['quote'])
        loaded=packet['context']['evidence']+packet['context'].get('stable_evidence',[])
        self.assertTrue(any(row.get('text')==self.latest for row in loaded))
        start=self.store.get(self.scope,Ref(**intent['start_ref'])).content.decode()
        self.assertIn(self.latest,start);self.assertIn('R002',start)
        self.assertEqual(intent['coverage'],'declared_anchors_only_not_all_user_history')

    def test_wrong_quote_range_utf8_and_superseded_current_are_rejected(self):
        self.ledger['resume_intent']['latest_correction']['start']+=1
        with self.assertRaises(InvalidRequest):self.export()

    def test_old_effective_request_cannot_replace_active_correction(self):
        self.ledger['resume_intent']['latest_request']={'requirement_id':'R002','start':len((self.goal+'\r\n').encode()),'end':len((self.goal+'\r\n'+self.old).encode())}
        with self.assertRaises(InvalidRequest):self.export()

    def test_exact_scope_version_and_missing_cursor_return_candidates_without_fallback(self):
        self.intent();r=self.recovery();from contextrail import Scope
        other=Scope(self.scope.tenant,self.scope.project,self.scope.branch,'other-task')
        lease=self.store.create_task(other,Target('other-owner','local','external'),'other task',allowed_providers=('local',))
        foreign=self.store.snapshot(other,lease,())
        result=r.candidates(snapshot=foreign)
        self.assertEqual(result['status'],'not_found');self.assertIsNone(result['selected']);self.assertFalse(result['fallback_used'])
        self.assertTrue(all(x['scope']['task']==self.scope.task for x in result['candidates']))
        self.assertNotIn('other task',canonical(result))
        with self.assertRaises(RailError):r.preview(self.sid,self.version+1)
        missing=r.candidates();self.assertEqual(missing['status'],'selection_required');self.assertIsNone(missing['selected'])

    def test_native_binding_is_precise_idempotent_and_missing_session_stays_unknown(self):
        self.intent();r=self.recovery()
        a=r.bind(self.sid,self.version,self.owner,client='codex',native_session='native-a',binding_id='bind-a')
        self.assertEqual(a,r.bind(self.sid,self.version,self.owner,client='codex',native_session='native-a',binding_id='bind-a'))
        with self.assertRaises(RailError):r.bind(self.sid,self.version,self.owner,client='codex',native_session='native-b',binding_id='bind-a')
        found=r.candidates(snapshot=self.sid,version=self.version,client='codex',native_session='native-a')
        self.assertEqual(found['status'],'matched');self.assertEqual(found['selected']['snapshot'],self.sid)
        missing=r.candidates(client='codex',native_session='unmapped')
        self.assertEqual(missing['status'],'not_found');self.assertIsNone(missing['selected'])

    def test_missing_version_shows_scope_alternatives_without_selecting_one(self):
        self.intent();result=self.recovery().candidates(snapshot=self.sid,version=self.version+100)
        self.assertEqual(result['status'],'not_found');self.assertIsNone(result['selected']);self.assertFalse(result['fallback_used'])
        self.assertEqual([r['snapshot'] for r in result['candidates']],[self.sid])
        self.assertFalse(result['candidates'][0]['matches_requested_identity'])

    def test_preview_does_not_consume_and_accept_retry_has_one_ownership_effect(self):
        offer=self.offer();r=self.recovery();before=self.store.task(self.scope)
        for _ in range(2):
            r.candidates();preview=r.preview(self.sid,self.version,budget=16000)
            self.assertEqual(preview['resume_intent']['next_action'],self.latest)
        self.assertEqual(self.store.task(self.scope),before)
        receiver=self.recovery('receiver');receipt=self.receipt(offer)
        a=receiver.accept('offer-1',receipt);b=receiver.accept('offer-1',receipt)
        self.assertEqual(a,b);self.assertEqual(a['lease'],{'session':'receiver','epoch':1})
        self.assertEqual(sum(e['kind']=='handoff.activated' for e in self.store.events(self.scope)),1)
        self.assertEqual(self.store.task(self.scope)['owner'],'receiver')
        self.assertEqual(r.offer(self.sid,self.version,self.owner,Target('receiver','local','external'),operation_id='offer-1',budget=16000),offer)
        with self.assertRaises(RailError):self.store.put(self.scope,self.owner,'stale-writer',b'no',expected_revision=0)

    def test_delivery_wrong_target_and_incomplete_checks_do_not_activate(self):
        offer=self.offer();receiver=self.recovery('receiver');proof=self.receipt(offer)
        proof['delivered']=False
        with self.assertRaises(RailError):receiver.accept('offer-1',proof)
        proof=self.receipt(offer);proof['checks'].pop()
        with self.assertRaises(RailError):receiver.accept('offer-1',proof)
        with self.assertRaises(RailError):self.recovery().accept('offer-1',self.receipt(offer))
        self.assertEqual(self.store.task(self.scope)['owner'],'host');self.assertEqual(self.store.handoff(self.scope,offer['handoff']).phase,'prepared')

    def test_interrupted_delivery_continues_original_offer_after_reopen(self):
        offer=self.offer();receiver=self.recovery('receiver');receipt=self.receipt(offer)
        validate=self.store.validate
        def interrupted(*args):raise Conflict('simulated interruption after delivery')
        from contextrail.errors import Conflict
        self.store.validate=interrupted
        try:
            with self.assertRaises(RailError):receiver.accept('offer-1',receipt)
        finally:self.store.validate=validate
        with Store(self.config.store) as reopened:
            from contextrail.recovery import Recovery
            result=Recovery(reopened,self.config,'receiver').accept('offer-1',receipt)
            self.assertEqual(result['lease'],{'session':'receiver','epoch':1})
            self.assertEqual(sum(e['kind']=='handoff.activated' for e in reopened.events(self.scope)),1)

    def test_concurrent_same_receiver_claims_only_once(self):
        from concurrent.futures import ThreadPoolExecutor
        offer=self.offer();proof=self.receipt(offer)
        def accept(_):
            from contextrail.recovery import Recovery
            with Store(self.config.store) as reopened:return Recovery(reopened,self.config,'receiver').accept('offer-1',proof)
        with ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(accept,range(6)))
        self.assertTrue(all(r==results[0] for r in results));self.assertEqual(results[0]['lease']['epoch'],1)
        self.assertEqual(sum(e['kind']=='handoff.activated' for e in self.store.events(self.scope)),1)

    def test_late_delivery_ack_after_other_retry_activated_returns_original_lease(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        offer=self.offer();proof=self.receipt(offer);ready=threading.Barrier(2);activated=threading.Event()
        def accept(index):
            from contextrail.recovery import Recovery
            with Store(self.config.store) as db:
                original_ack=db.acknowledge;original_activate=db.activate
                def ack(*args):
                    ready.wait(timeout=10)
                    if index==1 and not activated.wait(timeout=10):raise RuntimeError('first actual activation did not finish')
                    return original_ack(*args)
                def activate(*args):
                    value=original_activate(*args)
                    if index==0:activated.set()
                    return value
                db.acknowledge=ack;db.activate=activate
                return Recovery(db,self.config,'receiver').accept('offer-1',proof)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(accept,range(2)))
        self.assertEqual(results[0],results[1]);self.assertEqual(results[0]['lease']['epoch'],1)

    def test_changed_source_or_reused_operation_is_rejected_without_substitution(self):
        offer=self.offer();r=self.recovery()
        with self.assertRaises(RailError):r.offer(self.sid,self.version,self.owner,Target('receiver','local','external'),operation_id='offer-1',budget=15000)
        (self.bundle/'user.md').write_bytes(self.raw+b'changed')
        with self.assertRaises(RailError):self.recovery('receiver').accept('offer-1',self.receipt(offer))
        self.assertEqual(self.store.task(self.scope)['owner'],'host')
        with self.assertRaises(RailError):r.preview(self.sid,self.version,budget=16000)

    def test_budget_refuses_to_drop_tail_and_legacy_intent_is_unknown(self):
        self.intent()
        with self.assertRaises(BudgetExceeded):self.bridge.packet(self.sid,budget=800)
        self.ledger.pop('resume_intent');self.ledger['snapshot_id']=self.scope.task+'/rev-2';self.export()
        result=self.bridge.packet(self.sid,budget=16000)
        self.assertEqual(result['resume_intent']['status'],'not_declared');self.assertIsNone(result['resume_intent']['next_action'])

    def test_cli_preview_and_real_mcp_required_tail(self):
        self.intent()
        result=subprocess.run([sys.executable,'-m','contextrail','recover','--workspace',str(self.root),'--session','host','--snapshot',self.sid,'--version',str(self.version),'--budget','16000'],capture_output=True,timeout=40)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'));self.assertEqual(json.loads(result.stdout)['resume_intent']['next_action'],self.latest)
        async def read():
            from mcp import ClientSession,StdioServerParameters
            from mcp.client.stdio import stdio_client
            async with stdio_client(StdioServerParameters(command=sys.executable,args=['-m','contextrail','mcp','--workspace',str(self.root),'--session','host'])) as (rd,wr):
                async with ClientSession(rd,wr) as client:
                    await client.initialize();out=await client.call_tool('context.brief',{'snapshot':self.sid,'budget':16000})
                    self.assertFalse(out.isError,out.content);payload=json.loads(out.content[0].text)
                    ref=self.bridge.packet(self.sid,budget=16000)['resume_intent']['anchors']['latest_correction']['ref']
                    page=await client.call_tool('context.get',ref);self.assertFalse(page.isError);self.assertEqual(json.loads(page.content[0].text)['content'],self.latest)
                    self.assertIn(self.latest,canonical(payload))
        asyncio.run(read())

    def test_native_user_projection_preserves_tail_and_rejects_assistant_and_wrong_session(self):
        self.intent()
        from contextrail.intent import project_native_user
        app=Path.home()/'.codex/services/engineering-memory/app';overlay=Path(__file__).resolve().parents[1]/'integrations/engineering_memory';home=self.root/'memory';home.mkdir()
        source=self.root/'native.jsonl'
        source.write_text('\n'.join(canonical(x) for x in [
            {'type':'session_meta','payload':{'id':'native-session','cwd':str(self.root)}},
            {'type':'response_item','payload':{'type':'message','id':'u1','role':'user','content':[{'type':'input_text','text':self.raw.decode()}]}},
            {'type':'response_item','payload':{'type':'message','id':'a1','role':'assistant','phase':'final_answer','content':[{'type':'output_text','text':'不是用户要求'}]}}
        ])+'\n',encoding='utf-8')
        script='import sys,json;from pathlib import Path;sys.path[:0]=sys.argv[1:3];from listener import Store;s=Store(Path(sys.argv[3]));s.ingest(Path(sys.argv[4]),"codex");from native_event_stream import list_events;p=list_events(s.db,s.event_workspace(sys.argv[5]),"codex","native-session",budget=65536);print(json.dumps(p));s.db.close()'
        result=subprocess.run([sys.executable,'-c',script,str(overlay),str(app),str(home),str(source),str(self.root)],capture_output=True,timeout=45)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'));events=json.loads(result.stdout)['entries']
        user=next(e for e in events if e['role']=='user');assistant=next(e for e in events if e['role']=='assistant')
        start=self.raw.index(self.latest.encode());projection=project_native_user(home,self.root,'codex','native-session',user['id'],start=start,end=start+len(self.latest.encode()))
        self.assertEqual(projection['text'],self.latest);self.assertEqual(projection['source']['event_id'],user['id']);self.assertGreater(projection['loss']['omitted'],0)
        with self.assertRaises(RailError):project_native_user(home,self.root,'codex','native-session',assistant['id'])
        with self.assertRaises(RailError):project_native_user(home,self.root,'codex','wrong-session',user['id'])

    def test_claude_inline_recovery_keeps_anchors_within_native_character_limit(self):
        self.intent();from contextrail.native_clients import host_hook
        registry=self.root/'registry.json'
        registry.write_text(canonical({'schema':1,'bindings':[{'client':'claude','workspace':str(self.root),'store':str(self.config.store),
            'scope':asdict(self.scope),'session':'host','base_snapshot':self.sid,'owner':asdict(self.owner),'enabled':True,'context_bytes':20000,'hook_bytes':24576}]}),encoding='utf-8')
        result=host_hook(registry,'claude',{'hook_event_name':'UserPromptSubmit','session_id':'native','cwd':str(self.root),'prompt':self.latest},native_memory=True)
        self.assertNotEqual(result.get('decision'),'block',result)
        text=result['hookSpecificOutput']['additionalContext'];packet=json.loads(text)
        self.assertLessEqual(len(text.encode('utf-16-le'))//2,10000)
        self.assertEqual(packet['task']['resume_intent']['next_action'],self.latest)
        self.assertIn(self.latest,canonical(packet['task']['context']))

    def test_new_recovery_management_projection_uses_original_learning_owner(self):
        self.intent();body=self.recovery().candidates()
        body_with_reply={**body,'new_user_response':'新纠正仍应保留'}
        script='import json,sys;sys.path.insert(0,sys.argv[1]);from learning_origin import project_candidate;items=json.load(sys.stdin);print(json.dumps([project_candidate({"kind":"document","final":json.dumps(x,ensure_ascii=False)}) for x in items]))'
        app=Path.home()/'.codex/services/engineering-memory/app'
        child=subprocess.run([sys.executable,'-X','utf8','-c',script,str(app)],input=canonical([body,body_with_reply]).encode(),capture_output=True,timeout=30)
        self.assertEqual(child.returncode,0,child.stderr.decode('utf-8','replace'));pure,mixed=json.loads(child.stdout)
        self.assertFalse(pure['_origin']['eligible_for_learning'],'a recovery candidate inventory became learning text')
        self.assertEqual(pure['final'],'');self.assertTrue(mixed['_origin']['eligible_for_learning']);self.assertEqual(mixed['final'],'新纠正仍应保留')


if __name__=='__main__':unittest.main()
