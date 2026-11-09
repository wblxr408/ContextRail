"""Current desktop Bridge viewing journey over real files and HTTP."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from contextrail.ui_server import Server


class BridgePanelTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.bundles=self.root/'.context-rail/bridge'
        self.server=Server(SimpleNamespace(root=self.root),0)
        thread=threading.Thread(target=self.server.serve_forever,daemon=True);thread.start()
        def stop():self.server.shutdown();self.server.server_close();thread.join()
        self.addCleanup(stop);self.base='http://127.0.0.1:'+str(self.server.server_port)

    def bundle(self,revision,statement):
        path=self.bundles/'demo'/revision;path.mkdir(parents=True,exist_ok=True)
        value={'schema':1,'task_id':'demo','snapshot_id':'demo/'+revision,'requirements':[
            {'id':'R1','revision':1,'kind':'goal','scope':'global','status':'active','always_load':True,
             'statement':statement,'source':{'path':'user.md','quote':statement,'locator':'用户消息'},'progress':'pending','acceptance':'输出文档','evidence':[]},
            {'id':'R0','revision':1,'kind':'goal','scope':'global','status':'superseded','superseded_by':'R1',
             'statement':'旧目标','source':{'path':'user.md','quote':'旧目标'},'progress':'pending','evidence':[]}]}
        (path/'requirements.json').write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
        (path/'user.md').write_text(statement+'\n旧目标\n',encoding='utf-8')
        (path/'STATE.md').write_text('下一步：继续编写文档。',encoding='utf-8')
        return path

    def get(self,route,**query):
        try:
            with urlopen(self.base+route+('?' + urlencode(query) if query else '')) as response:return response.status,json.load(response)
        except HTTPError as error:return error.code,json.load(error)

    def test_versions_and_effective_goal_are_viewable_without_task_claim(self):
        old=self.bundle('revision-1','先生成旧文档');self.bundle('revision-2','最终只输出中文文档')
        _,listing=self.get('/api/bridge/bundles')
        self.assertEqual({r['snapshot_id'] for r in listing.get('items',[])},{'demo/revision-1','demo/revision-2'},'desktop has no Bridge version browser')
        _,detail=self.get('/api/bridge/bundle',bundle='demo/revision-2')
        self.assertEqual(detail['goals'],['最终只输出中文文档']);self.assertEqual(detail['active_count'],1)
        self.assertEqual(len(detail['requirements']),2);self.assertEqual(detail['authority'],'saved_history_not_current_instructions')
        _,original=self.get('/api/bridge/source',bundle='demo/revision-1',file='user.md')
        self.assertIn('先生成旧文档',original['text'])
        self.assertEqual(json.loads((old/'requirements.json').read_text(encoding='utf-8'))['requirements'][0]['progress'],'pending')

    def test_source_reader_is_bounded_and_rejects_unlisted_files_and_escape(self):
        path=self.bundle('revision-1','继续任务');(path/'private.env').write_text('DO-NOT-EXPOSE')
        (path/'user.md').write_text('详细🙂'*30000,encoding='utf-8')
        _,source=self.get('/api/bridge/source',bundle='demo/revision-1',file='user.md')
        self.assertTrue(source['truncated']);self.assertIn('详细',source['text'])
        status,denied=self.get('/api/bridge/source',bundle='demo/revision-1',file='private.env')
        self.assertEqual(status,403);self.assertNotIn('DO-NOT-EXPOSE',json.dumps(denied))
        status,_=self.get('/api/bridge/source',bundle='demo/revision-1',file='../../../../private.env')
        self.assertEqual(status,403)

    def test_empty_and_malformed_records_keep_valid_history_available(self):
        _,empty=self.get('/api/bridge/bundles');self.assertEqual(empty.get('items'),[])
        self.bundle('revision-1','继续任务')
        bad=self.bundles/'broken';bad.mkdir();(bad/'requirements.json').write_text('{broken')
        _,listing=self.get('/api/bridge/bundles');self.assertEqual(len(listing['items']),1);self.assertEqual(len(listing['errors']),1)

    def test_changed_version_is_reloaded_and_missing_selection_is_not_substituted(self):
        path=self.bundle('revision-1','先做A')
        _,before=self.get('/api/bridge/bundle',bundle='demo/revision-1');self.assertEqual(before['goals'],['先做A'])
        self.bundle('revision-1','现在做B')
        _,after=self.get('/api/bridge/bundle',bundle='demo/revision-1');self.assertEqual(after['goals'],['现在做B'])
        (path/'requirements.json').unlink()
        status,_=self.get('/api/bridge/bundle',bundle='demo/revision-1');self.assertNotEqual(status,200)

    def test_original_source_secrets_are_redacted_in_view(self):
        path=self.bundle('revision-1','继续任务');secret='sk-'+'B'*40
        (path/'user.md').write_text('api_key='+secret,encoding='utf-8')
        _,source=self.get('/api/bridge/source',bundle='demo/revision-1',file='user.md')
        self.assertNotIn(secret,source['text']);self.assertIn('REDACTED',source['text'])
