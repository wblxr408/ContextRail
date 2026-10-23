"""D03/D04/D06 current behavior contract; baseline once, then grouped acceptance."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from contextrail import Store,Scope,Target,Selection
from contextrail.compaction import CompactionLifecycle
from contextrail.errors import RailError

SOURCE='记录🙂 D:\\项目\\config.toml\n执行 `python -m build`。\n原始错误 E0427；详情 https://example.org/guide 。\n使用 v1.2.3，超时 25 ms。不要删除备份。\n'
RESTRICTION='仅完善自用demo，禁止发布。'
GOOD=RESTRICTION+'使用 v1.2.3，超时 25 ms。不要删除备份。'


class GroundedDerivationStage(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='rail-grounded-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.scope=Scope('local','p','w','derive')
        self.store=Store(self.root/'rail.sqlite');self.addCleanup(self.store.close)
        self.lease=self.store.create_task(self.scope,Target('host','local','external'),'self-use',constraints=(RESTRICTION,),allowed_providers=('local',))
        self.ref=self.store.put(self.scope,self.lease,'source',SOURCE.encode(),expected_revision=0)
        self.sid=self.store.snapshot(self.scope,self.lease,(Selection(self.ref),))
        self.life=CompactionLifecycle(self.store);self.job=self.life.begin(self.scope,self.lease,self.sid,reason='grounded')

    def finish(self,text=GOOD):
        return self.life.complete(self.scope,self.lease,self.job.id,title='current',text=text,spans=(self.ref,))

    def test_summary_rejects_missing_current_restriction_and_retains_draft(self):
        rejected=False
        try:self.finish(GOOD.replace(RESTRICTION,''))
        except RailError:rejected=True
        self.assertTrue(rejected,'summary missing current user restriction became completed')
        attempts=self.store.compaction_attempts(self.scope,self.job.id)
        self.assertEqual(attempts[-1]['status'],'rejected');self.assertIn('required',json.dumps(attempts[-1]['report']))
        self.assertEqual(self.store.compaction(self.scope,self.job.id).phase,'prepared')

    def test_direct_store_cannot_commit_wrong_version_or_quantity(self):
        for wrong in (GOOD.replace('v1.2.3','v9.9.9'),GOOD.replace('25 ms','250 ms')):
            with self.assertRaises(RailError):self.store.complete_compaction(self.scope,self.lease,self.job.id,title='bad',text=wrong,spans=(self.ref,))
        self.assertEqual(len(self.store.compaction_attempts(self.scope,self.job.id)),2)

    def test_negation_loss_is_not_an_accepted_summary(self):
        with self.assertRaises(RailError):self.finish(GOOD.replace('不要删除备份','删除备份'))

    def test_omitted_error_remains_searchable_with_exact_utf8_source(self):
        summary=self.finish();found=self.store.search_summaries(self.scope,'E0427')
        self.assertEqual([x.id for x in found],[summary.id],'omitted literal is unreachable through summary search')
        material=self.store.summary_evidence(self.scope,summary.id)
        for literal in ('E0427','D:\\项目\\config.toml','python -m build','https://example.org/guide'):
            entries=[e for e in material['residue']['entries'] if e['text']==literal]
            self.assertTrue(entries,literal)
            for e in entries:self.assertEqual(self.store.get(self.scope,self.ref).content[e['start']:e['end']].decode(),literal)
        self.assertIn('不要删除备份',json.dumps(material['residue'],ensure_ascii=False))

    def test_external_validator_rejection_is_readable_and_retry_can_succeed(self):
        self.life.validate=lambda draft:(_ for _ in ()).throw(ValueError('host rejected unsupported inference'))
        with self.assertRaises(ValueError):self.finish()
        # Existing public state is meaningful even before the new attempt reader exists.
        self.assertTrue(any(e['kind']=='compaction.draft_rejected' for e in self.store.events(self.scope)),'rejected summary has no durable rejection evidence')
        self.assertEqual(self.store.compaction_attempts(self.scope,self.job.id)[-1]['status'],'rejected')
        self.life.validate=None;self.finish();self.assertEqual(self.store.compaction(self.scope,self.job.id).phase,'completed')

    def test_required_bridge_statement_is_checked_even_when_not_cited(self):
        restriction='最后修正：先完成大模块，再上传。'
        row=self.store.put(self.scope,self.lease,'bridge/requirements/latest',json.dumps({'statement':restriction,'status':'active','kind':'action'},ensure_ascii=False).encode(),expected_revision=0)
        snapshot=self.store.snapshot(self.scope,self.lease,(Selection(self.ref),Selection(row,required=True)))
        job=self.life.begin(self.scope,self.lease,snapshot,reason='last-correction')
        with self.assertRaises(RailError):self.life.complete(self.scope,self.lease,job.id,title='omission',text=GOOD,spans=(self.ref,))

    def test_readonly_tool_pages_literals_within_actual_response_budget(self):
        from contextrail.host import ContextTools
        summary=self.finish();reader=Store.open_readonly(self.root/'rail.sqlite');self.addCleanup(reader.close)
        tools=ContextTools(reader,self.scope,'host',max_read_bytes=2000)
        entries=[];offset=0
        while True:
            try:result=tools.call('context.summary_evidence',{'id':summary.id,'offset':offset,'limit':1,'budget':1400})
            except RailError:result={'entries':[],'next_offset':None,'error':'bounded_evidence_unavailable'}
            self.assertNotIn('error',result,'no bounded original-source read route')
            self.assertLessEqual(len(json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()),1400)
            entries.extend(result['entries']);offset=result['next_offset']
            if offset is None:break
        self.assertIn('E0427',[e['text'] for e in entries])

    def memory(self,body):
        root=Path(__file__).resolve().parents[1]
        script='''import sys,json,sqlite3,os
from pathlib import Path
sys.path.insert(0,str(Path.home()/'.codex/services/engineering-memory/app'))
if not os.environ.get('CONTEXT_RAIL_STAGE_INSTALLED_ONLY'):sys.path.insert(0,sys.argv[2]+'/integrations/engineering_memory')
from memory_runtime import MemoryRuntime
root=Path(sys.argv[1]);vault=root/'vault';vault.mkdir(exist_ok=True)
source=vault/'orbit.md'
source.write_text('---\\ntitle: ORBIT\\npermalink: main/orbit\\ntype: note\\n---\\nORBIT '+sys.argv[3],encoding='utf-8')
memory=MemoryRuntime(vault,root/'memory.sqlite',use_models=False)
def generator(e):
    return {'answer':'使用 v1.2.3，超时 25 ms。不要删除备份。','citations':['main/orbit'],'support':[{'citation':'main/orbit','quote':'使用 v1.2.3，超时 25 ms。不要删除备份。'}]}
'''+body+'\nmemory.close()\n'
        runtime=Path.home()/'AppData/Roaming/uv/tools/basic-memory/Scripts/python.exe'
        run=subprocess.run([str(runtime),'-X','utf8','-c',script,str(self.root),str(root),SOURCE],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(run.returncode,0,run.stderr[-5000:])
        return json.loads(run.stdout.splitlines()[-1])

    def test_synthesis_rejects_changed_version_with_real_but_irrelevant_quote(self):
        out=self.memory('''def bad(e):
    x=generator(e);x['answer']='使用 v9.9.9，超时 250 ms。不要删除备份。';return x
try: memory.reflect('ORBIT',generator=bad);result={'rejected':False}
except ValueError: result={'rejected':True}
print(json.dumps(result))''')
        self.assertTrue(out['rejected'],'valid citation alone allowed contradictory literal facts')

    def test_residue_reaches_synthesis_from_beyond_excerpt_and_reports_loss(self):
        out=self.memory('''source.write_text(source.read_text(encoding='utf-8')+'\\n'+('背景内容。'*900)+'\\n末尾错误 E7788，使用 D:\\\\配置\\\\late.toml',encoding='utf-8')
seen={}
def inspect(e):seen.update(e);return generator(e)
memory.reflect('ORBIT',generator=inspect)
print(json.dumps({'residue':seen.get('residue',{})},ensure_ascii=False))''')
        self.assertIn('E7788',json.dumps(out['residue']));self.assertIn('source_utf8_bytes',json.dumps(out['residue']))

    def test_unknown_and_inference_are_distinct_from_exact_source_claims(self):
        out=self.memory('''def typed(e):
    x=generator(e);x['claims']=[{'kind':'source','text':'使用 v1.2.3','citation':'main/orbit','quote':'使用 v1.2.3'}, {'kind':'inference','text':'可能是上游问题'}, {'kind':'unknown','text':'尚不知根因'}];return x
x=memory.reflect('ORBIT',generator=typed)
print(json.dumps(x,ensure_ascii=False))''')
        self.assertIn('validation',out);self.assertEqual([c['kind'] for c in out['claims']],['source','inference','unknown'])

    def test_refresh_versions_survive_source_correction_and_reopen(self):
        out=self.memory('''a=memory.page('ORBIT','ORBIT',refresh=True,generator=generator)
source.write_text(source.read_text(encoding='utf-8').replace('v1.2.3','v2.0.0'),encoding='utf-8')
stale=memory.page('ORBIT','ORBIT')
def corrected(e):
    x=generator(e);x['answer']=x['answer'].replace('v1.2.3','v2.0.0');x['support'][0]['quote']=x['answer'];return x
b=memory.page('ORBIT','ORBIT',refresh=True,generator=corrected)
result={'a':a,'stale':stale,'b':b}
if a.get('version'):
    memory.close();memory=MemoryRuntime(vault,root/'memory.sqlite',use_models=False)
    result['old']=memory.page('ORBIT','ORBIT',version=a['version'])
print(json.dumps(result,ensure_ascii=False))''')
        self.assertIsNone(out['stale']['content'],'stale knowledge still returned as current content')
        self.assertNotEqual(out['a']['version'],out['b']['version']);self.assertIn('v1.2.3',out['old']['content']);self.assertIn('v2.0.0',out['b']['content'])

    def test_failed_refresh_preserves_previous_version_and_rejected_draft(self):
        out=self.memory('''a=memory.page('ORBIT','ORBIT',refresh=True,generator=generator)
source.write_text(source.read_text(encoding='utf-8').replace('25 ms','30 ms'),encoding='utf-8')
def invalid(e):return generator(e)
try:b=memory.page('ORBIT','ORBIT',refresh=True,generator=invalid)
except ValueError:b={'content':'no persistent refresh rejection'}
result={'a':a,'b':b}
if a.get('version'):result['history']=memory.page('ORBIT','ORBIT',history=True)
print(json.dumps(result,ensure_ascii=False))''')
        self.assertIsNone(out['b']['content']);self.assertTrue(out['b']['stale']);self.assertEqual(out['a']['version'],out['b']['previous_version'])
        self.assertTrue(out['history']['attempts']);self.assertIn('25 ms',json.dumps(out['history']['attempts']))

    def test_source_change_during_generation_cannot_publish_stale_answer(self):
        out=self.memory('''def racing(e):
    source.write_text(source.read_text(encoding='utf-8').replace('v1.2.3','v3.0.0'),encoding='utf-8');return generator(e)
result=memory.page('ORBIT','ORBIT',refresh=True,generator=racing)
print(json.dumps(result,ensure_ascii=False))''')
        self.assertTrue(out['stale']);self.assertIsNone(out['content']);self.assertIn('source_changed',json.dumps(out))

    def test_deleted_source_and_legacy_cache_never_return_current_prose(self):
        out=self.memory('''a=memory.page('ORBIT','ORBIT',refresh=True,generator=generator)
source.unlink();deleted=memory.page('ORBIT','ORBIT')
identity=json.dumps(['legacy','global'],ensure_ascii=False)
with memory.db:memory.db.execute('INSERT OR REPLACE INTO memory_pages VALUES(?,?,?,?,?,?)',(identity,'ORBIT','global',memory._generation(),'unverified legacy text','[]'))
legacy=memory.page('legacy','ORBIT')
print(json.dumps({'deleted':deleted,'legacy':legacy},ensure_ascii=False))''')
        self.assertIsNone(out['deleted']['content']);self.assertIsNone(out['legacy']['content']);self.assertTrue(out['legacy']['stale'])

    def test_archive_failure_never_switches_current_pointer(self):
        out=self.memory('''a=memory.page('ORBIT','ORBIT',refresh=True,generator=generator)
source.write_text(source.read_text(encoding='utf-8').replace('v1.2.3','v2.0.0'),encoding='utf-8')
import memory_runtime
original=memory_runtime.atomic_text
def fail(path,text):raise OSError('injected archive failure')
memory_runtime.atomic_text=fail
def corrected(e):
    x=generator(e);x['answer']=x['answer'].replace('v1.2.3','v2.0.0');x['support'][0]['quote']=x['answer'];return x
b=memory.page('ORBIT','ORBIT',refresh=True,generator=corrected)
memory_runtime.atomic_text=original
print(json.dumps({'a':a,'b':b,'read':memory.page('ORBIT','ORBIT')},ensure_ascii=False))''')
        self.assertIsNone(out['b']['content']);self.assertEqual(out['b']['previous_version'],out['a']['version']);self.assertIsNone(out['read']['content'])

    def test_remote_page_history_uses_the_same_owned_versions(self):
        out=self.memory('''memory.page('ORBIT','ORBIT',refresh=True,generator=generator)
from resource_client import RemoteRuntime
from memory_integration import dispatch
remote=RemoteRuntime(root)
def invoke(name,*args,**kwargs):
    kwargs.pop('_synthesize',None);return getattr(memory,name)(*args,**kwargs)
remote.invoke=invoke
try:result=dispatch(remote,'memory_knowledge_page',{'name':'ORBIT','query':'ORBIT','history':True})['structuredContent']['result']
except TypeError:result={'versions':[],'error':'remote_history_not_supported'}
print(json.dumps(result,ensure_ascii=False))''')
        self.assertTrue(out['versions'],'remote client could not read the original owned version history')

    def test_derivation_attempt_does_not_retain_query_credentials(self):
        out=self.memory('''secret='sk-'+('A'*40)
memory.reflect('ORBIT api_key='+secret,generator=generator)
saved='\\n'.join(str(tuple(r)) for r in memory.db.execute('SELECT * FROM memory_derivation_attempts'))
print(json.dumps({'leaked':secret in saved}))''')
        self.assertFalse(out['leaked'],'new derivation attempts retained a raw query credential')


if __name__=='__main__':unittest.main()
