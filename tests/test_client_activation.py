"""Only the newly requested legacy migration and fresh native-session behavior."""
import copy
import json
from pathlib import Path
import shutil
import os
import tempfile
import sqlite3
from contextlib import closing
import unittest
import sys

from contextrail.client_config import change_clients, RECEIPT
from contextrail.errors import Conflict
from contextrail.native_assistance import assist
from tests.test_claude_mem_compact import CompactAssistance


class ClientActivation(unittest.TestCase):
    def setUp(self):
        self.f = CompactAssistance()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.layout = Path(self.f.prepare()['registry']).parent

    def legacy(self):
        old = self.f.root/'old-generated-layout'
        shutil.copytree(self.layout, old)
        for client, hookfile, serverfile in (
            ('codex','codex.hooks.json','codex.config.toml'),
            ('claude','claude.settings.json','claude.mcp.json')):
            hooks=json.loads((old/hookfile).read_bytes())
            hooks['hooks']={'UserPromptSubmit':hooks['hooks']['UserPromptSubmit']}
            (old/hookfile).write_text(json.dumps(hooks),encoding='utf-8')
            hp=self.f.workspace/('.codex/hooks.json' if client=='codex' else '.claude/settings.local.json')
            hp.parent.mkdir(parents=True,exist_ok=True)
            hooks['hooks']['UserPromptSubmit'].append({'hooks':[{'type':'command','command':'user-memory-and-audit'}]})
            hp.write_text(json.dumps(hooks),encoding='utf-8')
            sp=self.f.workspace/('.codex/config.toml' if client=='codex' else '.mcp.json')
            content=(old/serverfile).read_text(encoding='utf-8')
            if client=='codex':content='# preserved user header\n'+content
            else:
                value=json.loads(content);value['mcpServers']['user-search']={'command':'keep-search'}
                content=json.dumps(value)
            sp.write_text(content,encoding='utf-8')
        return old

    def test_explicit_legacy_adoption_upgrades_once_and_removes_only_its_entries(self):
        old=self.legacy()
        result=change_clients(self.f.workspace,('codex','claude'),layout=self.layout,adopt_layout=old)
        self.assertTrue(result['applied'])
        again=change_clients(self.f.workspace,('codex','claude'),layout=self.layout,adopt_layout=old)
        self.assertEqual(again['applied'],[])
        change_clients(self.f.workspace,('codex','claude'))
        for client,hpath in (('codex','.codex/hooks.json'),('claude','.claude/settings.local.json')):
            hooks=json.loads((self.f.workspace/hpath).read_bytes())['hooks']
            self.assertEqual(hooks['UserPromptSubmit'],[{'hooks':[{'type':'command','command':'user-memory-and-audit'}]}])
            self.assertTrue(all(not groups for event,groups in hooks.items() if event!='UserPromptSubmit'))
        self.assertEqual((self.f.workspace/'.codex/config.toml').read_text(encoding='utf-8'),'# preserved user header\n')
        self.assertEqual(json.loads((self.f.workspace/'.mcp.json').read_bytes()),{'mcpServers':{'user-search':{'command':'keep-search'}}})

    def test_changed_legacy_hook_aborts_whole_migration(self):
        old=self.legacy()
        changed=self.f.workspace/'.claude/settings.local.json'
        value=json.loads(changed.read_bytes());value['hooks']['UserPromptSubmit'][0]['hooks'][0]['timeout']=999
        changed.write_text(json.dumps(value),encoding='utf-8')
        paths=[self.f.workspace/p for p in ('.codex/config.toml','.codex/hooks.json','.mcp.json','.claude/settings.local.json')]
        before=[p.read_bytes() for p in paths]
        with self.assertRaises(Conflict):
            change_clients(self.f.workspace,('codex','claude'),layout=self.layout,adopt_layout=old)
        self.assertEqual([p.read_bytes() for p in paths],before)
        self.assertFalse((self.f.workspace/RECEIPT).exists())

    @unittest.skipUnless(os.name=='nt','Windows drive-root migration contract')
    def test_project_directly_under_drive_root_migrates_both_clients(self):
        direct=tempfile.TemporaryDirectory(dir=Path.cwd().anchor,prefix='rail activation ')
        self.addCleanup(direct.cleanup)
        self.f.workspace=Path(direct.name)
        registry=json.loads(self.f.registry.read_bytes())
        for b in registry['bindings']:b['workspace']=str(self.f.workspace)
        self.f.registry.write_text(json.dumps(registry),encoding='utf-8')
        old=self.legacy()
        change_clients(self.f.workspace,('codex','claude'),layout=self.layout,adopt_layout=old)
        change_clients(self.f.workspace,('codex','claude'))
        self.assertEqual(json.loads((self.f.workspace/'.mcp.json').read_bytes()),{'mcpServers':{'user-search':{'command':'keep-search'}}})

    def test_current_hook_binding_accepts_exact_fresh_root_session_without_static_rewrite(self):
        registry=json.loads(self.f.registry.read_bytes())
        for b in registry['bindings']:b['native_session_binding']='current_hook'
        self.f.registry.write_text(json.dumps(registry),encoding='utf-8')
        before=self.f.query.read_bytes()
        for client in ('codex','claude'):
            payload={'hook_event_name':'PostCompact','session_id':'fresh-'+client,'cwd':str(self.f.workspace)}
            marked=assist(self.f.registry,client,payload)
            self.assertEqual(marked,{'suppressOutput':True})
            # Child session cannot consume or mark a different root task.
            child=assist(self.f.registry,client,{**payload,'session_id':'child-'+client,'agent_id':'other-parent'})
            self.assertNotEqual(child,{'suppressOutput':True})
        self.assertEqual(self.f.query.read_bytes(),before)

    def test_current_session_reads_do_not_feed_back_into_automatic_file_history(self):
        from contextrail import source_events,timeline_index
        path=self.f.workspace/'notes.md';path.write_text('current file',encoding='utf-8')
        registry=json.loads(self.f.registry.read_bytes())
        for b in registry['bindings']:b['native_session_binding']='current_hook'
        self.f.registry.write_text(json.dumps(registry),encoding='utf-8')
        def captured(session,marker):
            with closing(sqlite3.connect(self.f.home/'state.sqlite')) as db:
                db.row_factory=sqlite3.Row
                db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,source TEXT,payload TEXT,state TEXT)')
                db.executescript(source_events.SCHEMA+timeline_index.SCHEMA)
                db.execute('BEGIN')
                identity=source_events.source_identity('codex/tool.call',str(self.f.workspace),session,marker,'actual-reader.jsonl',0)
                data={'original_tool':'Read','arguments':{'file_path':str(path)},'note':'previous file work'}
                source_events.admit_event(db,identity,{'kind':'native-evidence','cwd':str(self.f.workspace),'final':data},'actual-reader.jsonl',container=True)
                event={'id':identity['id'],'workspace':source_events.workspace_key(self.f.workspace),'client':'codex','native_session':session,
                    'role':'tool','event_type':'tool.call','observed_at':100,'occurred_at':None,'data':data}
                db.execute('INSERT INTO normalized_events(event_id,workspace,client,native_session,role,event_type,metadata) VALUES(?,?,?,?,?,?,?)',
                    (event['id'],event['workspace'],'codex',session,'tool','tool.call',json.dumps(event)))
                timeline_index.index_native(db,event);db.commit()
        captured('codex-native','historical-work')
        captured('fresh-codex','current-read-1')
        payload={'hook_event_name':'PreToolUse','cwd':str(self.f.workspace),'session_id':'fresh-codex',
            'tool_name':'Read','tool_input':{'file_path':str(path)}}
        first=assist(self.f.registry,'codex',payload)
        entries=json.loads(first['hookSpecificOutput']['additionalContext'])['entries']
        self.assertTrue(entries)
        self.assertEqual({r['native_session'] for r in entries},{'codex-native'})
        captured('fresh-codex','current-read-2')
        second=assist(self.f.registry,'codex',payload)
        self.assertNotIn('hookSpecificOutput',second)
        command='Get-Content -LiteralPath '+"'"+str(path)+"' -TotalCount 4"
        code='text(await tools.exec_command({cmd:'+json.dumps(command)+',shell:"powershell",max_output_tokens:2000}));'
        wrapped={**payload,'session_id':'fresh-code-mode','tool_name':'exec','tool_input':{'input':code}}
        native=assist(self.f.registry,'codex',wrapped)
        self.assertTrue(native.get('hookSpecificOutput'),native)
        self.assertNotIn('hookSpecificOutput',assist(self.f.registry,'codex',wrapped))
        dynamic={**wrapped,'session_id':'other-code-mode','tool_input':{'input':'text(await tools.exec_command({cmd: computePath()}));'}}
        self.assertNotIn('hookSpecificOutput',assist(self.f.registry,'codex',dynamic))

    def test_recovery_metadata_shares_the_native_budget_before_optional_selection(self):
        from contextrail.models import canonical
        original=self.f.event('codex','UserPromptSubmit',prompt='current activation')
        registry=json.loads(self.f.registry.read_bytes())
        budget=len(canonical(original).encode('utf-8'))+20
        for b in registry['bindings']:b['hook_bytes']=budget
        self.f.registry.write_text(json.dumps(registry),encoding='utf-8')
        self.f.event('codex','PostCompact')
        self.f.capture_compact('codex','actual-original-compaction')
        recovered=self.f.event('codex','SessionStart',source='compact')
        text=recovered.get('hookSpecificOutput',{}).get('additionalContext','')
        self.assertTrue(text,recovered)
        value=json.loads(text)
        self.assertEqual(value['recovery']['source_event'],'actual-original-compaction')
        self.assertIn(self.f.quote,text)
        self.assertLessEqual(len(canonical(recovered).encode('utf-8')),budget)
        followup=self.f.event('codex','UserPromptSubmit',prompt='continue the same recovered task')
        self.assertNotIn('hookSpecificOutput',followup)

    def test_same_returned_task_is_not_reinjected_and_original_memory_runs_once_per_prompt(self):
        calls=self.f.root/'memory-calls.txt'
        registry=json.loads(self.f.registry.read_bytes())
        registry['memory']={'command':[sys.executable,'-c',
            'import json,sys;from pathlib import Path;p=Path(sys.argv[1]);p.write_text(p.read_text()+"call\\n" if p.exists() else "call\\n");print(json.dumps({"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"original memory"}}))',str(calls)],'timeout_seconds':5}
        self.f.registry.write_text(json.dumps(registry),encoding='utf-8')
        payload={'hook_event_name':'UserPromptSubmit','session_id':'codex-native','cwd':str(self.f.workspace),'prompt':'continue'}
        first=self.f.cli('host-hook','--registry',self.f.registry,'--client','codex',input=payload)
        self.assertEqual(json.loads(first['hookSpecificOutput']['additionalContext'])['task']['task_id'],'compact-task')
        second=self.f.cli('host-hook','--registry',self.f.registry,'--client','codex',input=payload)
        self.assertEqual(second['hookSpecificOutput']['additionalContext'],'original memory')
        self.assertEqual(calls.read_text().splitlines(),['call','call'])


if __name__=='__main__':unittest.main()
