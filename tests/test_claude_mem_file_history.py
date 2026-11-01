"""CM06/07/09 current file history, persistence and source-navigation behavior."""
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import unittest

from . import test_claude_mem_compact as setup
from contextrail import Store
from contextrail.host import ContextTools
from contextrail.query_sources import QueryBinding
from contextrail import source_events, timeline_index


class FileHistory(unittest.TestCase):
    def setUp(self):
        self.f=setup.CompactAssistance('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups);self.f.prepare()
        self.file=self.f.workspace/'设计 notes.py';self.file.write_text('actual file content',encoding='utf-8')
        with closing(sqlite3.connect(self.f.home/'state.sqlite')) as db:
            db.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,source TEXT,payload TEXT,state TEXT)')
            db.executescript(source_events.SCHEMA+timeline_index.SCHEMA);db.commit()

    def observation(self,marker,text):
        with closing(sqlite3.connect(self.f.home/'state.sqlite')) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN')
            identity=source_events.source_identity('codex/tool.call',str(self.f.workspace),'codex-native',marker,'original.jsonl',0)
            data={'note':text,'original_tool':'Read','arguments':{'file_path':str(self.file)}}
            source_events.admit_event(db,identity,{'kind':'native-evidence','cwd':str(self.f.workspace),'final':data},'original.jsonl',container=True)
            event={'id':identity['id'],'workspace':source_events.workspace_key(self.f.workspace),'client':'codex','native_session':'codex-native',
                'role':'tool','event_type':'tool.call','observed_at':100,'occurred_at':None,'data':data}
            db.execute('INSERT INTO normalized_events(event_id,workspace,client,native_session,role,event_type,metadata) VALUES(?,?,?,?,?,?,?)',
                (event['id'],event['workspace'],'codex','codex-native','tool','tool.call',json.dumps(event)))
            timeline_index.index_native(db,event);db.commit()
            return identity['id']

    def tools(self,store):
        return ContextTools(store,self.f.scope,'owner',query_binding=QueryBinding.load(self.f.query,'compact-task',self.f.workspace))

    def test_file_history_injects_once_per_source_version_and_new_history_can_be_read_exactly(self):
        first=self.observation('source-1','上次原因：修复 UTF-8 截断')
        before=self.file.read_text(encoding='utf-8')
        result=self.f.event('claude','PreToolUse',tool_name='Read',tool_input={'file_path':str(self.file)})
        self.assertIn('修复 UTF-8',result.get('hookSpecificOutput',{}).get('additionalContext',''))
        self.assertNotIn('permissionDecision',result['hookSpecificOutput'])
        duplicate=self.f.event('claude','PreToolUse',tool_name='Read',tool_input={'file_path':str(self.file)})
        self.assertNotIn('hookSpecificOutput',duplicate)
        self.observation('source-2','新增原因：中文路径改为显式参数')
        updated=self.f.event('claude','PreToolUse',tool_name='Read',tool_input={'file_path':str(self.file)})
        self.assertIn('中文路径',updated['hookSpecificOutput']['additionalContext'])
        with Store(self.f.database) as store:
            history=self.tools(store).call('context.file_history',{'file':str(self.file)})
            self.assertTrue(any(row['source_event']==first for row in history['entries']))
            ref=history['entries'][0]['ref']
            details=self.tools(store).call('context.history_details',{'refs':[ref,{**ref,'event_id':'missing-source'}]})
            self.assertEqual(details['items'][0]['status'],'available')
            self.assertEqual(details['items'][1]['status'],'unavailable')
            around=self.tools(store).call('context.history_neighborhood',{'ref':ref})
            self.assertTrue(around['entries'])
            mixed=self.tools(store).call('context.history_details',{'refs':[
                {'reader':'context.get',**self.f.exported['manifest_ref']},
                {'reader':'memory.stream_get',**{k:v for k,v in ref.items() if k!='origin'}},
                {'reader':'basic-memory.read_note','identifier':'main/unavailable-fixture-note','project':'main'}]})
            self.assertEqual([item['status'] for item in mixed['items']],['available','available','unavailable'])
        cli=self.f.cli('file-history','--session','owner','--query-binding',self.f.query,'--file',self.file)
        self.assertTrue(cli['entries'])
        options=self.f.root/'detail-options.json'
        options.write_text(json.dumps({'refs':[ref]}),encoding='utf-8')
        read=self.f.cli('history-details','--session','owner','--query-binding',self.f.query,'--input',options)
        self.assertEqual(read['items'][0]['status'],'available')
        options.write_text(json.dumps({'ref':ref}),encoding='utf-8')
        neighborhood=self.f.cli('history-neighborhood','--session','owner','--query-binding',self.f.query,'--input',options)
        self.assertTrue(neighborhood['entries'])
        self.assertEqual(self.file.read_text(encoding='utf-8'),before)

    def test_powershell_literal_path_is_supported_and_ambiguous_commands_do_not_block(self):
        self.observation('source-ps','PowerShell 字面路径历史')
        command="Get-Content -LiteralPath '"+str(self.file)+"' -Raw"
        result=self.f.event('codex','PreToolUse',tool_name='exec_command',tool_input={'cmd':command})
        self.assertIn('字面路径历史',result.get('hookSpecificOutput',{}).get('additionalContext',''))
        unknown=self.f.event('codex','PreToolUse',tool_name='exec_command',tool_input={'cmd':'Get-Content $env:UNBOUND_FILE | Invoke-Expression'})
        self.assertNotIn('hookSpecificOutput',unknown);self.assertNotEqual(unknown.get('continue'),False)
        outside=self.f.event('claude','PreToolUse',tool_name='Read',tool_input={'file_path':str(self.f.root/'another-worktree/设计 notes.py')})
        self.assertNotIn('hookSpecificOutput',outside);self.assertNotEqual(outside.get('continue'),False)

    def test_optional_source_failure_still_allows_read_and_reports_gap(self):
        with closing(sqlite3.connect(self.f.home/'state.sqlite')) as db:
            db.execute('DROP TABLE timeline_paths');db.commit()
        result=self.f.event('codex','PreToolUse',tool_name='Read',tool_input={'file_path':str(self.file)})
        self.assertNotEqual(result.get('continue'),False)
        self.assertIn('history',result.get('systemMessage','').lower())
        self.assertEqual(self.file.read_text(encoding='utf-8'),'actual file content')

    def test_replayed_compaction_trigger_does_not_hide_optional_file_history(self):
        self.observation('source-combined','组合场景：文件历史仍可见')
        self.f.event('codex','PostCompact',trigger='auto');self.f.capture_compact('codex','combined-cycle')
        self.f.event('codex','PostToolUse',turn_id='after-compact')
        self.f.event('codex','PostCompact',trigger='auto')
        result=self.f.event('codex','PreToolUse',tool_name='Read',tool_input={'file_path':str(self.file)})
        self.assertIn('文件历史仍可见',result.get('hookSpecificOutput',{}).get('additionalContext',''))

    def test_search_hits_return_direct_detail_and_neighborhood_arguments(self):
        self.observation('source-search','修复查询导航的具体原因')
        with Store(self.f.database) as store:
            reader=self.tools(store)
            result=reader.call('context.query',{'queries':['修复'],'budget':16384})
            hit=next(row for row in result['queries'][0]['hits'] if row['origin']=='events')
            navigation=hit.get('navigation',{})
            self.assertIn('details',navigation)
            detail=reader.call(navigation['details']['tool'],navigation['details']['arguments'])
            self.assertEqual(detail['items'][0]['status'],'available')
            around=reader.call(navigation['neighborhood']['tool'],navigation['neighborhood']['arguments'])
            self.assertTrue(around['entries'])


if __name__=='__main__':unittest.main()
