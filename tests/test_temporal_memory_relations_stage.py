"""G02/G03 through the existing memory owner and MCP dispatch."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TemporalMemoryRelationsStage(unittest.TestCase):
    def memory(self, body):
        with tempfile.TemporaryDirectory(prefix="rail-memory-history-") as folder:
            setup = """
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path.home()/'.codex/services/engineering-memory/app'))
sys.path.insert(0, str(Path(sys.argv[1])/'integrations/engineering_memory'))
from memory_runtime import MemoryRuntime, markdown
from memory_integration import dispatch
root = Path(sys.argv[2])
vault = root/'vault'
vault.mkdir()
memory = MemoryRuntime(vault, root/'state.sqlite', use_models=False)
def retain(identity, value, occurred=None):
    claim = {'subject':'storage choice','predicate':'configuration','value':value,'quote':value,
             'source':'user','authority':True,'scope':'D:/alpha'}
    return memory.retain(identity,[claim],{'user':value},occurred)
def note(name, body, **meta):
    (vault/(name+'.md')).write_text(markdown({'title':name,'permalink':'main/'+name,**meta},body),encoding='utf-8')
def tool(name, arguments):
    return dispatch(memory,name,arguments)['structuredContent']['result']
"""
            result = subprocess.run([sys.executable, "-X", "utf8", "-c", setup + body + "\nmemory.close()\n",
                                     str(ROOT), folder], cwd=ROOT, capture_output=True, timeout=35)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
            return json.loads(result.stdout)

    def test_recall_as_of_keeps_later_correction_out_and_separates_event_time(self):
        result = self.memory("""
old = retain('first','storage first decision','2025-06-01T10:00:00+08:00')
new = retain('correction','storage corrected decision','2025-06-02')
history = tool('memory_recall',{'query':'storage','scope':'D:/alpha','as_of':old['recorded_at']})
current = tool('memory_recall',{'query':'storage','scope':'D:/alpha'})
before = tool('memory_recall',{'query':'storage','scope':'D:/alpha','as_of':'2025-06-03T00:00:00Z'})
print(json.dumps({'history':history,'current':current,'before':before,'old':old,'new':new}))
""")
        old, new = result["old"], result["new"]
        self.assertLess(old["recorded_at"], new["recorded_at"])
        historical = result["history"]["results"]
        self.assertEqual([row["source_event"] for row in historical], ["first"])
        self.assertEqual(historical[0]["recorded_at"], old["recorded_at"])
        self.assertEqual(historical[0]["time_provenance"]["occurred_at"], "2025-06-01T02:00:00+00:00")
        self.assertEqual([row["source_event"] for row in result["current"]["results"]], ["correction"])
        self.assertEqual(result["current"]["results"][0]["time_provenance"]["state"], "date_only")
        self.assertEqual(result["before"]["results"], [])

    def test_unknown_recording_times_are_excluded_without_inventing_occurrence(self):
        result = self.memory("""
recorded = retain('undated','storage has no event date')
replay = retain('undated','storage has no event date')
legacy = {'claims':[{'subject':'legacy storage','predicate':'configuration','value':'storage legacy',
                      'quote':'storage legacy','source':'user','scope':'D:/alpha'}],
          'sources':{'user':'storage legacy'},'occurred_at':'2024-08-01'}
legacy_path = memory._event_path('legacy')
legacy_path.write_text(markdown({'memory_event':'legacy','memory_payload':legacy,'scope':'D:/alpha',
                                 'date':'2024-08-01','permalink':'main/legacy'},'storage legacy'),encoding='utf-8')
note('editable','storage unversioned',scope='D:/alpha')
found = tool('memory_recall',{'query':'storage','scope':'D:/alpha','as_of':recorded['recorded_at']})
print(json.dumps({'recorded':recorded,'replay':replay,'found':found}))
""")
        self.assertTrue(result["replay"]["duplicate"])
        self.assertEqual(result["recorded"]["recorded_at"], result["replay"]["recorded_at"])
        found = result["found"]
        self.assertEqual([row["source_event"] for row in found["results"]], ["undated"])
        self.assertEqual(found["results"][0]["time_provenance"]["state"], "missing")
        self.assertIsNone(found["results"][0]["time_provenance"]["occurred_at"])
        self.assertEqual(found["temporal"]["unknown_recorded_events"], 1)
        self.assertEqual(found["temporal"]["unversioned_notes_excluded"], 1)

    def test_knowledge_page_as_of_returns_exact_prior_publication(self):
        result = self.memory("""
