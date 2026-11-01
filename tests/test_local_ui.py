"""New local UI consumer contract over actual HTTP and original task stores."""
import http.client,json,threading,unittest,sqlite3,time
from pathlib import Path
from dataclasses import asdict
from contextrail import Store,Lease
from tests import test_claude_mem_compact as compact_fixture

class LocalUI(unittest.TestCase):
    def setUp(self):
        self.f=compact_fixture.CompactAssistance();self.f.setUp();self.addCleanup(self.f.doCleanups);self.f.prepare()
        self.vault=self.f.root/'main';self.vault.mkdir();(self.vault/'lesson.md').write_text('# 已有工程记忆\n\n保留原服务。',encoding='utf-8')
        self.product=self.f.workspace/'report.md';self.product.write_text('真实交付物',encoding='utf-8')
        from contextrail.deliverables import register
        with Store(self.f.database) as store:
            source=store.put(self.f.scope,Lease('owner',0),'ui-real-source',b'Explicit host output',expected_revision=0)
            register(store,self.f.scope,Lease('owner',0),self.f.workspace,{'registration_id':'ui-real-report','source':asdict(source),
                'fields':{'deliverables':[{'kind':'document','path':str(self.product),'title':'真实结果'}]}})
            self.before=store.task(self.f.scope)
        from contextrail.ui_server import make_server
        self.server=make_server(self.f.registry,port=0,memory_root=self.vault)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close)
        self.origin='http://127.0.0.1:'+str(self.server.server_port)
    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3)
    def request(self,path,body=None,origin=None,host=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=8)
        h={'Host':host or '127.0.0.1:'+str(self.server.server_port)}
        if body is not None:h.update({'Content-Type':'application/json','Origin':origin or self.origin})
        c.request('POST' if body is not None else 'GET',path,json.dumps(body).encode() if body is not None else None,h)
        r=c.getresponse();raw=r.read();status=r.status;c.close()
        return status,json.loads(raw) if r.getheader('Content-Type','').startswith('application/json') else raw
    def test_real_task_memory_and_product_projection_do_not_change_task_authority(self):
        status,view=self.request('/api/work-view');self.assertEqual(status,200,view)
        self.assertEqual(view['humanRequests'][0]['original']['text'],self.before['objective'])
        self.assertEqual(view['humanRequests'][0]['workProduct'][0]['title'],'真实结果')
        status,notes=self.request('/api/observations?limit=20');self.assertEqual(status,200,notes)
        self.assertTrue(any('已有工程记忆' in row['title'] for row in notes['items']))
        from urllib.parse import quote
        _,products=self.request('/api/rail/products')
        status,body=self.request('/api/rail/product?ref='+quote(json.dumps(products['items'][0]['ref'])));self.assertEqual(status,200,body)
        self.assertEqual(body.decode(),'真实交付物')
        with Store(self.f.database) as s:self.assertEqual(s.task(self.f.scope),self.before)
    def test_checked_version_persists_and_new_task_version_reopens_review(self):
        _,v=self.request('/api/work-view');r=v['humanRequests'][0]
        status,v=self.request('/api/work-view/review',{'requestId':r['id'],'expectedFingerprint':r['contentFingerprint'],'checked':True})
        self.assertEqual(status,200,v);self.assertIn('humanRequests',v,'Upstream review reader consumes a snapshot directly');self.assertTrue(v['humanRequests'][0]['review']['checked'])
        _,v=self.request('/api/work-view');self.assertTrue(v['humanRequests'][0]['review']['checked'])
        with Store(self.f.database) as s:s.put(self.f.scope,Lease('owner',0),'new-user-evidence',b'New correction',expected_revision=0)
        status,_=self.request('/api/work-view/review',{'requestId':r['id'],'expectedFingerprint':r['contentFingerprint'],'checked':True})
        self.assertEqual(status,409)
        _,v=self.request('/api/work-view');self.assertFalse(v['humanRequests'][0]['review']['checked'])
    def test_project_toggle_is_version_guarded_and_other_config_is_preserved(self):
        before=json.loads(self.f.registry.read_bytes());_,cfg=self.request('/api/rail/config')
        status,saved=self.request('/api/rail/config',{'client':'codex','enabled':False,'expectedRevision':cfg['revision']})
        self.assertEqual(status,200,saved)
        after=json.loads(self.f.registry.read_bytes());self.assertFalse(after['bindings'][0]['enabled'])
        self.assertEqual(after['bindings'][1],before['bindings'][1]);self.assertEqual(after['memory'],before['memory'])
        self.assertEqual(self.request('/api/rail/config',{'client':'codex','enabled':True,'expectedRevision':cfg['revision']})[0],409)

    def test_open_product_keeps_its_exact_registration_when_list_order_changes(self):
        from urllib.parse import quote
        _,products=self.request('/api/rail/products');ref=products['items'][0]['ref']
        other=self.f.workspace/'other.md';other.write_text('另一份交付物',encoding='utf-8')
        from contextrail.deliverables import register
        with Store(self.f.database) as store:
            source=store.put(self.f.scope,Lease('owner',0),'ui-other-source',b'Another explicit result',expected_revision=0)
            register(store,self.f.scope,Lease('owner',0),self.f.workspace,{'registration_id':'ui-other-report','source':asdict(source),'fields':{'deliverables':[{'kind':'document','path':str(other),'title':'另一份结果'}]}})
        status,body=self.request('/api/rail/product?ref='+quote(json.dumps(ref)))
        self.assertEqual(status,200,body)
        self.assertEqual(body.decode(),'真实交付物')
    def test_foreign_origin_host_and_unregistered_path_are_rejected(self):
        _,cfg=self.request('/api/rail/config')
        self.assertEqual(self.request('/api/rail/config',{'client':'codex','enabled':False,'expectedRevision':cfg['revision']},origin='https://foreign.example')[0],403)
        self.assertEqual(self.request('/api/work-view',host='foreign.example')[0],403)
        self.assertEqual(self.request('/api/rail/product?path=C:/private.txt')[0],400)
        self.assertEqual(self.request('/api/rail/config',{'client':'codex','enabled':False,'expectedRevision':cfg['revision'],'model':'unrequested'})[0],400)

    def test_original_native_evidence_is_projected_without_rewriting_it(self):
        from contextrail.source_events import workspace_key
        meta={'observed_at':time.time(),'event_type':'assistant.final'}
        with sqlite3.connect(self.f.home/'state.sqlite') as db:
            db.execute('CREATE TABLE source_events(id TEXT PRIMARY KEY,content TEXT)')
            db.execute('INSERT INTO source_events VALUES(?,?)',('event-ui-1',json.dumps({'final':{'text':'原生已记录结果'}})))
            db.execute('INSERT INTO normalized_events(event_id,workspace,client,native_session,role,event_type,metadata) VALUES(?,?,?,?,?,?,?)',('event-ui-1',workspace_key(self.f.workspace),'codex','codex-native','assistant','assistant.final',json.dumps(meta)))
            db.execute('INSERT INTO source_events VALUES(?,?)',('event-ui-2',json.dumps({'final':{'text':'原生已记录结果'}})))
            db.execute('INSERT INTO normalized_events(event_id,workspace,client,native_session,role,event_type,metadata) VALUES(?,?,?,?,?,?,?)',('event-ui-2',workspace_key(self.f.workspace),'codex','codex-native','assistant','assistant.final',json.dumps(meta)))
        status,data=self.request('/api/observations?platformSource=codex&contentSessionId=codex-native')
        self.assertEqual(status,200,data)
        self.assertEqual(data['items'][0]['narrative'],'原生已记录结果')
        self.assertEqual(data['items'][0]['platform_source'],'codex')
        from urllib.parse import quote
        _,catalog=self.request('/api/sessions?project='+quote(self.f.workspace.name))
        selected=next(row for row in catalog['sessions'] if row['content_session_id']=='codex-native')
        self.assertEqual(selected['item_count'],2,'Catalog counts must come from original captured events')
        self.assertTrue(all(row['project']==self.f.workspace.name for row in catalog['sessions']))
        with sqlite3.connect(self.f.home/'state.sqlite') as db:
            db.execute('UPDATE source_events SET content=? WHERE id=?',(json.dumps({'final':{'text':'X'*350000}}),'event-ui-2'))
        status,data=self.request('/api/observations?platformSource=codex&contentSessionId=codex-native')
        self.assertEqual(status,200,data)
        self.assertIn('界面正文上限',data['items'][0]['narrative'])
        self.assertEqual(data['items'][1]['narrative'],'原生已记录结果')

if __name__=='__main__':unittest.main()
