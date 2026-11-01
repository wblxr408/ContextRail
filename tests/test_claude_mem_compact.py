"""Current CM05 consumer behavior, real hook subprocesses and original stores."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing

from contextrail import Scope, Store, Target, Lease
from contextrail.bridge import BridgeEvidence
from contextrail.source_events import workspace_key


class CompactAssistance(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='rail compact 中文 ');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.workspace=self.root/'用户 workspace';self.workspace.mkdir()
        (self.workspace/'.contextrail.toml').write_text('[project]\ntask="compact-task"\n',encoding='utf-8')
        self.home=self.root/'memory';self.home.mkdir();self.database=self.workspace/'rail.sqlite3'
        self.bundle=self.root/'bundle';self.bundle.mkdir()
        self.quote='保留当前要求；最后纠正：不要自动发布。'
        (self.bundle/'user.md').write_text(self.quote,encoding='utf-8')
        ledger={'schema':1,'task_id':'compact-task','snapshot_id':'compact-task/revision-1','requirements':[
            {'id':'R001','revision':1,'status':'active','kind':'goal','scope':'implementation','always_load':True,
             'statement':self.quote,'source':{'path':'user.md','quote':self.quote,'locator':'latest user correction'},
             'acceptance':'回读末尾纠正','progress':'pending','evidence':[]}]}
        (self.bundle/'requirements.json').write_text(json.dumps(ledger,ensure_ascii=False),encoding='utf-8')
        init=self.cli('project-init','--session','owner','--objective','compact task')
        self.scope=Scope(**init['scope']);self.database=Path(init['store'])
        with Store(self.database) as store:
            self.exported=BridgeEvidence(store,self.scope,'owner').import_bundle(self.bundle,'implementation',Lease('owner',0))
        with closing(sqlite3.connect(self.home/'state.sqlite')) as db:
            db.execute('CREATE TABLE normalized_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE,workspace TEXT,client TEXT,native_session TEXT,role TEXT,event_type TEXT,metadata TEXT)')
            db.commit()
        self.query=self.root/'query.json'
        self.query.write_text(json.dumps({'schema':1,'workspace':str(self.workspace),'task':'compact-task','memory_home':str(self.home),
            'native_sessions':[{'client':c,'session':c+'-native'} for c in ('codex','claude')]}),encoding='utf-8')

    def run_cli(self,command,*args,input=None):
        return subprocess.run([sys.executable,'-m','contextrail',command,'--workspace',str(self.workspace),*map(str,args)],
            input=json.dumps(input,ensure_ascii=False).encode('utf-8') if input is not None else None,capture_output=True,timeout=40)

    def cli(self,command,*args,input=None):
        r=self.run_cli(command,*args,input=input)
        self.assertEqual(r.returncode,0,r.stderr.decode('utf-8','replace'))
        return json.loads(r.stdout)

    def prepare(self):
        result=self.cli('client-prepare','--session','owner','--epoch',0,'--snapshot',self.exported['snapshot'],
            '--output',self.root/'layout','--query-binding',self.query,'--context-assistance')
        self.registry=Path(result['registry'])
        return result

    def event(self,client,event,**fields):
        return self.cli('host-hook','--registry',self.registry,'--client',client,'--native-memory',input={
            'hook_event_name':event,'cwd':str(self.workspace),'session_id':client+'-native',**fields})

    def capture_compact(self,client,source):
        with closing(sqlite3.connect(self.home/'state.sqlite')) as db:
            db.execute('INSERT INTO normalized_events(event_id,workspace,client,native_session,role,event_type,metadata) VALUES(?,?,?,?,?,?,?)',
                (source,workspace_key(self.workspace),client,client+'-native','lifecycle','context.compacted',json.dumps({'id':source,'event_type':'context.compacted','observed_at':100})))
            db.commit()

    def test_next_capable_event_returns_exact_correction_once_and_new_cycle_again(self):
        layout=self.prepare()
        for client in ('codex','claude'):
            with self.subTest(client=client):
                fragment=json.loads(Path(layout['clients'][client]['hook_config']).read_text(encoding='utf-8'))
                self.assertTrue({'PostCompact','SessionStart','PostToolUse'} <= fragment['hooks'].keys())
                marked=self.event(client,'PostCompact',trigger='auto')
                self.assertNotIn('hookSpecificOutput',marked)
                self.capture_compact(client,client+'-compaction-1')
                returned=self.event(client,'SessionStart',source='compact')
                text=returned['hookSpecificOutput']['additionalContext']
                self.assertIn(self.quote,text)
                packet=json.loads(text)
                self.assertEqual(packet['origin']['event'],'SessionStart')
                self.assertEqual(packet['recovery']['source_event'],client+'-compaction-1')
                self.assertEqual(packet['recovery']['model_consumption'],'unknown')
                duplicate=self.event(client,'PostToolUse',turn_id='turn-after')
                self.assertNotIn('hookSpecificOutput',duplicate)
                self.event(client,'PostCompact',trigger='auto')
                replay=self.event(client,'PostToolUse',turn_id='turn-after')
                self.assertNotIn('hookSpecificOutput',replay)
                self.capture_compact(client,client+'-compaction-2')
                renewed=self.event(client,'PostToolUse',turn_id='next-turn')
                self.assertIn(self.quote,renewed['hookSpecificOutput']['additionalContext'])
        with Store(self.database) as store:
            kinds=[r['kind'] for r in store.events(self.scope)]
            self.assertEqual(kinds.count('native_compact.returned'),4)
            self.assertGreaterEqual(kinds.count('native_compact.pending'),4)
            self.assertEqual(store.task(self.scope)['owner'],'owner')

    def test_source_lag_and_stale_required_bundle_remain_retryable(self):
        self.prepare();self.event('codex','PostCompact',trigger='manual')
        waiting=self.event('codex','PostToolUse',turn_id='lag')
        self.assertNotIn('hookSpecificOutput',waiting)
        self.assertIn('awaiting_original_compaction',waiting['systemMessage'])
        self.capture_compact('codex','cycle-stale')
        (self.bundle/'user.md').write_text('尚未导入的纠正',encoding='utf-8')
        stale=self.event('codex','PostToolUse',turn_id='stale')
        self.assertFalse(stale['continue'])
        self.assertIn('stale_snapshot',stale['stopReason'])
        with Store(self.database) as store:
            self.assertFalse(any(r['kind']=='native_compact.returned' for r in store.events(self.scope)))
        (self.bundle/'user.md').write_text(self.quote,encoding='utf-8')
        recovered=self.event('codex','PostToolUse',turn_id='retry')
        self.assertIn(self.quote,recovered['hookSpecificOutput']['additionalContext'])

    def test_new_event_installation_preserves_user_hook_and_removes_only_owned_events(self):
        self.prepare();folder=self.workspace/'.codex';folder.mkdir()
        mine={'hooks':[{'type':'command','command':'my-audit','timeout':5}]}
        (folder/'hooks.json').write_text(json.dumps({'hooks':{'PostToolUse':[mine]}}),encoding='utf-8')
        self.cli('client-apply','--layout',self.registry.parent,'--client','codex')
        configured=json.loads((folder/'hooks.json').read_text(encoding='utf-8'))
        self.assertIn('PostCompact',configured['hooks'])
        self.assertIn(mine,configured['hooks']['PostToolUse'])
        self.cli('client-remove','--client','codex')
        remaining=json.loads((folder/'hooks.json').read_text(encoding='utf-8'))
        self.assertEqual(remaining['hooks']['PostToolUse'],[mine])
        self.assertFalse(remaining['hooks'].get('PostCompact'))

    def test_prepared_process_loss_retries_after_claim_expiry_without_consumption_claim(self):
        self.prepare();self.event('codex','PostCompact',trigger='auto');self.capture_compact('codex','cycle-retry')
        from contextrail.native_clients import host_hook
        request={'hook_event_name':'PostToolUse','cwd':str(self.workspace),'session_id':'codex-native','turn_id':'prepare-only'}
        prepared=host_hook(self.registry,'codex',request,native_memory=True)
        self.assertIn(self.quote,prepared['hookSpecificOutput']['additionalContext'])
        # No stdout acknowledgement: simulate the observable durable state of a lost process.
        with Store(self.database) as store:
            self.assertFalse(any(r['kind']=='native_compact.returned' for r in store.events(self.scope)))
            store.db.execute('UPDATE native_context_deliveries SET claim_until=0');store.db.commit()
        retry=self.event('codex','PostToolUse',turn_id='retry-after-expiry')
        self.assertIn(self.quote,retry.get('hookSpecificOutput',{}).get('additionalContext',''))

    def test_budget_failure_retains_cycle_and_unbound_child_is_not_injected(self):
        self.prepare();self.event('codex','PostCompact',trigger='auto');self.capture_compact('codex','cycle-budget')
        registry=json.loads(self.registry.read_text(encoding='utf-8'))
        original=registry['bindings'][0]['context_bytes'];registry['bindings'][0]['context_bytes']=64
        self.registry.write_text(json.dumps(registry),encoding='utf-8')
        limited=self.event('codex','PostToolUse',turn_id='small')
        self.assertFalse(limited['continue']);self.assertIn('budget_exceeded',limited['stopReason'])
        registry['bindings'][0]['context_bytes']=original;self.registry.write_text(json.dumps(registry),encoding='utf-8')
        child=self.event('codex','PostToolUse',turn_id='child',agent_id='other-agent')
        self.assertNotIn('hookSpecificOutput',child)
        recovered=self.event('codex','PostToolUse',turn_id='enough')
        self.assertIn(self.quote,recovered['hookSpecificOutput']['additionalContext'])

    def test_status_exposes_flushed_compaction_receipt_without_claiming_consumption(self):
        self.prepare();self.event('codex','PostCompact',trigger='auto');self.capture_compact('codex','cycle-status')
        self.event('codex','PostToolUse',turn_id='status')
        from contextrail.host import ContextTools
        from contextrail.query_sources import QueryBinding
        with Store(self.database) as store:
            status=ContextTools(store,self.scope,'owner',query_binding=QueryBinding.load(self.query,'compact-task',self.workspace)).call('context.status',{})
        returned=status.get('native_assistance',{}).get('compact',{}).get('last_returned',{})
        self.assertEqual(returned.get('source_event'),'cycle-status')
        self.assertEqual(returned.get('model_consumption'),'unknown')

    def test_pretool_is_the_next_capable_event_and_restores_required_context_first(self):
        self.prepare();self.event('claude','PostCompact',trigger='auto');self.capture_compact('claude','cycle-pretool')
        result=self.event('claude','PreToolUse',tool_name='Read',tool_input={'file_path':str(self.workspace/'next.py')})
        self.assertIn(self.quote,result.get('hookSpecificOutput',{}).get('additionalContext',''))
        self.assertEqual(result['hookSpecificOutput']['hookEventName'],'PreToolUse')
        self.assertNotIn('permissionDecision',result['hookSpecificOutput'])


if __name__=='__main__':unittest.main()