note('ORBIT','ORBIT stores bronze records.',scope='D:/alpha')
first = memory.page('ORBIT','ORBIT','D:/alpha',refresh=True)
note('ORBIT','ORBIT stores silver records.',scope='D:/alpha')
stale = memory.page('ORBIT','ORBIT','D:/alpha')
second = memory.page('ORBIT','ORBIT','D:/alpha',refresh=True)
historic = tool('memory_knowledge_page',{'name':'ORBIT','query':'ORBIT','scope':'D:/alpha',
                                        'as_of':first['recorded_at']})
boundary = tool('memory_knowledge_page',{'name':'ORBIT','query':'ORBIT','scope':'D:/alpha',
                                         'as_of':second['recorded_at']})
missing = tool('memory_knowledge_page',{'name':'ORBIT','query':'ORBIT','scope':'D:/alpha',
                                       'as_of':'2024-01-01T00:00:00Z'})
with memory.db:
    memory.db.execute('INSERT INTO memory_pages VALUES(?,?,?,?,?,?)',
                      (json.dumps(['LEGACY','d:/alpha']),'ORBIT','D:/alpha','unrecorded','legacy page','[]'))
legacy = tool('memory_knowledge_page',{'name':'LEGACY','query':'ORBIT','scope':'D:/alpha','as_of':second['recorded_at']})
print(json.dumps({'first':first,'second':second,'stale':stale,'historic':historic,'boundary':boundary,
                  'missing':missing,'legacy':legacy}))
""")
        self.assertIsNone(result["stale"]["content"])
        self.assertEqual(result["historic"]["version"], result["first"]["version"])
        self.assertIn("bronze", result["historic"]["content"])
        self.assertEqual(result["historic"]["superseded_at"], result["second"]["recorded_at"])
        self.assertEqual(result["boundary"]["version"], result["second"]["version"])
        self.assertIsNone(result["missing"]["content"])
        self.assertEqual(result["missing"]["status"], "not_recorded_as_of")
        self.assertIsNone(result["legacy"]["content"])
        self.assertEqual(result["legacy"]["unknown_recorded_versions"], 1)

    def test_typed_relations_show_repair_and_unresolved_conflict_with_exact_scope(self):
        result = self.memory("""
note('old-fault','linker OOM on build host',scope='D:/alpha',status='obsolete')
note('fixed','The memory flag fixes it.\\n\\n## Relations\\n- fixes [[main/old-fault]]',
     scope='D:/alpha',status='accepted')
note('root-cause','Process limit was too small.',scope='D:/alpha',status='accepted',
     relations={'causes':['old-fault.md']})
note('opposing','Another decision says flags should stay.',scope='D:/alpha',status='accepted',
     relations={'contradicts':['main/fixed']})
note('foreign','An unrelated other project.',scope='D:/beta',relations={'fixes':['main/old-fault']})
old = tool('memory_relations',{'identifier':'main/old-fault','scope':'D:/alpha'})
fixed = tool('memory_relations',{'identifier':'main/fixed','scope':'D:/alpha'})
historic = tool('memory_recall',{'query':'linker','scope':'D:/alpha','include_history':True,'graph_hops':0})
current = tool('memory_recall',{'query':'decision','scope':'D:/alpha','graph_hops':0})
print(json.dumps({'old':old,'fixed':fixed,'historic':historic,'current':current}))
""")
        edges = result["old"]["entries"]
        self.assertEqual({(row["kind"], row["source"]["permalink"]) for row in edges},
                         {("fixes", "main/fixed"), ("causes", "main/root-cause")})
        self.assertTrue(all(row["target"]["scope"] == "D:/alpha" for row in edges))
        repair = next(row for row in edges if row["kind"] == "fixes")
        self.assertEqual(repair["evidence"]["declaration"], "- fixes [[main/old-fault]]")
        self.assertEqual(repair["evidence"]["basis"], "markdown_relations")
        conflict = next(row for row in result["fixed"]["entries"] if row["kind"] == "contradicts")
        self.assertEqual(conflict["state"], "unresolved")
        self.assertEqual((conflict["source"]["status"], conflict["target"]["status"]), ("accepted", "accepted"))
        hit = next(row for row in result["historic"]["results"] if row["permalink"] == "main/old-fault")
        self.assertEqual({row["kind"] for row in hit["relations"]}, {"fixes", "causes"})
        self.assertTrue(any(row["permalink"] == "main/opposing" for row in result["current"]["results"]))

    def test_ambiguous_and_cross_scope_relations_do_not_bind_to_unrelated_notes(self):
        result = self.memory("""
