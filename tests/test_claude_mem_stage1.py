"""Focused consumer contracts for this stage's new behavior only."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import asdict
from contextlib import closing

from contextrail import Scope, Store, Target
from contextrail.host import ContextTools
from contextrail.native_clients import apply_project_clients, remove_project_clients
from contextrail.query_sources import QueryBinding
from contextrail.errors import RailError


class Stage1Clients(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="rail stage1 中文 ")
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.workspace=self.root/"用户 workspace"
        self.workspace.mkdir()
        self.layout=self.root/"layout v1"
        self.make_layout(self.layout, "entry-v1.py")
        (self.workspace/".codex").mkdir()
        (self.workspace/".codex/config.toml").write_text('# my comment\n[other]\nvalue = "保留"\n',encoding="utf-8")
        self.user_hook={"hooks":{"UserPromptSubmit":[{"hooks":[{"type":"command","command":"user-owned-hook","timeout":7}]}]},"user_field":"保留"}
        (self.workspace/".codex/hooks.json").write_text(json.dumps(self.user_hook),encoding="utf-8")

    def make_layout(self,path,entry):
        path.mkdir()
        binding={"client":"codex","workspace":str(self.workspace),"store":str(self.workspace/"state.sqlite3"),
                 "scope":{"tenant":"local","project":"demo","branch":"worktree","task":"task"},
                 "session":"reader","base_snapshot":"snapshot","owner":{"session":"owner","epoch":0},
                 "enabled":True,"context_bytes":8192,"hook_bytes":24576}
        (path/"registry.json").write_text(json.dumps({"schema":1,"memory":None,"bindings":[binding]}),encoding="utf-8")
        (path/"codex.config.toml").write_text('[mcp_servers.contextrail]\ncommand = "python"\nargs = '+json.dumps([entry])+'\n',encoding="utf-8")
        (path/"codex.hooks.json").write_text(json.dumps({"hooks":{"UserPromptSubmit":[{"hooks":[{"type":"command","command":"python "+entry,"timeout":25}]}]}}),encoding="utf-8")

    def test_owned_layout_upgrade_keeps_unrelated_user_configuration(self):
        apply_project_clients(self.layout,self.workspace,clients=("codex",))
        newer=self.root/"layout v2"
        self.make_layout(newer,"entry-v2.py")
        try:
            apply_project_clients(newer,self.workspace,clients=("codex",))
            outcome="upgraded"
        except RailError:
            outcome="rejected"
        self.assertEqual(outcome,"upgraded","An installed owned entry must migrate without treating it as an unknown user conflict")
        import tomllib
        data=tomllib.loads((self.workspace/".codex/config.toml").read_text(encoding="utf-8"))
        self.assertEqual(data["mcp_servers"]["contextrail"]["args"],["entry-v2.py"])
        self.assertEqual(data["other"]["value"],"保留")
        hooks=json.loads((self.workspace/".codex/hooks.json").read_text(encoding="utf-8"))
        commands=[h["command"] for g in hooks["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
        self.assertEqual(commands,["user-owned-hook","python entry-v2.py"])

    def test_reapply_then_remove_owns_one_effect_and_preserves_user_hooks(self):
        apply_project_clients(self.layout,self.workspace,clients=('codex',))
        replay=apply_project_clients(self.layout,self.workspace,clients=('codex',))
        self.assertEqual(replay['applied'],[])
        command=[sys.executable,'-m','contextrail','client-remove','--workspace',str(self.workspace),'--client','codex']
        removed=subprocess.run(command,capture_output=True)
        self.assertEqual(removed.returncode,0,removed.stderr.decode('utf-8'))
        import tomllib
        config=tomllib.loads((self.workspace/'.codex/config.toml').read_text(encoding='utf-8'))
        self.assertEqual(config,{'other':{'value':'保留'}})
        self.assertEqual(json.loads((self.workspace/'.codex/hooks.json').read_text(encoding='utf-8')),self.user_hook)
        self.assertEqual(json.loads((self.workspace/'.context-rail/client-install.json').read_text(encoding='utf-8'))['clients'],{})

    def test_user_edit_prevents_uninstall_without_partial_removal(self):
        apply_project_clients(self.layout,self.workspace,clients=('codex',))
        path=self.workspace/'.codex/hooks.json'
        edited=json.loads(path.read_text(encoding='utf-8'));edited['hooks']['UserPromptSubmit'][-1]['hooks'][0]['timeout']=99
        path.write_text(json.dumps(edited),encoding='utf-8')
        with self.assertRaises(RailError): remove_project_clients(self.workspace,clients=('codex',))
        import tomllib
        self.assertEqual(tomllib.loads((self.workspace/'.codex/config.toml').read_text(encoding='utf-8'))['mcp_servers']['contextrail']['args'],['entry-v1.py'])
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')),edited)

    def test_both_clients_upgrade_and_claude_removal_preserves_codex_and_user_server(self):
        def claude_layout(path,entry):
            registry=json.loads((path/'registry.json').read_text(encoding='utf-8'))
            registry['bindings'].append({**registry['bindings'][0],'client':'claude'})
            (path/'registry.json').write_text(json.dumps(registry),encoding='utf-8')
            (path/'claude.mcp.json').write_text(json.dumps({'mcpServers':{'contextrail':{'command':'python','args':[entry]}}}),encoding='utf-8')
            (path/'claude.settings.json').write_text(json.dumps({'hooks':{'UserPromptSubmit':[{'hooks':[{'type':'command','command':'python '+entry,'timeout':25}]}]}}),encoding='utf-8')
        claude_layout(self.layout,'claude-v1.py')
        (self.workspace/'.mcp.json').write_text(json.dumps({'mcpServers':{'mine':{'command':'my-server'}}}),encoding='utf-8')
        apply_project_clients(self.layout,self.workspace)
        new=self.root/'layout v2';self.make_layout(new,'entry-v2.py');claude_layout(new,'claude-v2.py')
        apply_project_clients(new,self.workspace)
        servers=json.loads((self.workspace/'.mcp.json').read_text(encoding='utf-8'))['mcpServers']
        self.assertEqual(servers['contextrail']['args'],['claude-v2.py'])
        remove_project_clients(self.workspace,clients=('claude',))
        self.assertEqual(json.loads((self.workspace/'.mcp.json').read_text(encoding='utf-8'))['mcpServers'],{'mine':{'command':'my-server'}})
        self.assertIn('codex',json.loads((self.workspace/'.context-rail/client-install.json').read_text(encoding='utf-8'))['clients'])

    @unittest.skipUnless(os.name=="nt","This case exercises a real Windows denied-delete handle")
    def test_late_locked_hook_restores_prior_configuration(self):
        import ctypes
        from ctypes import wintypes
        api=ctypes.WinDLL("kernel32",use_last_error=True)
        api.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        api.CreateFileW.restype=wintypes.HANDLE
        api.CloseHandle.argtypes=[wintypes.HANDLE]
        before=(self.workspace/".codex/config.toml").read_text(encoding="utf-8")
        handle=api.CreateFileW(str(self.workspace/".codex/hooks.json"),0x80000000,3,None,3,0,None)
        if handle==ctypes.c_void_p(-1).value:
            self.fail("Could not acquire fixture's denied-delete handle")
        try:
            with self.assertRaises((RailError,OSError)):
                apply_project_clients(self.layout,self.workspace,clients=("codex",))
        finally:
            api.CloseHandle(handle)
        self.assertEqual((self.workspace/".codex/config.toml").read_text(encoding="utf-8"),before,
                         "A later target failing must not leave an earlier config installed")
        self.assertEqual(json.loads((self.workspace/".codex/hooks.json").read_text()),self.user_hook)


class Stage1Faults(unittest.TestCase):
    def test_status_explains_quota_deferral_without_model_or_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);home=root/"memory";home.mkdir()
            workspace=root/"workspace";workspace.mkdir()
            db=sqlite3.connect(home/"state.sqlite")
            db.executescript("""
                CREATE TABLE jobs(id TEXT PRIMARY KEY,source TEXT,payload TEXT,state TEXT,attempts INTEGER,created REAL,available REAL);
                CREATE TABLE job_control(job_id TEXT PRIMARY KEY,reason TEXT,next_retry REAL,first_wait REAL,stage TEXT,cancel_requested INTEGER,updated REAL);
                CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE source_event_conflicts(id INTEGER PRIMARY KEY,event_id TEXT,reason TEXT,changed_fields TEXT,first_seen REAL,incoming TEXT);
            """)
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?)",("job", "source", json.dumps({"cwd":str(workspace)}),"retry",1,100,4102444800))
            db.execute("INSERT INTO job_control VALUES(?,?,?,?,?,?,?)",("job","quota_exhausted",4102444800,100,"waiting",0,100))
            db.commit();db.close()
            scope=Scope("local","demo","main","task")
            with Store(root/"rail.sqlite3") as store:
                store.create_task(scope,Target("owner","host","external"),"keep current corrections",allowed_providers=("host",))
                binding=QueryBinding.parse({"schema":1,"workspace":str(workspace),"task":"task","memory_home":str(home)}, "task")
                result=ContextTools(store,scope,"owner",query_binding=binding).call("context.status",{})
            quota=next((x for x in result.get("diagnostics",[]) if x.get("code")=="quota_deferred"),None)
            self.assertIsNotNone(quota,"Observed quota wait must be explained, rather than just counted as a pending job")
            self.assertFalse(quota["restart_recommended"])
            self.assertEqual(quota["source"],"existing_memory.job_control")
            self.assertEqual(result["injection"]["model_consumption"],"unknown")
            self.assertEqual(result["model_calls"],0)
            self.assertEqual(result['effective_config']['memory_home'],str(home))
            self.assertFalse(result['effective_config']['writes_performed'])
            for reason,state,expected in [('auth_invalid','retry','credential_rejected'),('index_unavailable','awaiting_index','index_unavailable'),
                                           ('provider_failure_with_private_text','failed','queue_deferred')]:
                with self.subTest(reason=reason):
                    with closing(sqlite3.connect(home/'state.sqlite')) as db:
                        db.execute('UPDATE job_control SET reason=?,next_retry=1',(reason,))
                        db.execute('UPDATE jobs SET state=?',(state,));db.commit()
                    with Store(root/'rail.sqlite3') as store:
                        tools=ContextTools(store,scope,'owner',query_binding=binding)
                        observed=tools.call('context.status',{})
                    diagnostic=next(d for d in observed['diagnostics'] if d['code']==expected)
                    self.assertEqual(diagnostic['recovery'],'not_observed')
                    self.assertEqual(diagnostic['state'],'last_observed_wait_retry_due')
                    self.assertNotIn('provider_failure_with_private_text',json.dumps(observed))
                    if state=='awaiting_index': self.assertIn('awaiting_index',[d['code'] for d in observed['diagnostics']])
                    if state=='failed': self.assertIn('failed_jobs',[d['code'] for d in observed['diagnostics']])
                    with closing(sqlite3.connect(home/'state.sqlite')) as db:
                        self.assertEqual(db.execute('SELECT reason,next_retry FROM job_control').fetchone(),(reason,1))
            with patch.dict(os.environ,{'CONTEXTRAIL_NODE':str(root/'missing-node.exe')}):
                with Store(root/'rail.sqlite3') as store:
                    result=ContextTools(store,scope,'owner',query_binding=binding).call('context.status',{})
            self.assertEqual(result['task']['objective'],'keep current corrections')
            self.assertIn('classifier_unavailable',[d['code'] for d in result['diagnostics']])


class Stage1ExplicitMemory(unittest.TestCase):
    def test_offline_explicit_save_replay_conflict_and_grounded_readback(self):
        with tempfile.TemporaryDirectory(prefix='rail manual 中文 ') as folder:
            root=Path(folder);home=root/'memory';app=home/'app';app.mkdir(parents=True)
            vault=root/'vault';vault.mkdir();workspace=root/'用户 workspace';workspace.mkdir()
            installed=Path.home()/'.codex/services/engineering-memory/app'
            # Actual existing M code, real Markdown/SQLite; no fake retain or model backend.
            shutil.copyfile(Path('integrations/engineering_memory/memory_runtime.py'),app/'memory_runtime.py')
            for name in ('canonical.py','capture_sanitizer.py','checkpoint_lifecycle.py'):
                shutil.copyfile(installed/name,app/name)
            (home/'config.json').write_text(json.dumps({'vault':str(vault)}),encoding='utf-8')
            request=root/'record.json'
            data={'event_id':'user-record-1','text':'只在明确要求后发布。Delivery requires my explicit request.','title':'交付偏好'}
            request.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
            command=[sys.executable,'-m','contextrail','memory-save','--workspace',str(workspace),'--home',str(home),'--input',str(request)]
            first=subprocess.run(command,capture_output=True)
            self.assertEqual(first.returncode,0,'Explicit memory must save to existing M without starting a model: '+first.stderr.decode('utf-8'))
            value=json.loads(first.stdout)
            self.assertEqual(value['learning_state'],'explicit_user_record_not_model_derived')
            self.assertEqual(value['model_calls'],0)
            self.assertEqual(value['index_state'],'not_observed')
            self.assertIn(data['text'],Path(value['source']).read_text(encoding='utf-8'))
            recalled=subprocess.run([sys.executable,'-X','utf8','-c',
                "import sys,json;sys.path.insert(0,sys.argv[1]);from memory_runtime import MemoryRuntime;"
                "m=MemoryRuntime(sys.argv[2],sys.argv[3],use_models=False);"
                "print(json.dumps(m.recall('Delivery',scope=sys.argv[4],mode='text'),ensure_ascii=False));m.close()",
                str(app),str(vault),str(home/'state.sqlite'),str(workspace)],capture_output=True)
            self.assertEqual(recalled.returncode,0,recalled.stderr.decode('utf-8'))
            found=json.loads(recalled.stdout)
            self.assertEqual(found['mode'],'text');self.assertIsNone(found['model'])
            self.assertTrue(any(row['source_event']==value['event_id'] and 'Delivery' in row['excerpt'] for row in found['results']))
            replay=subprocess.run(command,capture_output=True)
            self.assertEqual(replay.returncode,0)
            self.assertTrue(json.loads(replay.stdout)['duplicate'])
            with closing(sqlite3.connect(home/'state.sqlite')) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM memory_events').fetchone()[0],1)
            self.assertEqual(len(list((vault/'engineering/memory-facts').glob('*.md'))),1)
            data['text']='改为自动发布';request.write_text(json.dumps(data),encoding='utf-8')
            conflict=subprocess.run(command,capture_output=True)
            self.assertNotEqual(conflict.returncode,0)
            self.assertEqual(json.loads(conflict.stderr)['error']['code'],'conflict')
            self.assertIn('Delivery requires',Path(value['source']).read_text(encoding='utf-8'))


if __name__=="__main__":
    unittest.main()

