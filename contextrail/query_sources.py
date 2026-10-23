"""Trusted source bindings and existing-memory adapters. No new service."""
from dataclasses import dataclass
import importlib.util
import json
import math
from pathlib import Path
import sqlite3
import time

from .errors import InvalidRequest
from .models import identifier
from .source_events import workspace_key
from .timeline_index import occurrence,canonical
from .indexing import _tokens


class SourceFailure(Exception):
    def __init__(self,code):self.code=code


@dataclass(frozen=True)
class QueryBinding:
    workspace:str
    task:str
    memory_home:Path|None
    native_sessions:tuple
    memory_mode:str='text'
    timeout_seconds:float=12
    snapshot:str|None=None

    @classmethod
    def parse(cls,value,task,workspace=None):
        if value is None:return None
        allowed={'schema','workspace','task','memory_home','native_sessions','memory_mode','timeout_seconds','snapshot'}
        if not isinstance(value,dict) or value.keys()-allowed or value.get('schema')!=1 or value.get('task')!=task:raise InvalidRequest('Query binding must name the exact task')
        root=value.get('workspace')
        if not isinstance(root,str) or not Path(root).is_absolute():raise InvalidRequest('Query workspace must be an absolute host binding')
        root=str(Path(root).resolve())
        if workspace is not None and workspace_key(root)!=workspace_key(str(Path(workspace).resolve())):raise InvalidRequest('Query binding workspace differs from this project')
        home=value.get('memory_home')
        if home is not None and (not isinstance(home,str) or not Path(home).is_absolute()):raise InvalidRequest('Memory home must be an absolute host binding')
        sessions=value.get('native_sessions',[])
        if not isinstance(sessions,list) or len(sessions)>4:raise InvalidRequest('Bind at most four exact native sessions')
        pairs=[]
        for selected in sessions:
            if not isinstance(selected,dict) or set(selected)!={'client','session'} or selected['client'] not in {'codex','claude'}:raise InvalidRequest('Invalid native source binding')
            identifier(selected['session']);pairs.append((selected['client'],selected['session']))
        if len(set(pairs))!=len(pairs):raise InvalidRequest('Native source bindings must be unique')
        if pairs and home is None:raise InvalidRequest('Native sources require their existing memory home')
        mode=value.get('memory_mode','text');timeout=value.get('timeout_seconds',12)
        if mode not in {'text','hybrid'} or type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=20:raise InvalidRequest('Invalid bound memory mode or timeout')
        snapshot=value.get('snapshot')
        if snapshot is not None:identifier(snapshot)
        return cls(root,task,Path(home).resolve() if home else None,tuple(pairs),mode,timeout,snapshot)

    @classmethod
    def load(cls,path,task,workspace):
        try:
            raw=Path(path).read_bytes()
            if len(raw)>32768:raise ValueError()
            return cls.parse(json.loads(raw),task,workspace)
        except (OSError,ValueError,UnicodeError):raise InvalidRequest('Query binding is unavailable or invalid') from None


def connect_events(binding,deadline):
    if binding is None or binding.memory_home is None or not binding.native_sessions:raise SourceFailure('not_configured')
    try:
        path=binding.memory_home/'state.sqlite'
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)
        db.row_factory=sqlite3.Row
        db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        db.execute('BEGIN')
        return db
    except (OSError,sqlite3.Error):raise SourceFailure('event_source_unavailable') from None


def event_selection(binding):
    clauses=[];args=[]
    for client,session in binding.native_sessions:
        clauses.append('(n.client=? AND n.native_session=?)');args.extend((client,session))
    return 'n.workspace=? AND ('+' OR '.join(clauses)+')',[workspace_key(binding.workspace),*args]


def event_revision(db,binding):
    where,args=event_selection(binding)
    row=db.execute('SELECT count(*),coalesce(max(n.sequence),0) FROM normalized_events n WHERE '+where,args).fetchone()
    return tuple(row)


