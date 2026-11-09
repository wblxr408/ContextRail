"""On-demand loopback host for the attributed claude-mem UI and original M/R."""
import argparse
from datetime import datetime,timezone
from hashlib import sha256
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
import json
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import parse_qs,urlsplit,quote

from .errors import RailError,InvalidRequest,Conflict,AccessDenied,Unavailable
from .models import Scope,canonical
from .native_clients import read_registry,toggle_client
from .query_sources import QueryBinding
from .store import Store

STATIC=Path(__file__).with_name('ui_static')
def now():return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
def version(value):return sha256(canonical(value).encode()).hexdigest()

class Backend:
    def __init__(self,registry,memory_root=None):
        self.path=Path(registry).resolve();value=read_registry(self.path)
        bindings=value['bindings']
        if not bindings:raise InvalidRequest('UI requires an existing task binding.')
        self.binding=bindings[0];self.root=Path(self.binding['workspace']).resolve();self.scope=Scope(**self.binding['scope'])
        if any(b['workspace']!=self.binding['workspace'] or b['scope']!=self.binding['scope'] for b in bindings):
            raise InvalidRequest('Select one exact workspace/task registry for this UI.')
        self.query=QueryBinding.load(self.binding['query_binding'],self.scope.task,self.root)
        self.vault=Path(memory_root or Path.home()/'basic-memory').resolve()
        self.guard=threading.RLock();self.note_index={};self.notes_at=0
    def store(self):
        path=Path(self.binding['store'])
        if not path.is_file():raise Unavailable('Original task database unavailable; UI will not create a replacement.')
        return Store(path)
    def tools(self,store):
        from .host import ContextTools
        return ContextTools(store,self.scope,self.binding['session'],query_binding=self.query)
    def products(self):
        with self.store() as store:return self.tools(store).call('context.deliverables',{'budget':65536,'limit':40})
    def config(self):
        raw=self.path.read_bytes();r=read_registry(self.path)
        return {'workspace':str(self.root),'task':self.scope.task,'revision':version(json.loads(raw)),
            'clients':[{k:b.get(k) for k in ('client','enabled','context_assistance','native_session_binding','context_bytes','hook_bytes')} for b in r['bindings']],
            'memory':{'source':'existing main vault and service','root':str(self.vault),'service_changed':False},
            'takes_effect':'next native Hook; MCP catalog reload belongs to the client','capabilities':{'project_toggle':True,'accounts_or_models':False,'memory_delete':False},
            'source':str(self.path),'writes_to_global_configuration':False}
    def save_config(self,body):
        if not isinstance(body,dict) or set(body)!={'client','enabled','expectedRevision'} or type(body['enabled']) is not bool:
            raise InvalidRequest('Only client, enabled and exact expectedRevision are accepted.')
        from .client_config import locked
        with self.guard,locked(self.root):
            if body['expectedRevision']!=self.config()['revision']:raise Conflict('Project settings changed; reload before saving.')
            toggle_client(self.path,body['client'],body['enabled'])
        return self.config()
    def _review(self,fingerprint):
        with self.store() as s:
            try:row=s.db.execute('SELECT checked,at FROM ui_reviews WHERE scope=? AND fingerprint=?',(self.scope.key,fingerprint)).fetchone()
            except sqlite3.OperationalError:return {'checked':False,'checkedAtEpoch':None,'available':True}
        return {'checked':bool(row['checked']) if row else False,'checkedAtEpoch':row['at']*1000 if row else None,'available':True}
    def snapshot(self):
        with self.guard:
            with self.store() as s:
                task=s.task(self.scope)
                rows=s.db.execute('SELECT sequence,kind,at,metadata FROM events WHERE scope=? ORDER BY sequence DESC LIMIT 12',(self.scope.key,)).fetchall()
            products=self.products()['items']
            stamps=[]
            for p in products:
                path=p.get('open_path')
                try:
                    stat=Path(path).stat() if path else None
                    stamps.append((p.get('record_ref'),p.get('source'),p['availability'],(stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns) if stat else None))
                except OSError:stamps.append((p.get('record_ref'),'unavailable'))
            fingerprint=version([self.scope.key,task['version'],stamps])
            rid=canonical(['task',self.scope.key,self.scope.task]);epoch=rows[0]['at']*1000 if rows else None
            task_url='/api/work-view/evidence/work-state/1'
            agents=[{'id':canonical(['agent',c,se]),'label':('Codex' if c=='codex' else 'Claude Code')+' · recorded session',
                'platformSource':c,'sessionTitle':'Saved source; process liveness not inferred','project':self.root.name,'contentSessionId':se} for c,se in self.query.native_sessions]
            request={'id':rid,'contentFingerprint':fingerprint,'title':task['objective'],'titleSource':'explicit-work-state-request',
                'original':{'text':task['objective'],'url':task_url,'atEpoch':None},'scopeUpdates':[],
                'humanCopy':{'state':'current','title':task['objective'],'summary':'原任务库的已保存状态；界面连接不代表 Agent 正在运行。','next':'核对任务来源、最新纠正与交付物。'},
                'lifecycle':None,'items':[{'id':self.scope.key,'kind':'work-state','title':task['objective'],'status':'open',
                    'nextStep':None,'lastUpdateEpoch':epoch or 0,'sourceRefs':[{'kind':'work-state','id':1}],'url':task_url}],
                'workstreams':[],'activity':[{'id':str(r['sequence']),'kind':'work-state','title':r['kind'],'detail':None,'atEpoch':r['at']*1000,'url':task_url} for r in rows],
                'agentIds':[a['id'] for a in agents],'conversationKeys':[canonical(['conversation',c,se]) for c,se in self.query.native_sessions],
                'requestedBy':'You','startedAtEpoch':None,'completedAtEpoch':None,'updatedAtEpoch':epoch,
                'review':self._review(fingerprint),'workProduct':[{'kind':p['kind'],'title':p['title'],
                    'url':p.get('open_url') or '/api/rail/product?ref='+quote(canonical(p['ref']),safe=''),'fileUrl':'/api/rail/product?ref='+quote(canonical(p['ref']),safe='') if p.get('open_path') else None,
                    'available':bool(p.get('open_path') or p.get('open_url')),'sourcePath':p.get('registered_path')} for i,p in enumerate(products)],'focusAreaId':'other'}
            return {'version':1,'generatedAt':now(),'agents':agents,'humanRequests':[request],'unlinkedWork':[],
                'source':{'kind':'original-rail-task-store','connected':True,'error':None,'lastCheckedAt':now()},'revision':fingerprint,
                'goals':None,'goalHistory':[],'focusAreas':[],'rules':[],'ruleScopes':[],'rulesLearning':None,'rulesFile':None,'suggestions':[],
                'scope':{'workspace':str(self.root),'task':self.scope.task},'taskVersion':task['version'],'model_calls':0}
    def review(self,body):
        if not isinstance(body,dict) or set(body)!={'requestId','expectedFingerprint','checked'} or type(body['checked']) is not bool:
            raise InvalidRequest('Review requires the exact displayed request version and a checked boolean.')
        with self.guard:
            snapshot=self.snapshot();r=snapshot['humanRequests'][0]
            if body['requestId']!=r['id'] or body['expectedFingerprint']!=r['contentFingerprint']:
                raise Conflict('The task or product changed; reload the exact version before marking it checked.')
            with self.store() as s:
                with s.transaction():
                    if s.task(self.scope)['version']!=snapshot['taskVersion']:raise Conflict('Task changed during review; reload before saving.')
                    s.db.execute('CREATE TABLE IF NOT EXISTS ui_reviews(scope TEXT,fingerprint TEXT,checked INTEGER,at REAL,PRIMARY KEY(scope,fingerprint))')
                    s.db.execute('INSERT INTO ui_reviews VALUES(?,?,?,?) ON CONFLICT(scope,fingerprint) DO UPDATE SET checked=excluded.checked,at=excluded.at',
                        (self.scope.key,r['contentFingerprint'],int(body['checked']),time.time()))
            return self.snapshot()
    def notes(self):
        with self.guard:
            if time.monotonic()-self.notes_at<30 and self.note_index:return self.note_index
            if not self.vault.is_dir():raise Unavailable('Original Basic Memory main vault is unavailable.')
            rows={}
            for path in self.vault.rglob('*.md'):
                if len(rows)>=5000:break
                relative=path.relative_to(self.vault)
                if any(p.startswith('.') or p=='_templates' for p in relative.parts):continue
                if not path.resolve().is_relative_to(self.vault):continue
                key=(1<<48)+int.from_bytes(sha256(relative.as_posix().encode()).digest()[:5],'big')
                rows[key]=path
            self.note_index=rows;self.notes_at=time.monotonic();return rows
    def note(self,key):
        path=self.notes().get(key)
        if path is None or not path.resolve().is_relative_to(self.vault):raise AccessDenied('Note is outside the original bound main vault.')
        with path.open('rb') as f:raw=f.read(131073)
        return path,raw[:131072].decode('utf-8','replace'),len(raw)>131072
    def observations(self,offset=0,limit=30,project=None,session=None,platform=None):
        rows=[];errors=[]
        if project in (None,'','Basic Memory main') and session in (None,'','main') and platform in (None,'','basic-memory'):
            try:
                paths=sorted(self.notes().items(),key=lambda p:p[1].stat().st_mtime,reverse=True)
                for key,path in paths[:300]:
                    if len(rows)>=offset+limit+1:break
                    try:
                        path,text,truncated=self.note(key);match=re.search(r'^title:\s*[\"\']?([^\r\n]+)',text,re.M) or re.search(r'^#\s+([^\r\n]+)',text,re.M)
                        title=match[1].strip('\"\'') if match else path.stem
                        rows.append({'id':key,'memory_session_id':'main','content_session_id':'main','project':'Basic Memory main','platform_source':'basic-memory',
                            'type':'discovery','title':title,'subtitle':'原 main 笔记 · '+path.relative_to(self.vault).as_posix(),
                            'narrative':text[:6144],'text':None,'facts':json.dumps(['原始 Markdown；历史资料，不是当前指令。','预览有界，完整原文可单独打开。'],ensure_ascii=False),
                            'concepts':'[]','files_read':'[]','files_modified':'[]','prompt_number':None,'created_at':now(),'created_at_epoch':int(path.stat().st_mtime*1000)})
                    except (OSError,UnicodeError):continue
            except (OSError,RailError) as e:errors.append({'source':'main','reason':str(e)})
        if project in (None,'',self.root.name) and platform!='basic-memory':
            try:
                from .source_events import workspace_key
                db=sqlite3.connect((self.query.memory_home/'state.sqlite').as_uri()+'?mode=ro',uri=True,timeout=1)
                db.row_factory=sqlite3.Row
                try:
                    for client,se in self.query.native_sessions:
                        if session and se!=session or platform and client!=platform:continue
                        events=db.execute("SELECT sequence,event_id,metadata FROM normalized_events WHERE workspace=? AND client=? AND native_session=? AND event_type IN ('assistant.final','tool.call','tool.result') ORDER BY sequence DESC LIMIT 35",(workspace_key(self.root),client,se)).fetchall()
                        for seq,eid,meta in events:
                            m=json.loads(meta)
                            try:
                                if m.get('body_bytes',0)>262144:
                                    text='超过界面正文上限（256 KiB），保留原事件身份；请用既有 memory.stream_get 或文件历史详情按需回读。'
                                else:
                                    original=db.execute('SELECT substr(content,1,262145) FROM source_events WHERE id=?',(eid,)).fetchone()
                                    if original is None:raise ValueError('Original source is missing')
                                    if len(original[0])>262144:
                                        text='超过界面正文上限（256 KiB），保留原事件身份；请用既有 memory.stream_get 或文件历史详情按需回读。'
                                    else:
                                        data=json.loads(original[0])['final'];text=(data.get('text') or canonical(data)) if isinstance(data,dict) else str(data)
                            except (ValueError,KeyError,sqlite3.Error):text='原来源正文不可用，保留事件身份。'
                            rows.append({'id':seq,'memory_session_id':se,'content_session_id':se,'project':self.root.name,'platform_source':client,'type':'discovery',
                                'title':m['event_type']+' · '+se[:8],'subtitle':'原 normalized_events · '+eid,'narrative':text[:6144],'text':None,
                                'facts':'[]','concepts':'[]','files_read':'[]','files_modified':'[]','prompt_number':None,'created_at':m.get('observed_at'),
                                'created_at_epoch':self._epoch(m.get('observed_at'))})
                finally:db.close()
            except (OSError,sqlite3.Error,AttributeError):errors.append({'source':'native events','reason':'Original listener source unavailable'})
        rows.sort(key=lambda r:r['created_at_epoch'],reverse=True)
        return {'items':rows[offset:offset+limit],'hasMore':len(rows)>offset+limit,'source_errors':errors,'coverage':'bounded original main files and explicitly bound native sessions','model_calls':0}
    @staticmethod
    def _epoch(value):
        if isinstance(value,(float,int)):return int(value*1000)
        try:return int(datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()*1000)
        except (ValueError,TypeError,AttributeError):return 0
    def sessions(self,offset,limit,project=None):
        result=[];errors=[]
        if project in (None,'','Basic Memory main'):
            try:result.append({'platform_source':'basic-memory','content_session_id':'main','project':'Basic Memory main','custom_title':'原 Basic Memory main','started_at_epoch':0,'item_count':len(self.notes())})
            except (OSError,RailError):errors.append({'source':'main','reason':'Original main vault unavailable'})
        if project in (None,'',self.root.name):
            from .source_events import workspace_key
            try:
                with sqlite3.connect((self.query.memory_home/'state.sqlite').as_uri()+'?mode=ro',uri=True,timeout=1) as db:
                    for c,se in self.query.native_sessions:
                        count=db.execute('SELECT count(*) FROM normalized_events WHERE workspace=? AND client=? AND native_session=?',(workspace_key(self.root),c,se)).fetchone()[0]
                        result.append({'platform_source':c,'content_session_id':se,'project':self.root.name,'custom_title':'已绑定原生会话 '+se[:8],'started_at_epoch':0,'item_count':count})
            except (OSError,sqlite3.Error,AttributeError):errors.append({'source':'native events','reason':'Original listener source unavailable'})
        return {'sessions':result[offset:offset+limit],'hasMore':len(result)>offset+limit,'source_errors':errors}
    def task(self):
        with self.store() as s:return {'task':s.task(self.scope),'scope':self.binding['scope'],'authority':'read-only projection; no lease change'}
    def status(self):
        with self.store() as s:return self.tools(s).call('context.status',{'budget':24000})
    def history(self,file):
        with self.store() as s:return self.tools(s).call('context.file_history',{'file':file,'budget':24000})

