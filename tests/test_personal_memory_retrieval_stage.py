"""G01/I05 consumers through the original memory runtime and Rail query."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PersonalMemoryRetrievalTests(unittest.TestCase):
    def scenario(self, body):
        setup = r'''
import json, shutil, socketserver, subprocess, sys, threading
from pathlib import Path
repo, root = map(Path,sys.argv[1:3])
overlay = repo/'integrations/engineering_memory'
sys.path[:0] = [str(overlay),str(Path.home()/'.codex/services/engineering-memory/app')]
from memory_runtime import MemoryRuntime, markdown
from mcp_proxy import Adapter
from resource_client import receive, send, MAX_REQUEST, MAX_RESPONSE
from contextrail import Store, Target
from contextrail.project import load_project
from contextrail.host import ContextTools
vault=root/'vault';vault.mkdir()
a=root/'ContextRail';a.mkdir()
b=root/'other';b.mkdir()
class FixtureModels:
    embedding_name='fixture'
    reranker_name='fixture'
    def __init__(self):
        self.calls=[]
        self.unavailable=False
    def embed(self,texts):
        self.calls.append(('embed',len(texts)))
        if self.unavailable:raise RuntimeError('fixture model unavailable')
        return [[0.0,0.0] if 'unrankable phrase' in text else
                [1.0,0.0] if ('abrupt disruption' in text or 'DurableRecoveryProcedure' in text)
                else [0.0,1.0] for text in texts]
    def rerank(self,query,texts):
        self.calls.append(('rerank',len(texts)))
        return [1.0 if 'DurableRecoveryProcedure' in text else 0.0 for text in texts]
model=FixtureModels()
memory=MemoryRuntime(vault,root/'state.sqlite',use_models=False,model_backend=model)
def note(name,text,scope=b,**meta):
    (vault/(name+'.md')).write_text(markdown({'title':name,'permalink':'main/'+name,'scope':str(scope),**meta},text),encoding='utf-8')
def fact(event,subject,text,scope,kind='preference',source='user',authority=True,**fields):
    claim={'subject':subject,'predicate':kind,'value':text,'quote':text,'source':source,'scope':str(scope),
           'kind':kind,'authority':authority,**fields}
    memory.retain(event,[claim],{source:text})
adapter=Adapter(memory,{'vault':str(vault)},root)
class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        request=receive(self.request,MAX_REQUEST)
        if request['token']=='fixture':
            try:reply=adapter.request(request['message'])
            except ValueError as exc:reply={'error':{'message':str(exc)}}
            send(self.request,reply,MAX_RESPONSE)
app=root/'app';app.mkdir()
shutil.copyfile(overlay/'resource_client.py',app/'resource_client.py')
def binding(workspace,task,mode='text'):
    return {'schema':1,'workspace':str(workspace),'task':task,'memory_home':str(root),
            'memory_mode':mode,'personal_profile':True}
def make(workspace,task,constraints=()):
    config=load_project(workspace,overrides={'task':task,'store':str(root/'rail.sqlite')})
    store=Store(config.store)
    lease=store.create_task(config.scope,Target('owner','host','external'),'Current task',
                            constraints=constraints,allowed_providers=('host',))
    return store,config,lease
def cli(workspace,task,command,args=(),request=None):
    source=root/(task+'-binding.json')
    source.write_text(json.dumps(binding(workspace,task)),encoding='utf-8')
    run=subprocess.run([sys.executable,'-m','contextrail',command,'--workspace',str(workspace),
        '--task',task,'--session','owner','--store',str(root/'rail.sqlite'),
        '--query-binding',str(source),*args],input=json.dumps(request) if request is not None else None,
        capture_output=True,text=True,encoding='utf-8',timeout=18)
    if run.returncode:raise RuntimeError(run.stderr)
    return json.loads(run.stdout)
with socketserver.TCPServer(('127.0.0.1',0),Handler) as server:
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    (root/'resource-endpoint.json').write_text(json.dumps({'port':server.server_address[1],'token':'fixture'}),encoding='utf-8')
    try:
'''
        ending = "\n    finally:\n        server.shutdown();thread.join();memory.close()\n"
        with tempfile.TemporaryDirectory() as folder:
            script = setup + "\n".join("        " + line if line else "" for line in body.splitlines()) + ending
            run = subprocess.run([sys.executable, "-X", "utf8", "-c", script, str(ROOT), folder],
                                 capture_output=True, text=True, encoding="utf-8", timeout=70)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_new_brief_reuses_only_explicit_defaults_and_honors_override_and_revocation(self):
        result = self.scenario(r'''
fact('global-default','package manager','Across projects I default to pnpm.','global',profile_scope='global')
fact('local-only','delivery','This ContextRail demo is only for my own use.',a)
fact('project-decision','package manager','For ContextRail use npm.',a,kind='decision',authority=False)
fact('assistant-only','assistant-guess','Use yarn everywhere.','global',source='final',profile_scope='global')
fact('fallback-cwd','color','I prefer blue.','global')
store_a,conf_a,lease_a=make(a,'a')
store_b,conf_b,lease_b=make(b,'b')
store_c,conf_c,lease_c=make(b,'c',('Use npm as the package manager for this task.',))
sa=store_a.snapshot(conf_a.scope,lease_a,())
sb=store_b.snapshot(conf_b.scope,lease_b,())
sc=store_c.snapshot(conf_c.scope,lease_c,())
a_view=cli(a,'a','brief',['--snapshot',sa,'--budget','16384'])
b_view=cli(b,'b','brief',['--snapshot',sb,'--budget','16384'])
c_view=cli(b,'c','brief',['--snapshot',sc,'--budget','16384'])
req={'action':'revoke','scope':'global','event_id':'retire-pnpm','subject':'package manager',
     'statement':'Withdraw my cross-project pnpm default.'}
saved=cli(b,'b','personal-profile',['--epoch','0'],req)
replay=cli(b,'b','personal-profile',['--epoch','0'],req)
after=cli(b,'b','personal-profile',request={'action':'view'})
for store in (store_a,store_b,store_c):store.close()
print(json.dumps(dict(a=a_view,b=b_view,c=c_view,saved=saved,replay=replay,after=after)))
''')
        defaults = result["b"]["personal_profile"]["entries"]
        self.assertEqual([row["subject"] for row in defaults], ["package manager"])
        self.assertEqual(defaults[0]["quote"], "Across projects I default to pnpm.")
        self.assertEqual(defaults[0]["ref"]["project"], "main")
        self.assertEqual([row["subject"] for row in result["a"]["personal_profile"]["entries"]], ["delivery"])
        self.assertEqual(result["c"]["personal_profile"]["entries"], [])
        self.assertEqual(result["c"]["task"]["constraints"], ["Use npm as the package manager for this task."])
        self.assertEqual(result["saved"]["event_id"], result["replay"]["event_id"])
        self.assertTrue(result["replay"]["duplicate"])
        self.assertEqual(result["after"]["entries"], [])

    def test_adaptive_search_keeps_exact_evidence_and_only_escalates_when_needed(self):
        result = self.scenario(r'''
note('windows-result','HTTP429 returned by D:/tools/runner.py during the native transport.')
note('longer-identifier','HTTP4290 returned by D:/tools/runner.py.bak elsewhere.')
note('recovery','DurableRecoveryProcedure stores a checkpoint and reconnects on restart.')
note('lexical-partial','unexpected bridge message retained for inspection.')
store,config,lease=make(b,'lookup')
tools=ContextTools(store,config.scope,'owner',query_binding=binding(b,'lookup','adaptive'))
def read(q):
    return tools.call('context.query',{'queries':[q],'budget':16384,'refresh':True})['queries'][0]
exact=read('HTTP429')
keyword=read('checkpoint')
no_calls=list(model.calls)
semantic=read('abrupt disruption')
semantic_calls=list(model.calls)
model.unavailable=True
failed=read('unexpected completely different power loss')
path=read('D:\\tools\\runner.py')
after=list(model.calls)
model.unavailable=False
no_gain=read('unexpected unrankable phrase')
batch=tools.call('context.query',{'queries':['abrupt disruption first','abrupt disruption second',
    'abrupt disruption third','HTTP429'],'budget':32768,'refresh':True})['queries']
store.close()
print(json.dumps(dict(exact=exact,keyword=keyword,no_calls=no_calls,semantic=semantic,
                      semantic_calls=semantic_calls,failed=failed,path=path,after=after,batch=batch,no_gain=no_gain)))
''')
        def hits(key):
            return [row["id"] for row in result[key]["hits"] if row["origin"] == "memory"]
        self.assertEqual(hits("exact"), ["main/windows-result"])
        self.assertIn("main/recovery", hits("keyword"))
        self.assertEqual(result["no_calls"], [])
        self.assertEqual(hits("semantic")[0], "main/recovery")
        self.assertTrue(result["semantic_calls"])
        self.assertLessEqual(max(call[1] for call in result["semantic_calls"]), 8)
        self.assertIn("main/lexical-partial", hits("failed"))
        self.assertEqual(result["failed"]["sources"]["memory"]["coverage"]["routing"]["route"],
                         "lexical_model_unavailable")
        self.assertEqual(hits("path"), ["main/windows-result"])
        self.assertEqual(len(result["after"]), len(result["semantic_calls"]) + 1)
        self.assertEqual(hits("no_gain"), ["main/lexical-partial"])
        self.assertEqual(result["no_gain"]["sources"]["memory"]["coverage"]["routing"]["route"],
                         "lexical_no_semantic_gain")
        routes = [row["sources"]["memory"]["coverage"]["routing"] for row in result["batch"]]
        self.assertEqual(sum(row["model_attempted"] is True for row in routes), 2)
        self.assertEqual([row["route"] for row in routes[-2:]], ["lexical_batch_limit", "lexical_exact"])

    def test_bridge_native_startup_includes_defaults_below_the_current_prompt(self):
        result = self.scenario(r'''
from tests import test_native_client_context as fixture
case=fixture.NativeContextTests('test_client_profiles_expose_bound_packet_and_cold_page_over_real_stdio')
case.setUp()
try:
    fact('native-default','review cadence','Across projects I prefer a short review before delivery.',
         'global',profile_scope='global')
    path=root/'native-profile.json'
    path.write_text(json.dumps(binding(case.workspace,'native-demo')),encoding='utf-8')
    layout=case.cli('client-prepare','--session','owner','--epoch','0','--snapshot',case.exported['snapshot'],
                    '--output',case.root/'profile-client','--query-binding',path)
    def send_native(session,prompt):
        payload={'hook_event_name':'UserPromptSubmit','session_id':session,'cwd':str(case.workspace),
                 'prompt':prompt,'transcript_path':'not-read'}
        result=case.cli('host-hook','--client','codex','--registry',layout['registry'],
                        input=json.dumps(payload).encode())
        return json.loads(result['hookSpecificOutput']['additionalContext'])
    first=send_native('startup-default','Continue the current demo task.')
    second=send_native('startup-override','For this task the review cadence is immediate.')
    print(json.dumps(dict(first=first,second=second)))
finally:
    case.doCleanups()
''')
        self.assertEqual([item["subject"] for item in result["first"]["personal_profile"]["entries"]],
                         ["review cadence"])
        self.assertEqual(result["second"]["personal_profile"]["entries"], [])
        self.assertEqual(result["first"]["task"]["required_ids"], result["second"]["task"]["required_ids"])