def events_search(db,binding,query,limit,offset,view,since=None,until=None):
    where,args=event_selection(binding)
    terms=list(dict.fromkeys(_tokens(query)))[:16]
    if not terms:raise SourceFailure('unsupported_query_terms')
    # The deadline progress handler bounds scanning old, already processed bodies.
    # No second FTS index/store is introduced for this read facade.
    db.create_function('rail_fold',1,lambda text:str(text or '').casefold())
    body="rail_fold(json_extract(s.content,'$.final'))"
    score='+'.join('(instr('+body+',?)>0)' for _ in terms)
    joins=' FROM normalized_events n JOIN source_events s ON s.id=n.event_id WHERE '+where+' AND length(s.content)<=4194304 AND ('+' OR '.join('instr('+body+',?)>0' for _ in terms)+')'
    filtered=[*args,*terms]
    for value,op in ((since,'>='),(until,'<')):
        if value is not None:joins+=' AND julianday(json_extract(n.metadata,\'$.occurred_at\'))'+op+'julianday(?)';filtered.append(value)
    total=db.execute('SELECT count(*)'+joins,filtered).fetchone()[0]
    ordering='julianday(json_extract(n.metadata,\'$.occurred_at\')) IS NULL,julianday(json_extract(n.metadata,\'$.occurred_at\')),n.sequence'
    if view=='relevance':ordering='matched DESC,n.sequence DESC'
    rows=db.execute('SELECT n.event_id,n.metadata,s.content,('+score+') AS matched'+joins+' ORDER BY '+ordering+' LIMIT ? OFFSET ?',(*terms,*filtered,limit,offset)).fetchall()
    hits=[]
    for row in rows:
        event=json.loads(row['metadata']);content=json.loads(row['content'])['final']
        text=canonical(content);position=min((text.casefold().find(t) for t in terms if t in text.casefold()),default=0)
        preview=text[max(0,position-100):position+420]
        hits.append({'origin':'events','id':event['id'],'title':event['event_type'],'preview':preview,'preview_truncated':len(text)>len(preview),
            'task':None,'source_session':event['native_session'],'client':event['client'],
            'version':{'kind':'source_event_first_capture','value':event['id'],'state':'available'},
            'time':{'occurred_at':event.get('occurred_at'),'state':event.get('source_time_state','missing'),'basis':'native_event'},
            'ref':{'reader':'memory.stream_get','workspace':binding.workspace,'client':event['client'],'native_session':event['native_session'],'event_id':event['id']},
            'current_task_membership':False,'evidence_role':'historical_event','source_score':row['matched']})
    oversized=db.execute('SELECT count(*) FROM normalized_events n JOIN source_events s ON s.id=n.event_id WHERE '+where+' AND length(s.content)>4194304',args).fetchone()[0]
    return hits,total,{'scope':'bound_workspace_and_exact_sessions','oversized_excluded':oversized,'complete':oversized==0,'ranking':'matched_lexical_terms' if view=='relevance' else 'matching_events_by_occurrence'}


class ExistingMemory:
    def __init__(self,binding):
        self.binding=binding;self.module=None

    def invoke(self,name,args,kwargs,deadline):
        if self.binding is None or self.binding.memory_home is None:raise SourceFailure('not_configured')
        remaining=min(self.binding.timeout_seconds,deadline-time.monotonic())
        if remaining<=0:raise SourceFailure('deadline_exceeded')
        try:
            if self.module is None:
                spec=importlib.util.spec_from_file_location('rail_bound_memory_transport',self.binding.memory_home/'app/resource_client.py')
                self.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)
            response=self.module.rpc(self.binding.memory_home,{'jsonrpc':'2.0','id':1,'method':'engineering/runtime','params':{'name':name,'args':args,'kwargs':kwargs}},timeout=remaining)
            if 'error' in response or response.get('result',{}).get('isError'):raise SourceFailure('existing_memory_unavailable')
            return response['result']['structuredContent']['result']
        except SourceFailure:raise
        except (TimeoutError,ConnectionError):raise SourceFailure('memory_deadline_or_connection') from None
        except (OSError,ValueError,KeyError,TypeError,RuntimeError):raise SourceFailure('existing_memory_unavailable') from None

    def revision(self,deadline):
        value=self.invoke('query_revision',[],{},deadline)
        if not isinstance(value,dict) or not isinstance(value.get('revision'),str):raise SourceFailure('memory_revision_unavailable')
        return value['revision']

    def search(self,query,limit,offset,deadline):
        value=self.invoke('recall',[query],{'scope':self.binding.workspace,'limit':limit,'page':offset//limit+1,'mode':self.binding.memory_mode,'include_revision':True},deadline)
        if not isinstance(value,dict) or not isinstance(value.get('results'),list):raise SourceFailure('invalid_memory_result')
        hits=[]
        for note in value['results'][:limit]:
            permalink=note['permalink'];identifier(permalink)
            evidence_time=note.get('time_provenance')
            if not isinstance(evidence_time,dict):evidence_time={'occurred_at':None,'state':'unknown_precision','source_date':note.get('date'),'basis':'legacy_recall_date'}
            hits.append({'origin':'memory','id':permalink,'title':str(note.get('title',''))[:160],
                'preview':str(note.get('excerpt',''))[:520],'preview_truncated':len(str(note.get('excerpt','')))>520,
                'task':None,'source_session':note.get('source_session'),
                'version':{'kind':'note_revision','value':None,'state':'unavailable','reason':'recall_does_not_offer_exact_historical_revision'},
                'time':evidence_time,'status':note.get('status','unknown'),
                'ref':{'reader':'basic-memory.read_note','identifier':permalink,'project':'main'},
                'current_task_membership':False,'evidence_role':'retrieved_history','source_score':note.get('score')})
        return hits,value.get('total',len(hits)),{'scope':'main_workspace_and_applicable_global_history','mode':value.get('mode'),'complete':True},value.get('catalog_revision')
