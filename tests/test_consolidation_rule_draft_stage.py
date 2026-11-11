"""I06/I07 current consumer acceptance through the original memory owner."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests import test_experience_review_stage as experience_fixture
from contextrail.learning_origin import project_candidate

ROOT = Path(__file__).resolve().parents[1]


class ConsolidationAndRuleDraftTests(unittest.TestCase):
    def test_consolidation_round_trip_retains_exact_differences_and_relations(self):
        script = r'''
import json, shutil, socketserver, subprocess, sys, threading
from pathlib import Path
root = Path(sys.argv[2])
workspace = root / 'workspace'
workspace.mkdir()
vault = root / 'vault'
vault.mkdir()
overlay = Path(sys.argv[1]) / 'integrations/engineering_memory'
sys.path[:0] = [str(overlay), str(Path.home()/'.codex/services/engineering-memory/app')]
from memory_runtime import MemoryRuntime, markdown
from mcp_proxy import Adapter
from resource_client import receive, send, MAX_REQUEST, MAX_RESPONSE
memory = MemoryRuntime(vault, root/'state.sqlite', use_models=False)
def note(name, body, **meta):
    path = vault/(name+'.md')
    path.write_text(markdown({'title':name,'permalink':'main/'+name,'scope':str(workspace),**meta},body),encoding='utf-8')
    return path
common = 'Build pipeline checks the revision before starting compilation.\nCollect the original console output and preserve dependency order.\n'
a = note('compile-one', common + 'Timeout is 250 ms.\n', status='accepted')
b = note('compile-two', common + 'User correction: timeout is 500 ms.\n',
         relations={'contradicts':['main/compile-one']})
note('independent', 'Gardening requires healthy soil and sunlight.', status='accepted')
note('foreign', common + 'Use this only in another workspace.', scope=str(root/'foreign'))
adapter = Adapter(memory, {'vault':str(vault)}, root)
class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        request=receive(self.request,MAX_REQUEST)
        if request['token']=='fixture-only':
            try: response=adapter.request(request['message'])
            except ValueError as exc:response={'error':{'message':str(exc)}}
            send(self.request,response,MAX_RESPONSE)
app=root/'app'
app.mkdir()
shutil.copyfile(overlay/'resource_client.py',app/'resource_client.py')
binding=root/'query.json'
binding.write_text(json.dumps({'schema':1,'task':'consolidation','workspace':str(workspace),'memory_home':str(root)}),encoding='utf-8')
base=['--workspace',str(workspace),'--task','consolidation','--store',str(root/'rail.sqlite'),
      '--session','owner','--response-bytes','32768']
def cli(command, args, request=None):
    result=subprocess.run([sys.executable,'-m','contextrail',command,*base,*args],
        input=json.dumps(request) if request is not None else None, capture_output=True,
        text=True,encoding='utf-8',timeout=15)
    if result.returncode: raise RuntimeError(result.stderr)
    return json.loads(result.stdout)
cli('project-init',['--objective','Review explicit project notes.'])
with socketserver.TCPServer(('127.0.0.1',0),Handler) as server:
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        (root/'resource-endpoint.json').write_text(json.dumps({'port':server.server_address[1],'token':'fixture-only'}),encoding='utf-8')
        args=['--query-binding',str(binding),'--epoch','0']
        selected=['main/compile-one','main/compile-two','main/independent','main/foreign']
        def review(**request):
            return cli('memory-consolidation',args,request)
        proposed=review(identifiers=selected)
        by_query=review(query='Build pipeline revision')
        group,=proposed['groups']
        group_args={'group_id':group['id'],'revision':group['revision']}
        approved=review(action='apply',identifiers=selected,representative='main/compile-two',**group_args)
        retry=review(action='apply',identifiers=selected,representative='main/compile-two',**group_args)
        collapsed=review(identifiers=selected)
        member=next(item for item in group['members'] if item['permalink']=='main/compile-two')
        reopened=review(action='open',identifier=member['permalink'],note_version=member['version'])
        originals_before=[a.read_text(encoding='utf-8'),b.read_text(encoding='utf-8')]
        undone=review(action='undo',**group_args)
        expanded=review(identifiers=selected)
        originals_after=[a.read_text(encoding='utf-8'),b.read_text(encoding='utf-8')]
        review(action='apply',identifiers=selected,**group_args)
        note('compile-two',common+'User correction: timeout is 750 ms.\n',relations={'contradicts':['main/compile-one']})
        changed=review(identifiers=selected)
        stale=review(action='apply',identifiers=selected,**group_args)
        late_undo=review(action='undo',**group_args)
        print(json.dumps(dict(proposed=proposed,by_query=by_query,approved=approved,retry=retry,collapsed=collapsed,expanded=expanded,
                             reopened=reopened,undone=undone,unchanged=originals_before==originals_after,
                             changed=changed,stale=stale,late_undo=late_undo)))
    finally:
        server.shutdown()
        thread.join()
memory.close()
'''
        with tempfile.TemporaryDirectory() as folder:
            run = subprocess.run([sys.executable, "-X", "utf8", "-c", script, str(ROOT), folder],
                                 capture_output=True, text=True, encoding="utf-8", timeout=55)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        group, = result["proposed"]["groups"]
        self.assertEqual({item["permalink"] for item in group["members"]},
                         {"main/compile-one", "main/compile-two"})
        self.assertEqual(group["representative"], "main/compile-one")
        self.assertEqual(result["by_query"]["groups"][0]["id"], group["id"])
        self.assertEqual(len(result["proposed"]["display"]), 3)
        self.assertEqual(result["proposed"]["excluded"],
                         [{"identifier": "main/foreign", "reason": "unavailable"}])
        differences = [line["text"] for member in group["members"] for line in member["differences"]]
        self.assertIn("Timeout is 250 ms.", differences)
        self.assertIn("User correction: timeout is 500 ms.", differences)
        self.assertTrue(any(edge["kind"] == "contradicts" for edge in group["relations"]))
        self.assertEqual(result["approved"], result["retry"])
        self.assertEqual(result["approved"]["representative"], "main/compile-two")
        self.assertEqual([row["kind"] for row in result["collapsed"]["display"]].count("consolidated"), 1)
        display = next(row for row in result["collapsed"]["display"] if row["kind"] == "consolidated")
        self.assertEqual(display["representative"], "main/compile-two")
        self.assertTrue(display["has_declared_contradiction"])
        self.assertEqual(len(result["collapsed"]["display"]), 2)
        self.assertIn("User correction: timeout is 500 ms.", result["reopened"]["content"])
        self.assertTrue(result["unchanged"])
        self.assertEqual(result["undone"]["state"], "undone")
        self.assertEqual(len(result["expanded"]["display"]), 3)
        self.assertEqual(len(result["changed"]["display"]), 3)
        self.assertEqual(result["stale"]["status"], "source_changed")
        self.assertEqual(result["late_undo"]["state"], "undone")
        captured = project_candidate({"user": "", "final": json.dumps(result["collapsed"])})
        self.assertFalse(captured["_origin"]["eligible_for_learning"])
        self.assertEqual(captured["final"], "")

    def draft_fixture(self):
        case = experience_fixture.ExperienceReviewTests(
            "test_review_requires_independent_sessions_and_rejection_requires_current_owner")
        case.setUp()
        self.addCleanup(case.doCleanups)
        for day, session in enumerate(("one", "two"), 1):
            case.capture(session, [("python build.py", 0)], day)
        candidate, = case.review()["candidates"]
        return case, candidate

    def draft(self, case, candidate, *extra, expected=0):
        return case.run_cli("experience-rule-draft", "--candidate", candidate["id"],
                            "--evidence-version", candidate["evidence_version"],
                            "--target", "AGENTS.md", "--rule", "Run python build.py after approval.",
                            *extra, expected=expected)

    def test_rule_draft_is_scoped_source_linked_and_reversible(self):
        case, candidate = self.draft_fixture()
        current = "# Project instructions\n\n- Never run python build.py before review.\n"
        (case.workspace / "AGENTS.md").write_text(current, encoding="utf-8", newline="")
        draft = self.draft(case, candidate)
        self.assertEqual(draft["workspace"], str(case.workspace))
        self.assertEqual(draft["source"]["evidence_version"], candidate["evidence_version"])
        self.assertEqual({item["native_session"] for item in draft["source"]["evidence"]}, {"one", "two"})
        self.assertEqual(draft["conflicts"][0]["path"], "AGENTS.md")
        self.assertEqual(draft["conflicts"][0]["kind"], "possible_conflict")
        captured = project_candidate({"user": "", "final": json.dumps(draft)})
        self.assertFalse(captured["_origin"]["eligible_for_learning"])
        self.assertEqual(captured["final"], "")
        self.assertEqual(set(captured["_origin"]["derived_from"]),
                         {ref["event_id"] for item in candidate["evidence"] for ref in item["refs"]})
        self.assertEqual((case.workspace / "AGENTS.md").read_text(encoding="utf-8"), current)
        fixture = case.root / "patch-review"
        fixture.mkdir()
        shutil.copyfile(case.workspace / "AGENTS.md", fixture / "AGENTS.md")
        for key in ("diff", "reverse_diff"):
            run = subprocess.run(["git", "apply", "-"], input=draft[key].encode("utf-8"),
                                 cwd=fixture, capture_output=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace"))
            revised = (fixture / "AGENTS.md").read_text(encoding="utf-8")
            if key == "diff":
                self.assertIn("- Run python build.py after approval.", revised)
            else:
                self.assertEqual(revised, current)

    def test_rule_draft_respects_rejection_and_old_evidence(self):
        case, candidate = self.draft_fixture()
        (case.workspace / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
        case.reject(candidate)
        error = self.draft(case, candidate, expected=1)
        self.assertIn("rejected", error.lower())
        reconsidered = self.draft(case, candidate, "--reconsider")
        self.assertEqual(reconsidered["source"]["review_reason"], "explicit_reconsideration")
        self.assertEqual(reconsidered["source"]["last_rejection"]["reason"], "These are temporary project steps")
        case.capture("three", [("python build.py", 0)], 3)
        case.select("one", "two", "three")
        error = self.draft(case, candidate, expected=1)
        self.assertIn("evidence changed", error.lower())

    def test_nested_windows_patch_retains_line_endings_and_scans_ancestor_rules(self):
        case, candidate = self.draft_fixture()
        ancestor = ("# Project instructions\n\n" + chr(96) * 3
                    + "md\n<!-- example markup\nNever run python build.py again.\n"
                    + chr(96) * 3 + "\n\n- Never run python build.py before review.\n")
        (case.workspace / "AGENTS.md").write_text(ancestor, encoding="utf-8")
        relative = Path("子项目 notes") / "CLAUDE.md"
        target = case.workspace / relative
        target.parent.mkdir()
        original = b"# Rules\r\n\r\n- Keep another option."
        target.write_bytes(original)
        draft = self.draft(case, candidate, "--target", relative.as_posix())
        self.assertEqual([(row["path"], row["line"]) for row in draft["conflicts"]], [("AGENTS.md", 8)])
        fixture = case.root / "windows-patch"
        (fixture / relative).parent.mkdir(parents=True)
        (fixture / relative).write_bytes(original)
        for key in ("diff", "reverse_diff"):
            run = subprocess.run(["git", "apply", "-"], input=draft[key].encode("utf-8"),
                                 cwd=fixture, capture_output=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace"))
            observed = (fixture / relative).read_bytes()
            if key == "diff":
                self.assertIn(b"- Run python build.py after approval.\r\n", observed)
                self.assertNotIn(b"\n", observed.replace(b"\r\n", b""))
            else:
                self.assertEqual(observed, original)
        self.assertEqual(target.read_bytes(), original)