class Server(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True
    def __init__(self,backend,port):
        self.backend=backend;self.last_request=time.monotonic();self.active_streams=0
        super().__init__(('127.0.0.1',port),Handler)

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def send(self,status,value,kind='application/json',headers=None):
        data=canonical(value).encode() if kind=='application/json' else value
        self.send_response(status);self.send_header('Content-Type',kind+'; charset=utf-8' if kind.startswith('text/') or kind=='application/json' else kind)
        self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cross-Origin-Resource-Policy','same-origin')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        for k,v in (headers or {}).items():self.send_header(k,v)
        self.end_headers();self.wfile.write(data)
    def validate(self,write=False):
        expected='127.0.0.1:'+str(self.server.server_port)
        if self.headers.get('Host')!=expected:raise AccessDenied('UI only accepts its exact loopback Host.')
        if self.headers.get('X-Forwarded-Host') or self.headers.get('Forwarded'):raise AccessDenied('Forwarded UI requests are not accepted.')
        if write and (self.headers.get('Origin')!='http://'+expected or self.headers.get_content_type()!='application/json'):
            raise AccessDenied('Changes require same-origin JSON from the local page.')
        self.server.last_request=time.monotonic()
    def dispatch(self,write):
        self.validate(write);u=urlsplit(self.path);q=parse_qs(u.query);path=u.path;b=self.server.backend
        def number(name,default):
            value=int(q.get(name,[default])[0])
            if value<0 or value>10000:raise InvalidRequest('Page range is outside the UI bound.')
            return value
        if write:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=32768:raise InvalidRequest('UI request must fit 32 KiB.')
            body=json.loads(self.rfile.read(size))
            if path=='/api/rail/config':return self.send(200,b.save_config(body))
            if path=='/api/work-view/review':return self.send(200,b.review(body))
            if path=='/api/rail/stop':
                self.send(200,{'stopping':True});threading.Thread(target=self.server.shutdown,daemon=True).start();return
            if path=='/api/work-view/usage':return self.send(200,{'accepted':False,'telemetry':False})
            raise AccessDenied('This control is not enabled for the original local sources.')
        if path in ('/api/work-view/events','/stream'):return self.stream(path)
        if path=='/api/work-view':return self.send(200,b.snapshot())
        if path=='/api/rail/config':return self.send(200,b.config())
        if path in {'/api/bridge/bundles','/api/bridge/bundle','/api/bridge/source'}:
            from .bridge_panel import BridgePanel
            panel=BridgePanel(b.root)
            if path.endswith('/bundles'):return self.send(200,panel.listing())
            selected=q.get('bundle',[''])[0]
            if path.endswith('/source'):return self.send(200,panel.source(selected,q.get('file',[''])[0]))
            return self.send(200,panel.detail(selected))
        if path=='/api/rail/status':return self.send(200,b.status())
        if path=='/api/rail/products':return self.send(200,b.products())
        if path=='/api/rail/history':return self.send(200,b.history(q.get('file',[''])[0]))
        if path=='/api/rail/history-detail':
            with b.store() as store:return self.send(200,b.tools(store).call('context.history_details',{'refs':[json.loads(q['ref'][0])],'budget':24000}))
        if path=='/api/rail/product':
            if set(q)!={'ref'}:raise InvalidRequest('Open only an exact registered product identity.')
            selected=json.loads(q['ref'][0]);products=b.products()['items']
            item=next((p for p in products if p['ref']==selected),None)
            if item is None:raise Conflict('The exact registered product is not in the current bounded page; no other product was substituted.')
            if not item.get('open_path'):raise Unavailable('This product has no currently readable registered local file.')
            file=Path(item['open_path'])
            if file.stat().st_size>32*1024*1024:raise InvalidRequest('Use the original local path for files larger than 32 MiB.')
            data=file.read_bytes();kind='text/plain' if file.suffix in ('.md','.txt','.csv','.json') else 'application/octet-stream'
            return self.send(200,data,kind,{'Content-Disposition':"inline" if kind=='text/plain' else 'attachment'})
        if path=='/api/rail/note':
            if set(q)!={'id'}:raise InvalidRequest('Supply an exact original note id.')
            p,text,truncated=b.note(int(q['id'][0]));return self.send(200,text.encode(),'text/plain')
        if path=='/api/observations':return self.send(200,b.observations(number('offset',0),min(number('limit',30),60),q.get('project',[None])[0],q.get('contentSessionId',[None])[0],q.get('platformSource',[None])[0]))
        if path in ('/api/summaries','/api/prompts'):return self.send(200,{'items':[],'hasMore':False,'reason':'No synthetic summaries or prompt copies were created.'})
        if path=='/api/projects':return self.send(200,{'projects':[b.root.name,'Basic Memory main'],'sources':['basic-memory','codex','claude'],'projectsBySource':{}})
        if path=='/api/sessions':return self.send(200,b.sessions(number('offset',0),min(number('limit',30),60),q.get('project',[None])[0]))
        if path=='/api/processing-status':return self.send(200,{'isProcessing':False,'queueDepth':0,'scope':'task projections; live model processing not inferred'})
        if path in ('/api/work-view/focus-shares','/api/work-view/focus/shares'):return self.send(200,{'shares':[]})
        if path=='/api/settings':return self.send(200,{'CONTEXTRAIL_LOCAL_UI':True,'CLAUDE_MEM_TEAM_ID':'','CLAUDE_MEM_CONTEXT_OBSERVATIONS':'20','CLAUDE_MEM_OBSERVER_PROVIDER':'existing-main','capabilities':b.config()['capabilities']})
        if path.startswith('/api/work-view/evidence/'):return self.send(200,b.task())
        if path=='/api/context/preview':
            with b.store() as store:
                from .bridge import BridgeEvidence
                bridge=BridgeEvidence(store,b.scope,b.binding['session']);snapshot=b.query.snapshot or b.binding['base_snapshot']
                packet=bridge.packet(snapshot,budget=20000,intent_details=False)
            return self.send(200,{'context':canonical(packet),'preview':canonical(packet),'view':'new preview not historical model input'})
        return self.static(path)
    def static(self,path):
        if path in ('/','/work-view','/work-view/'):relative='work-view/index.html'
        elif path in ('/viewer','/viewer/'):relative='viewer.html'
        elif path in ('/rail','/rail/'):relative='rail.html'
        elif path in ('/bridge','/bridge/'):relative='bridge.html'
        else:relative=path.lstrip('/')
        target=(STATIC/relative).resolve()
        if not target.is_relative_to(STATIC.resolve()):raise AccessDenied('Static path escapes the installed UI.')
        try:data=target.read_bytes()
        except FileNotFoundError:return self.send(404,{'error':'Local UI resource not found.'})
        return self.send(200,data,mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
    def stream(self,path):
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
        self.server.active_streams+=1
        try:
            while True:
                try:
                    if path.endswith('/events'):data=self.server.backend.snapshot()
                    else:
                        observed=self.server.backend.observations(limit=10)
                        data={'type':'initial_load','source_errors':observed['source_errors'],'projects':[self.server.backend.root.name,'Basic Memory main'],'isProcessing':False,'queueDepth':0}
                    event='work-view' if path.endswith('/events') else None
                except (RailError,OSError,sqlite3.Error):
                    data={'source':{'kind':'original-rail-task-store','connected':False,'error':'Original source read failed','lastCheckedAt':None}}
                    event='heartbeat'
                message=('event: '+event+'\n' if event else '')+'data: '+canonical(data)+'\n\n'
                self.wfile.write(message.encode());self.wfile.flush()
                for _ in range(15):
                    if getattr(self.server,'stopping',False):return
                    time.sleep(1)
        except (BrokenPipeError,ConnectionError,OSError):pass
        finally:self.server.active_streams-=1;self.server.last_request=time.monotonic();self.close_connection=True
    def do_GET(self):self.handle_request(False)
    def do_POST(self):self.handle_request(True)
    def do_DELETE(self):self.send(403,{'error':'Original memory and task sources are not deleted by this UI.'})
    def do_OPTIONS(self):self.send(403,{'error':'Cross-origin UI access is not enabled.'})
    def handle_request(self,write):
        try:self.dispatch(write)
        except (ValueError,TypeError,KeyError) as e:self.send(400,{'error':'Invalid UI request.'})
        except RailError as e:self.send({'conflict':409,'access_denied':403,'unavailable':503}.get(e.code,400),{'error':str(e),'code':e.code})
        except (OSError,sqlite3.Error):self.send(503,{'error':'Original source is unavailable; existing state was preserved.'})

def make_server(registry,*,port=0,memory_root=None):return Server(Backend(registry,memory_root),port)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--registry',type=Path,required=True);p.add_argument('--port',type=int,default=37778)
    p.add_argument('--memory-root',type=Path);p.add_argument('--idle-timeout',type=int,default=600);p.add_argument('--open',action='store_true')
    p.add_argument('--page',choices=('/','/rail','/bridge','/viewer'),default='/');args=p.parse_args()
    if not 0<=args.port<=65535 or args.idle_timeout<30:p.error('Choose a port and an idle timeout of at least 30 seconds.')
    backend=Backend(args.registry,args.memory_root);runtime=backend.root/'.context-rail/ui-runtime.json'
    try:
        prior=json.loads(runtime.read_bytes())
        from urllib.request import urlopen
        with urlopen('http://127.0.0.1:'+str(prior['port'])+'/api/rail/config',timeout=2) as response:data=json.load(response)
        if data['workspace']==str(backend.root) and data['task']==backend.scope.task:
            url='http://127.0.0.1:'+str(prior['port'])+args.page
            if args.open:__import__('webbrowser').open(url)
            print(url,flush=True);return
    except (OSError,ValueError,KeyError):pass
    server=Server(backend,args.port);url='http://127.0.0.1:'+str(server.server_port)+'/'
    runtime.write_text(canonical({'pid':os.getpid(),'port':server.server_port,'workspace':str(backend.root),'url':url}),encoding='utf-8')
    def idle():
        while not getattr(server,'stopping',False):
            time.sleep(5)
            if server.active_streams==0 and time.monotonic()-server.last_request>args.idle_timeout:
                server.stopping=True;server.shutdown();return
    threading.Thread(target=idle,daemon=True).start()
    if args.open:__import__('webbrowser').open(url.rstrip('/')+args.page)
    print(url,flush=True)
    try:server.serve_forever()
    finally:server.stopping=True;server.server_close()
if __name__=='__main__':main()