note('same-a','First failure report',title='Repeated title',scope='D:/alpha')
note('same-b','Second failure report',title='Repeated title',scope='D:/alpha')
note('outside','Other task',scope='D:/beta')
note('repair','Repair report\\n\\n## Relations\\n```md\\n- fixes [[main/same-a]]\\n```',
     scope='D:/alpha',relations={'fixes':['Repeated title'],'causes':['main/outside']})
found = tool('memory_relations',{'identifier':'main/repair','scope':'D:/alpha'})
ambiguous = tool('memory_relations',{'identifier':'Repeated title','scope':'D:/alpha'})
print(json.dumps({'found':found,'ambiguous':ambiguous}))
""")
        self.assertEqual(result["ambiguous"]["status"], "ambiguous")
        self.assertEqual({(entry["kind"], entry["state"]) for entry in result["found"]["entries"]},
                         {("fixes", "target_ambiguous"), ("causes", "target_unavailable")})
        self.assertTrue(all(entry["target"] is None for entry in result["found"]["entries"]))

    def test_existing_shared_transport_carries_time_and_relations(self):
        result = self.memory("""
from mcp_proxy import Adapter
from resource_client import RemoteRuntime, receive, send, MAX_REQUEST, MAX_RESPONSE
from contextrail.query_sources import ExistingMemory, QueryBinding
import shutil, socketserver, threading, time
retained = retain('wire-event','storage observed original')
note('fault','linker fault',scope='D:/alpha')
note('repair','repair flag',scope='D:/alpha',relations={'fixes':['main/fault']})
page = memory.page('Repair','repair','D:/alpha',refresh=True)
adapter = Adapter(memory,{'vault':str(vault)},root)
class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        request = receive(self.request, MAX_REQUEST)
        if request['token'] == 'fixture-only':
            send(self.request,adapter.request(request['message']),MAX_RESPONSE)
with socketserver.TCPServer(('127.0.0.1',0),Handler) as server:
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        (root/'resource-endpoint.json').write_text(json.dumps({'port':server.server_address[1],'token':'fixture-only'}),encoding='utf-8')
        remote = RemoteRuntime(root)
        historical = dispatch(remote,'memory_knowledge_page',{'name':'Repair','query':'repair','scope':'D:/alpha',
                              'as_of':page['recorded_at']})['structuredContent']['result']
        relations = dispatch(remote,'memory_relations',{'identifier':'main/fault','scope':'D:/alpha'})['structuredContent']['result']
        recall = remote.recall('storage',scope='D:/alpha',as_of=retained['recorded_at'])
        app = root/'app'
        app.mkdir()
        shutil.copyfile(Path(sys.argv[1])/'integrations/engineering_memory/resource_client.py',app/'resource_client.py')
        binding = QueryBinding('D:/alpha','demo',root,())
        hits = ExistingMemory(binding).search('repair',3,0,time.monotonic()+5)[0]
        print(json.dumps({'historical':historical,'relations':relations,'recall':recall,'hits':hits}))
    finally:
        server.shutdown()
        thread.join()
""")
        self.assertEqual(result["historical"]["status"], "historical_version")
        self.assertEqual(result["relations"]["entries"][0]["kind"], "fixes")
        self.assertEqual(result["recall"]["results"][0]["source_event"], "wire-event")
        repair = next(hit for hit in result["hits"] if hit["id"] == "main/repair")
        self.assertEqual(repair["relations"][0]["target"]["permalink"], "main/fault")
