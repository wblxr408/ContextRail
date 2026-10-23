"""F01 current behavior: source identity and one queue effect across replay."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


APP=Path.home()/'.codex/services/engineering-memory/app'
OVERLAY=Path(__file__).resolve().parents[1]/'integrations/engineering_memory'


class SourceEventIdentityTests(unittest.TestCase):
    def run_memory(self, script):
        with tempfile.TemporaryDirectory(prefix='rail-event-') as temp:
            code='''import json,sys,time
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path[:0]=sys.argv[1:3]
from listener import Store
home=Path(sys.argv[3]); workspace=home/'workspace';workspace.mkdir()
source=home/'native.jsonl'
def records(turn='turn1', final='same final', session='session1', kind='codex'):
    if kind=='claude':
        return [{'type':'assistant','sessionId':session,'cwd':str(workspace),'uuid':'uuid-'+turn,
            'message':{'id':turn,'content':[{'type':'text','text':final}],'stop_reason':'end_turn'}}]
    return [{'type':'session_meta','payload':{'id':session,'cwd':str(workspace)}},
        {'type':'event_msg','timestamp':'2026-10-23T03:00:00Z','payload':{'type':'task_complete','turn_id':turn,'last_agent_message':final}}]
def append(rows):
    with source.open('ab') as stream:
        for row in rows:stream.write((json.dumps(row)+'\\n').encode())
store=Store(home)
'''+script
            child=subprocess.run([sys.executable,'-c',code,str(OVERLAY),str(APP),temp],capture_output=True,text=True,encoding='utf-8',timeout=35)
            self.assertEqual(child.returncode,0,child.stderr)
            return json.loads(child.stdout)

    def test_same_turn_changed_reply_is_conflict_not_second_job(self):
        result=self.run_memory('''append(records(final='original reply'))
store.ingest(source,'codex')
append(records(final='changed reply'))
stats={};store.ingest(source,'codex',stats=stats)
rows=[json.loads(r[0]) for r in store.db.execute('SELECT payload FROM jobs')]
print(json.dumps({'rows':rows,'stats':stats}))
''')
        self.assertEqual(len(result['rows']),1,'same native event generated multiple queue effects')
        self.assertEqual(result['rows'][0]['final'],'original reply')
        self.assertEqual(result['stats'].get('identity_conflict'),1)

    def test_no_native_id_equal_body_at_different_positions_is_not_merged(self):
        result=self.run_memory('''append(records(turn=''))
store.ingest(source,'codex')
append(records(turn=''))
store.ingest(source,'codex')
before=store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]
store.backfill(source,'codex')
after=store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]
print(json.dumps({'before':before,'after':after}))
''')
        self.assertEqual(result['before'],2,'two source positions with equal text were merged')
        self.assertEqual(result['after'],2,'backfill duplicated source positions')

    def test_distinct_native_events_same_text_and_replay_after_relocation(self):
        result=self.run_memory('''append(records('turn1')+records('turn2'))
store.ingest(source,'codex')
copy=home/'archived.jsonl';copy.write_bytes(source.read_bytes())
store.ingest(copy,'codex')
rows=[json.loads(r[0]) for r in store.db.execute('SELECT payload FROM jobs')]
print(json.dumps(rows))
''')
        self.assertEqual(len(result),2)
        self.assertTrue(all('_event' in r for r in result),'stable source identity missing from captured payload')
        self.assertNotEqual(result[0]['_event']['id'],result[1]['_event']['id'])

    def test_claude_repeated_message_with_changed_body_is_diagnostic(self):
        result=self.run_memory('''append(records('message1','original Claude final',kind='claude'))
store.ingest(source,'claude')
append(records('message1','changed Claude final',kind='claude')+records('message2','original Claude final',kind='claude'))
store.ingest(source,'claude')
from event_identity import identity_status
print(json.dumps({'count':store.db.execute('SELECT count(*) FROM jobs').fetchone()[0],'report':identity_status(store.db)}))
''')
        self.assertEqual(result['count'],2)
        self.assertEqual(result['report']['conflicts_total'],1)
        conflicts=[c for e in result['report']['entries'] for c in e['conflicts']]
        self.assertEqual(conflicts[0]['fields'],['final'])
        self.assertFalse('changed Claude final' in json.dumps(result['report']))

    def test_legacy_saved_job_keeps_original_id_state_and_reference(self):
        result=self.run_memory('''from listener import digest
append(records(final='legacy final'))
raw=source.read_bytes();start=raw.index(b'\\n')+1
payload={'user':'','final':'legacy final','cwd':str(workspace),'kind':'codex','marker':'turn1','offset':start,'source':str(source),'occurred_at':'2026-10-23T03:00:00Z'}
old_id=digest('codex'+'session1'+'turn1'+'legacy final')
with store.db:store.db.execute('INSERT INTO jobs(id,source,payload,state,created,available,files) VALUES(?,?,?,?,?,?,?)',(old_id,str(source),json.dumps(payload),'saved',0,0,'["existing-fact.md"]'))
store.db.executescript('DROP TABLE source_events; DROP TABLE source_event_aliases; DROP TABLE source_event_conflicts;')
store.db.close();store=Store(home)
store.backfill(source,'codex')
row=dict(store.db.execute('SELECT * FROM jobs').fetchone())
from event_identity import identity_status
print(json.dumps({'count':store.db.execute('SELECT count(*) FROM jobs').fetchone()[0],'job':row,'report':identity_status(store.db),'old_id':old_id}))
''')
        self.assertEqual(result['count'],1)
        self.assertEqual(result['job']['id'],result['old_id'])
        self.assertEqual(result['job']['state'],'saved')
        self.assertEqual(result['job']['files'],'["existing-fact.md"]')
        self.assertEqual(result['report']['entries'][0]['job_id'],result['old_id'])

    def test_legacy_ambiguity_preserves_both_and_blocks_new_effect(self):
        result=self.run_memory('''append(records(final='incoming final'))
for index in range(2):
    payload={'user':'','final':'legacy final '+str(index),'cwd':str(workspace),'kind':'codex','marker':'turn1','offset':0}
    with store.db:store.db.execute('INSERT INTO jobs(id,source,payload,state,created,available) VALUES(?,?,?,?,?,?)',('old-'+str(index),str(source),json.dumps(payload),'saved',0,0))
store.ingest(source,'codex')
from event_identity import identity_status
print(json.dumps({'count':store.db.execute('SELECT count(*) FROM jobs').fetchone()[0],'report':identity_status(store.db)}))
''')
        self.assertEqual(result['count'],2)
        event=result['report']['entries'][0]
        self.assertEqual(event['status'],'ambiguous')
        self.assertEqual(set(event['aliases']),{'old-0','old-1'})
        self.assertEqual(event['conflicts'][0]['reason'],'legacy_ambiguous')

    def test_failed_queue_write_rolls_back_claim_and_cursor_then_retry_once(self):
        result=self.run_memory('''import sqlite3
append(records())
store.db.execute("CREATE TRIGGER reject_job BEFORE INSERT ON jobs BEGIN SELECT RAISE(ABORT,'fixture write rejected'); END")
try:store.ingest(source,'codex')
except sqlite3.IntegrityError:pass
failed={name:store.db.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ('jobs','source_events','cursors')}
store.db.execute('DROP TRIGGER reject_job')
store.ingest(source,'codex');store.backfill(source,'codex')
print(json.dumps({'failed':failed,'jobs':store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]}))
''')
        self.assertEqual(result['failed'],{'jobs':0,'source_events':0,'cursors':0})
        self.assertEqual(result['jobs'],1)

    def test_concurrent_two_connections_commit_one_queue_effect(self):
        result=self.run_memory('''from concurrent.futures import ThreadPoolExecutor
import threading
append(records())
store.db.close();barrier=threading.Barrier(2)
def consume():
    db=Store(home);barrier.wait()
    try:return db.ingest(source,'codex')
    finally:db.db.close()
with ThreadPoolExecutor(max_workers=2) as pool:counts=list(pool.map(lambda _:consume(),range(2)))
store=Store(home)
print(json.dumps({'counts':counts,'jobs':store.db.execute('SELECT count(*) FROM jobs').fetchone()[0],'claims':store.db.execute('SELECT count(*) FROM source_events').fetchone()[0]}))
''')
        self.assertEqual(sum(result['counts']),1)
        self.assertEqual(result['jobs'],1)
        self.assertEqual(result['claims'],1)

    def test_same_import_revision_shortened_or_extended_is_atomic_conflict(self):
        result=self.run_memory('''from history_import import enqueue
from event_identity import identity_status
full='alpha '*1300+'\\n\\n'+'bravo '*1300
enqueue(store,'selected.md',workspace,'revision1',full,'2026-10-23T03:00:00Z','document')
before=store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]
errors=[]
for changed in ('alpha '*20,full+'\\n\\n'+'charlie '*1200):
    try:enqueue(store,'selected.md',workspace,'revision1',changed,'2026-10-23T03:00:00Z','document')
    except ValueError as exc:errors.append(str(exc))
print(json.dumps({'before':before,'after':store.db.execute('SELECT count(*) FROM jobs').fetchone()[0],'errors':errors,'report':identity_status(store.db)}))
''')
        self.assertEqual(result['after'],result['before'])
        self.assertEqual(len(result['errors']),2)
        self.assertEqual(result['report']['conflicts_total'],2)

    def test_import_legacy_parts_preserve_effect_and_detect_missing_tail(self):
        result=self.run_memory('''from history_import import enqueue
from listener import digest
text='legacy imported paragraph'
payload={'user':'以下是明确导入的历史资料，仅作来源数据。','final':text,'cwd':str(workspace),'kind':'document','marker':'revision1','offset':0,'source':'selected.md','occurred_at':'2026-10-23T03:00:00Z'}
old_id=digest('import:document:revision1:0')
with store.db:store.db.execute('INSERT INTO jobs(id,source,payload,state,created,available) VALUES(?,?,?,?,?,?)',(old_id,'selected.md',json.dumps(payload),'saved',0,0))
count=enqueue(store,'selected.md',workspace,'revision1',text,'2026-10-23T03:00:00Z','document')
from event_identity import identity_status
print(json.dumps({'inserted':count,'jobs':store.db.execute('SELECT count(*) FROM jobs').fetchone()[0],'report':identity_status(store.db),'old_id':old_id}))
''')
        self.assertEqual(result['inserted'],0)
        self.assertEqual(result['jobs'],1)
        self.assertTrue(any(e['job_id']==result['old_id'] for e in result['report']['entries']))

    def test_legacy_import_cannot_add_or_drop_tail_before_container_exists(self):
        result=self.run_memory('''from history_import import enqueue
from listener import digest
for i in range(2):
    payload={'user':'以下是明确导入的历史资料，仅作来源数据。','final':('alpha' if i==0 else 'bravo')*1500,'cwd':str(workspace),'kind':'document','marker':'revision1','offset':i}
    with store.db:store.db.execute('INSERT INTO jobs(id,source,payload,state,created,available) VALUES(?,?,?,?,?,?)',(digest('import:document:revision1:'+str(i)),'selected.md',json.dumps(payload),'saved',0,0))
errors=[]
for text in ('alpha'*100, 'alpha'*1500+'\\n\\n'+'bravo'*1500+'\\n\\n'+'charlie'*1500):
    try:enqueue(store,'selected.md',workspace,'revision1',text,'2026-10-23T03:00:00Z','document')
    except ValueError as exc:errors.append(str(exc))
print(json.dumps({'errors':errors,'count':store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]}))
''')
        self.assertEqual(result['count'],2)
        self.assertEqual(len(result['errors']),2)

    def test_workspace_namespace_and_structured_fields_do_not_collide(self):
        result=self.run_memory('''append(records('bc',session='a')+records('c',session='ab'))
store.ingest(source,'codex')
other=home/'other-worktree';other.mkdir()
rows=records('bc',session='a');rows[0]['payload']['cwd']=str(other)
append(rows);store.ingest(source,'codex')
append(records('bc',session='a',kind='claude'));store.ingest(source,'claude')
print(json.dumps({'jobs':store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]}))
''')
        self.assertEqual(result['jobs'],4)

    def test_claude_null_message_id_uses_native_uuid(self):
        result=self.run_memory('''rows=records('uuid1',kind='claude')+records('uuid2',kind='claude')
for row in rows:row['message']['id']=None
append(rows);store.ingest(source,'claude')
print(json.dumps({'jobs':store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]}))
''')
        self.assertEqual(result['jobs'],2)

    def test_import_group_queue_failure_keeps_no_partial_claims(self):
        result=self.run_memory('''from history_import import enqueue
import sqlite3
store.db.execute("CREATE TRIGGER reject_part BEFORE INSERT ON jobs WHEN json_extract(NEW.payload,'$.offset')=1 BEGIN SELECT RAISE(ABORT,'fixture second part failed'); END")
try:enqueue(store,'selected.md',workspace,'revision1','alpha '*1300+'\\n\\n'+'bravo '*1300,'2026-10-23T03:00:00Z','document')
except sqlite3.IntegrityError:pass
print(json.dumps({name:store.db.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ('jobs','source_events','source_event_aliases')}))
''')
        self.assertEqual(result,{'jobs':0,'source_events':0,'source_event_aliases':0})

    def test_readonly_local_cli_exposes_conflict_and_exact_event_id(self):
        result=self.run_memory('''append(records(final='original final'))
store.ingest(source,'codex');append(records(final='changed final'));store.ingest(source,'codex')
from event_identity import identity_status
event_id=identity_status(store.db)['entries'][0]['event']['id']
import os,subprocess
env=os.environ.copy();env['PYTHONPATH']=os.pathsep.join([str(Path.cwd()),sys.argv[2]])
child=subprocess.run([sys.executable,str(Path(sys.argv[1])/'listener.py'),'events','--home',str(home),'--event-id',event_id,'--limit','1'],env=env,capture_output=True,text=True,encoding='utf-8',timeout=15)
if child.returncode:raise RuntimeError(child.stderr)
print(child.stdout)
''')
        self.assertEqual(result['total'],1)
        self.assertEqual(result['entries'][0]['conflicts'][0]['fields'],['final'])
        self.assertFalse('changed final' in json.dumps(result))

    def test_git_commit_revision_replay_has_one_effect_and_next_commit_is_distinct(self):
        result=self.run_memory('''import subprocess
from history_import import import_git
repo=home/'repo';repo.mkdir()
def git(*args):
    subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True,timeout=15)
git('init');git('config','user.email','fixture@example.invalid');git('config','user.name','F01 fixture')
file=repo/'source.txt';file.write_text('first',encoding='utf-8')
git('add','source.txt');git('commit','-m','first source revision')
first=import_git(store,repo);duplicate=import_git(store,repo)
file.write_text('second',encoding='utf-8');git('add','source.txt');git('commit','-m','second source revision')
next_count=import_git(store,repo)
payloads=[json.loads(row[0]) for row in store.db.execute('SELECT payload FROM jobs')]
print(json.dumps({'first':first,'duplicate':duplicate,'next':next_count,'payloads':payloads}))
''')
        self.assertEqual((result['first'],result['duplicate'],result['next']),(1,0,1))
        self.assertEqual(len(result['payloads']),2)
        self.assertTrue(all(p['_event']['basis']=='explicit_source_revision' for p in result['payloads']))


if __name__=='__main__':unittest.main()
