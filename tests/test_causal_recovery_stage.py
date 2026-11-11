"""E04/E07 current-change consumers; run together at the module boundary."""
from contextlib import redirect_stdout
from dataclasses import asdict
import base64
import io
import json
from pathlib import Path
import tempfile
import unittest

from contextrail import Store, Scope, Target
from contextrail.errors import RailError
from contextrail.host import ContextTools
from contextrail.models import canonical
from contextrail.tool_results import OutputRecorder


class CausalRecoveryStage(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='rail-recovery-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.scope=Scope('local','p','w','recovery')
        self.dbpath=self.root/'rail.sqlite';self.store=Store(self.dbpath);self.addCleanup(lambda:self.store.close())
        self.lease=self.store.create_task(self.scope,Target('host','local','external'),'Recover interrupted work',allowed_providers=('local',))
        self.source=self.store.put(self.scope,self.lease,'request','修复构建失败并验证'.encode(),expected_revision=0)
        self.home=self.root/'memory';self.home.mkdir();self.logs=self.root/'logs';self.logs.mkdir()
        (self.home/'config.json').write_text(json.dumps({'sources':[{'enabled':True,'kind':c,'path':str(self.logs)} for c in ('codex','claude')]}))

    def record(self,call,code=0,text='done',partial=False,timeout=False,session=None):
        lease=session or self.lease
        return OutputRecorder(self.store,self.scope,lease,self.root).capture({'call_id':call,'tool':'exec_command','argv':['python','build.py'],'cwd':str(self.root),'started_at':1,'finished_at':2,'exit_code':code,'partial':partial,'timed_out':timeout,'stdout':text,'stderr':'','source_paths':['build.py']},budget=5000)

    def repairs(self):
        from contextrail.error_history import ErrorHistory
        return ErrorHistory(self.store,self.scope,self.lease,self.root)

    def read_errors(self,**kw):
        return ContextTools(self.store,self.scope,'host').call('context.error_history',{'budget':24000,**kw})

    def codex(self,kind,data,**extra):return {'type':'response_item','payload':{'type':kind,**data,**extra}}
    def user(self,text):return self.codex('message',{'role':'user','content':[{'type':'input_text','text':text}]})
    def call(self,identity='c1',name='exec_command',args=None):return self.codex('function_call',{'call_id':identity,'name':name,'arguments':json.dumps(args or {'cmd':'python build.py'})})
    def result(self,identity='c1',code=0,text='built'):return self.codex('function_call_output',{'call_id':identity,'output':json.dumps({'exit_code':code,'output':text})})
    def log(self,rows=None,session='exact',client='codex',cwd=None):
        path=self.logs/(session+'.jsonl')
        if client=='codex':rows=[{'type':'session_meta','payload':{'id':session,'cwd':str(cwd or self.root)}}]+(rows or [])
        else:rows=[{**r,'sessionId':session,'cwd':str(cwd or self.root)} for r in rows]
        path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8');return path

    def history(self,session='exact',client='codex',source=None):
        from contextrail.native_history import NativeHistory
        return NativeHistory(self.home,self.root,client,session,source=source)

    def test_failure_enters_workscene_with_original_evidence(self):
        self.record('failed',1,'SyntaxError: unexpected token')
        scene=ContextTools(self.store,self.scope,'host').call('context.workscene',{'budget':24000})
        errors=[r for r in scene['entries'] if r['category']=='error']
        self.assertEqual(len(errors),1,'saved failed result does not preserve an error recovery entry')
        self.assertEqual(errors[0]['payload']['error_category'],'syntax_error')
        self.assertEqual(errors[0]['sources'][0]['name'],'tool-result/failed/result')

    def test_adjacent_success_does_not_resolve_failure(self):
        self.record('broken',1,'SyntaxError: unexpected token');self.record('nearby',0)
        row=self.read_errors(category='syntax_error')['entries'][0]
        self.assertEqual(row['attempts'],[]);self.assertEqual(row['resolution'],'unconfirmed')
        self.assertTrue(row['related_results']);self.assertIn('candidate',row['related_results'][0]['basis'])

    def test_declared_attempt_uses_actual_bound_verification_and_reopens(self):
        self.record('broken',1,'SyntaxError: unexpected token');case=self.read_errors()['entries'][0]['id']
        self.repairs().attempt(case,'fix-1','编辑错误行','edit-call','verify-call',self.source)
        self.record('edit-call',0);self.assertEqual(self.read_errors()['entries'][0]['attempts'][0]['status'],'awaiting')
        self.record('verify-call',0);self.store.close();self.store=Store(self.dbpath)
        row=self.read_errors()['entries'][0]
        self.assertEqual(row['attempts'][0]['status'],'verification_passed');self.assertEqual(row['resolution'],'unconfirmed')
        self.assertEqual(row['attempts'][0]['verification']['ref']['name'],'tool-result/verify-call/result')
        before=self.store.task(self.scope)['version'];self.record('verify-call',0)
        self.assertEqual(self.store.task(self.scope)['version'],before)

    def test_partial_verification_and_retroactive_binding(self):
        self.record('broken',1,'timeout');case=self.read_errors()['entries'][0]['id']
        self.repairs().attempt(case,'fix','等待工具完成','edit','verify',self.source)
        self.record('verify',0,partial=True)
        self.assertEqual(self.read_errors()['entries'][0]['attempts'][0]['status'],'unknown')
        with self.assertRaises(RailError):self.repairs().attempt(case,'retro','补造关联','edit','verify',self.source)

    def test_interrupted_codex_recovers_last_request_result_and_pending(self):
        path=self.log([self.user('最终只输出 Markdown'),self.call(),self.result(),self.call('still-running')])
        with path.open('ab') as stream:stream.write(b'{"type":')
        result=self.history().read(budget=16000)
        self.assertEqual(result['last_request']['data']['text'],'最终只输出 Markdown')
        self.assertEqual([e['call_id'] for e in result['entries'] if e['type']=='tool.result'],['c1'])
        self.assertEqual(result['pending_calls'][0]['call_id'],'still-running')
        self.assertIn('partial_final_record',[g['reason'] for g in result['gaps']]);self.assertIsNone(result['last_final'])

    def test_claude_visible_blocks_and_secrets(self):
        secret='sk-'+'A'*40
        self.log([{'type':'user','message':{'content':[{'type':'text','text':'继续执行'}]}},
            {'type':'assistant','message':{'content':[{'type':'thinking','thinking':'HIDDEN-9834'},{'type':'tool_use','id':'c1','name':'Bash','input':{'command':'python build.py'}}]}},
            {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'c1','is_error':False,'content':'api_key='+secret}]}},
            {'type':'assistant','message':{'stop_reason':'end_turn','content':[{'type':'text','text':'已返回'}]}}],client='claude')
        result=self.history(client='claude').read(budget=16000)
        self.assertEqual(result['last_request']['data']['text'],'继续执行');self.assertEqual(result['last_final']['data']['text'],'已返回')
        self.assertNotIn(secret,canonical(result));self.assertNotIn('HIDDEN-9834',canonical(result))

    def test_capture_policy_and_injected_history_do_not_become_user_intent(self):
        (self.root/'.contextrail.toml').write_text('[capture]\nignore=["private/**"]\n')
        self.log([self.user('真正的新要求'),self.call(name='Read',args={'file_path':str(self.root/'private/secret.txt')}),self.result(text='PRIVATE-3728'),
            self.user(canonical({'schema':'contextrail.workscene.v1','entries':[],'topics':[],'jobs':[]}))])
        value=self.history().read(budget=16000)
        self.assertEqual(value['last_request']['data']['text'],'真正的新要求');self.assertNotIn('PRIVATE-3728',canonical(value))

    def test_exact_checkout_and_ambiguous_sources_are_rejected(self):
        source=self.log([self.user('wrong task')],cwd=self.root/'other')
        with self.assertRaises(RailError):self.history(source=source).read()
        source=self.log([self.user('right task')]);copy=self.logs/'archive';copy.mkdir();(copy/source.name).write_bytes(source.read_bytes())
        with self.assertRaises(RailError):self.history().read()
        self.assertEqual(self.history(source=source).read()['last_request']['data']['text'],'right task')

    def test_pagination_freezes_eof_and_get_returns_complete_processed_record(self):
        self.log([self.user('keep me'),self.call(),self.result(text='长🙂'*18000),self.user('last request')])
        history=self.history();first=history.read(limit=1,budget=5000,tail_bytes=0)
        self.assertLessEqual(len(canonical(first).encode()),5000)
        with (self.logs/'exact.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(self.user('later append'))+'\n')
        second=history.read(cursor=first['next_cursor'],limit=1,budget=5000)
        result=second['entries'][0];self.assertEqual(result['type'],'tool.result');self.assertTrue(result['omitted_body'])
        body=history.get(result['ref'],end=256,budget=5000)
        self.assertIn('长',base64.b64decode(body['base64']).decode('utf-8','ignore'));self.assertGreater(body['total'],256)
        third=history.read(cursor=second['next_cursor'],budget=5000)
        self.assertNotIn('later append',canonical(third))

    def test_replaced_source_invalidates_exact_cursor(self):
        path=self.log([self.user('one'),self.user('two')]);history=self.history();first=history.read(limit=1,tail_bytes=0)
        path.rename(path.with_suffix('.old'));self.log([self.user('replacement')])
        with self.assertRaises(RailError):history.read(cursor=first['next_cursor'])

    def test_bound_tool_disallows_source_overrides_and_exports_recovery_material(self):
        from contextrail.query_sources import QueryBinding
        self.log([self.user('恢复我的最后请求'),self.call(),self.result()])
        binding=QueryBinding(str(self.root),'recovery',self.home,(('codex','exact'),))
        tools=ContextTools(self.store,self.scope,'host',query_binding=binding)
        value=tools.call('context.native_history',{'client':'codex','native_session':'exact','budget':14000})
        self.assertEqual(value['last_request']['data']['text'],'恢复我的最后请求')
        for args in ({'source':str(self.logs/'exact.jsonl')},{'native_session':'other'}):
            with self.assertRaises(RailError):tools.call('context.native_history',{'client':'codex','native_session':'exact',**args})
        out=self.root/'handoff';receipt=self.history().export_state(out,budget=16000)
        from contextrail.learning_origin import project_text
        self.assertEqual(project_text(Path(receipt['state']).read_text(encoding='utf-8'))['text'],'')
        self.assertEqual(self.store.task(self.scope)['objective'],'Recover interrupted work')

    def test_cli_history_works_without_rail_database(self):
        from contextrail.cli import main
        self.log([self.user('找回最后请求')]);out=io.StringIO()
        with redirect_stdout(out):main(['native-history','--workspace',str(self.root),'--memory-home',str(self.home),'--client','codex','--native-session','exact'])
        self.assertEqual(json.loads(out.getvalue())['last_request']['data']['text'],'找回最后请求')

    def test_same_claude_record_paginates_all_visible_items(self):
        self.log([{'type':'user','message':{'content':[{'type':'text','text':'read this request'},
            {'type':'tool_result','tool_use_id':'done','content':'done result','is_error':False}]}},
            {'type':'user','message':{'content':[{'type':'text','text':'next request'}]}}],client='claude')
        reader=self.history(client='claude');first=reader.read(limit=1,tail_bytes=0)
        second=reader.read(cursor=first['next_cursor'],limit=1)
        self.assertEqual(second['entries'][0]['type'],'tool.result','second visible item in same physical row was skipped')

    def test_attempt_history_remains_readable_after_many_treatments(self):
        self.record('broken',1,'SyntaxError: unexpected token');case=self.read_errors()['entries'][0]['id']
        for n in range(8):
            self.repairs().attempt(case,'a'+str(n),'尝试修改 '+str(n),'edit'+str(n),'verify'+str(n),self.source)
            self.record('verify'+str(n),1,'SyntaxError: unexpected token')
        value=ContextTools(self.store,self.scope,'host').call('context.error_history',{'case_id':case,'attempt_limit':2,'budget':10000})
        row=value['entries'][0];self.assertEqual(row['attempt_total'],8);self.assertEqual(len(row['attempts']),2)
        later=ContextTools(self.store,self.scope,'host').call('context.error_history',{'case_id':case,'attempt_offset':row['next_attempt_offset'],'attempt_limit':2,'budget':10000})
        self.assertEqual(later['entries'][0]['attempts'][0]['id'],'a2')

    def test_native_error_pairs_exact_session_and_ignores_other_session_success(self):
        from contextrail.workscene import WorkScene
        scene=WorkScene(self.store,self.scope,self.lease,self.root)
        def ingest(identity,session,kind,data):
            event={'id':identity,'client':'codex','native_session':session,'workspace':str(self.root),'event_type':kind,'role':'tool','call_id':'same','data':data}
            ref=self.store.put(self.scope,self.lease,'native/'+identity,canonical(event).encode(),expected_revision=0)
            scene.native(event,ref)
        ingest('call-a','a','tool.call',{'tool':'Bash','arguments':{'command':'python build.py'}})
        ingest('result-a','a','tool.result',{'output':{'exit_code':1,'stdout':'SyntaxError: unexpected token'}})
        case=self.read_errors()['entries'][0]['id'];self.repairs().attempt(case,'native-fix','修复声明','edit','verify',self.source)
        ingest('call-b','b','tool.call',{'tool':'Bash','arguments':{'command':'python build.py'}})
        ingest('result-b','b','tool.result',{'output':{'exit_code':0,'stdout':'done'}})
        self.assertEqual(self.read_errors()['entries'][0]['attempts'][0]['status'],'awaiting')

    def test_old_final_does_not_hide_interruption_after_latest_request(self):
        self.log([self.user('older request'),self.codex('message',{'role':'assistant','phase':'final','content':[{'type':'output_text','text':'old answer'}]}),self.user('latest unfinished request')])
        value=self.history().read()
        self.assertIn('no_final_after_last_request',[g['reason'] for g in value['gaps']])

    def test_follow_cursor_crosses_hidden_records_on_both_sides_of_prior_eof(self):
        hidden = self.codex('reasoning', {'summary': [{'type': 'summary_text', 'text': 'hidden'}]})
        path = self.log([self.user('previous request'), hidden])
        history = self.history()
        cursor = history.read()['end_cursor']
        with path.open('a', encoding='utf-8') as stream:
            for row in (hidden, self.user('new visible request')):
                stream.write(json.dumps(row) + '\n')
        page = history.read(cursor=cursor, follow=True)
        self.assertEqual([item['data']['text'] for item in page['entries']], ['new visible request'])

    def test_model_ref_cannot_disambiguate_duplicate_native_sources(self):
        from contextrail.query_sources import QueryBinding

        original = self.log([self.user('actual original')])
        duplicate = self.logs / 'archive' / original.name
        duplicate.parent.mkdir()
        duplicate.write_bytes(original.read_bytes())
        ref = self.history(source=duplicate).read()['last_request']['ref']
        binding = QueryBinding(str(self.root), 'recovery', self.home, (('codex', 'exact'),))
        tools = ContextTools(self.store, self.scope, 'host', query_binding=binding)
        with self.assertRaises(RailError):
            tools.call('context.native_history_get', {'client': 'codex', 'native_session': 'exact', 'ref': ref})
