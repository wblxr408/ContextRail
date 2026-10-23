"""C06/C08 bounded source federation and per-reader revision-aware reuse."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import sqlite3
import threading
import time
from uuid import uuid4

from .errors import BudgetExceeded,InvalidRequest,RailError
from .models import Ref,canonical,unicode_text
from .query_sources import QueryBinding,SourceFailure,ExistingMemory,connect_events,event_revision,events_search
from ._vendor.context_mode_unified import merge_sources

SOURCES=('task','events','memory')


class QueryState:
    def __init__(self):
        self.cache=OrderedDict();self.bytes=0;self.lock=threading.Lock();self.history=OrderedDict()
        self.handles={};self.serial=0;self.delivered=OrderedDict();self.errors=[]

    def get(self,key):
        with self.lock:
            if key not in self.cache:return None
            value,size=self.cache[key];self.cache.move_to_end(key)
            result=copy.deepcopy(value);result['cache_hit']=True
            return result

    def put(self,key,value):
        if key is None:return
        size=len(repr(key).encode())+len(canonical(value).encode())
        if size>262144:return
        with self.lock:
            if key in self.cache:self.bytes-=self.cache.pop(key)[1]
            while self.cache and (self.bytes+size>1048576 or len(self.cache)>=64):self.bytes-=self.cache.popitem(last=False)[1][1]
            self.cache[key]=(copy.deepcopy(value),size);self.bytes+=size

    def handle(self,source,value):
        with self.lock:
            key=(source,value)
            if key not in self.handles:
                self.serial+=1
                if len(self.handles)>=64:self.handles.clear()
                self.handles[key]=source+'-revision-'+str(self.serial)
            return self.handles[key]


def state_for(tools):
    if not hasattr(tools,'_query_state'):tools._query_state=QueryState()
    return tools._query_state


def bound_sources(tools):
    value=getattr(tools,'query_binding',None)
    if isinstance(value,QueryBinding):
        if value.task!=tools.scope.task:raise InvalidRequest('Query binding belongs to another task')
        return value
    return QueryBinding.parse(value,tools.scope.task)


def validate(queries,view,limits,offsets,budget,source_bytes,refresh,since,until):
    if not isinstance(queries,list) or not 1<=len(queries)<=8:raise InvalidRequest('Supply 1-8 queries')
    for query in queries:
        if not isinstance(query,str) or not query.strip() or len(query)>1024:raise InvalidRequest('Query must contain 1-1024 characters')
        unicode_text(query)
    if view not in {'relevance','timeline'} or type(refresh) is not bool:raise InvalidRequest('Invalid query view or refresh flag')
    if type(budget) is not int or not 2048<=budget<=65536 or type(source_bytes) is not int or not 2048<=source_bytes<=16384:raise InvalidRequest('Invalid complete-response or source byte budget')
    normalized={};positions={}
    for selected,default,target,max_value in ((limits,5,normalized,20),(offsets,0,positions,10000)):
        if selected is not None and (not isinstance(selected,dict) or selected.keys()-set(SOURCES)):raise InvalidRequest('Unknown source limit/offset')
        for source in SOURCES:
            value=(selected or {}).get(source,default)
            if type(value) is not int or not (1 if target is normalized else 0)<=value<=max_value:raise InvalidRequest('Invalid source limit/offset')
            target[source]=value
    if positions['memory']%normalized['memory']:raise InvalidRequest('Memory offset must be a multiple of its page limit')
    from .timeline_index import occurrence
    known=[]
    for value in (since,until):
        if value is not None:
            if not isinstance(value,str) or occurrence(value)[2]!='provided':raise InvalidRequest('Time boundaries require zoned ISO text')
            known.append(occurrence(value)[1])
    if len(known)==2 and known[0]>=known[1]:raise InvalidRequest('Invalid half-open time interval')
    return [q.strip() for q in queries],normalized,positions


def _failure(code):return {'status':'unavailable','error':{'code':code,'retryable':code!='not_configured'},'hits':[],'total':None,'cache_hit':False,'coverage':{'complete':False}}


def _bounded(value,budget,offset):
    value.update(cache_hit=False)
    value['retry_offset']=None
    while True:
        value['fetched']=len(value['hits']);value['next_offset']=offset+value['scanned_candidates'] if value['total']>offset+value['scanned_candidates'] else None
        value['budget_limited']=value['total']>offset and not value['hits']
        if len(canonical(value).encode())<=budget:return value
        if not value['hits']:raise SourceFailure('source_metadata_exceeds_budget')
        removed=value['hits'].pop();value['retry_offset']=removed['source_offset']


def task_revision(tools):
    db=tools.store.db;scope=tools.scope.key
    counts=tuple(tuple(row) for row in db.execute('SELECT status,count(*),sum(spans) FROM evidence_index_state WHERE scope=? GROUP BY status ORDER BY status',(scope,)))
    live=db.execute('SELECT count(*) FROM artifacts WHERE scope=? AND invalidated=0 AND (expires IS NULL OR expires>?)',(scope,tools.store.clock())).fetchone()[0]
    fts=db.execute("SELECT count(*) FROM sqlite_master WHERE name='evidence_fts'").fetchone()[0]
    return tools.store.task(tools.scope)['version'],counts,live,fts


def task_search(tools,query,limit,offset,source_bytes):
    page=tools.store.search_indexed(tools.scope,query,limit=limit,offset=offset,budget=source_bytes,context_chars=100,window_limit=1)
    hits=[]
    for row in page['hits']:
        ref=row.get('snippet_ref',row['ref']);name=ref['name']
        manifest=name.rsplit('/',1)[0]+'/result' if name.startswith('tool-result/') and name.rsplit('/',1)[-1] in {'stdout','stderr'} else name
        event=tools.store.db.execute('SELECT entry FROM timeline_entries WHERE partition=? AND name=?',(tools.scope.key,manifest)).fetchone()
        provenance=json.loads(event[0]) if event else {}
        hits.append({'origin':'task','id':name+':'+str(ref['revision'])+':'+str(ref.get('start',0)),
            'title':row.get('title',name),'preview':row.get('snippet','')[:520],'preview_truncated':len(row.get('snippet',''))>520,
            'task':tools.scope.task,'source_session':provenance.get('native_session'),
            'version':{'kind':'artifact_revision','value':ref['revision'],'state':'available'},
            'time':{'occurred_at':provenance.get('occurred_at'),'state':provenance.get('source_time_state','missing'),'basis':'captured_source' if provenance else 'artifact_has_no_occurrence'},
            'ref':{'reader':'context.get',**ref},'current_task_membership':True,'evidence_role':'task_evidence','source_score':row.get('score')})
    return hits,page['total'],page['index']


def query(tools,*,queries,view='relevance',limits=None,offsets=None,budget=16384,source_bytes=8192,refresh=False,since=None,until=None):
    queries,limits,offsets=validate(queries,view,limits,offsets,budget,source_bytes,refresh,since,until)
    binding=bound_sources(tools);state=state_for(tools);deadline=time.monotonic()+24
    unique=list(dict.fromkeys(queries));request_id='query-'+uuid4().hex
    config_key=repr(binding);options=(view,tuple(limits.items()),tuple(offsets.items()),source_bytes,since,until,config_key)
    groups={source:{} for source in SOURCES}

    def group(source):
        db=None;memory=None;revision=None
        try:
            if source=='events':
                db=connect_events(binding,min(deadline,time.monotonic()+4));revision=event_revision(db,binding)
            elif source=='memory':
                memory=ExistingMemory(binding);revision=memory.revision(deadline)
            else:revision=task_revision(tools)
            for text in unique:
                if time.monotonic()>deadline:
                    groups[source][text]=_failure('deadline_exceeded');continue
                key=(source,revision,text,options)
                cached=None if refresh else state.get(key)
                if cached is not None:
                    cached['revision_checked_at']=time.time();groups[source][text]=cached;continue
                try:
                    response_revision=revision
                    if source=='task':
                        tools.store.db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
                        try:hits,total,coverage=task_search(tools,text,limits[source],offsets[source],source_bytes)
                        finally:tools.store.db.set_progress_handler(None,0)
                        stable=task_revision(tools)==revision
                    elif source=='events':
                        db.set_progress_handler(lambda:int(time.monotonic()>min(deadline,source_deadline)),1000)
                        hits,total,coverage=events_search(db,binding,text,limits[source],offsets[source],view,since,until)
                        stable=True
                    else:
                        hits,total,coverage,response_revision=memory.search(text,limits[source],offsets[source],deadline)
                        stable=response_revision is not None and response_revision==revision
                    consumed=len(hits)
                    for rank,hit in enumerate(hits):hit['source_offset']=offsets[source]+rank
                    # Memory/day precision is supplied by its owner; bounds never
                    # manufacture midnight for undated history or generic artifacts.
                    if source!='events' and (since is not None or until is not None):
                        from .timeline_index import occurrence
                        low=occurrence(since)[1] if since else None;high=occurrence(until)[1] if until else None
                        before=len(hits)
                        hits=[h for h in hits if h['time'].get('occurred_at') and (low is None or occurrence(h['time']['occurred_at'])[1]>=low) and (high is None or occurrence(h['time']['occurred_at'])[1]<high)]
                        coverage={**coverage,'time_filtered_candidates':before-len(hits),'global_time_search':False,'complete':False}
                    handle=state.handle(source,repr(response_revision)) if response_revision is not None else None
                    value=_bounded({'status':'ok','hits':hits,'total':total,'coverage':coverage,
                        'revision':{'kind':'bound_source_revision_handle','value':handle,'portable':False,'confirmed':stable},
                        'scope':{'task':tools.scope.task if source=='task' else None,'workspace':binding.workspace if binding else None},
                        'ranking':'source_rank_only_no_cross_source_score_comparison','scanned_candidates':consumed,
                        'revision_checked_at':time.time()},source_bytes,offsets[source])
                    groups[source][text]=value
                    if stable:state.put(key,value)
                except SourceFailure as exc:groups[source][text]=_failure(exc.code)
                except (sqlite3.Error,RailError,ValueError,TypeError,KeyError):groups[source][text]=_failure('source_query_failed_or_deadline')
        except SourceFailure as exc:
            for text in unique:groups[source][text]=_failure(exc.code)
        except (sqlite3.Error,ValueError,TypeError,KeyError):
            for text in unique:groups[source][text]=_failure('source_revision_unavailable')
        finally:
            if db is not None:db.close()

    # Main R connection stays on its owning thread; only external source groups
    # use workers, with their own SQLite/RPC connections and a shared deadline.
    source_deadline=min(deadline,time.monotonic()+4)
    with ThreadPoolExecutor(max_workers=2) as pool:
        events_future=pool.submit(group,'events');memory_future=pool.submit(group,'memory')
        group('task');events_future.result();memory_future.result()
    observed_errors=set()
    for source in SOURCES:
        for value in groups[source].values():
            if value['status']=='unavailable':
                code=value['error']['code']
                if (source,code) not in observed_errors:
                    state.errors.append({'source':source,'code':code,'observed_at':time.time(),'request_id':request_id})
                    observed_errors.add((source,code))
    state.errors=state.errors[-10:]
    output={'schema':'contextrail.federated-query.v1','request_id':request_id,'view':view,'queries':[],
        'scope':{'task':tools.scope.task,'reader_session':tools.session_id},
        'ranking':'reciprocal_source_rank' if view=='relevance' else 'matching_candidates_by_known_occurrence_unknown_last',
        'history_boundary':'events_and_memory_are_history_not_current_task_facts',
        'bound_sources':{'workspace':binding.workspace if binding else None,
            'native_sessions':[{'client':c,'session':s} for c,s in binding.native_sessions] if binding else [],
            'memory_project':'main' if binding and binding.memory_home else None},
        'batch_advice':{'requested':len(queries),'unique':len(unique),'coalesced':len(queries)-len(unique),
            'suggestion':'combine_related_queries_up_to_eight; use_returned_source_offsets_or_exact_refs'},
        'continuation':'change_query_or_source_offsets; refresh_or_exact_ref_remains_available'}
    first_positions={}
    hit_delivery_keys={}
    for position,text in enumerate(queries):
        sources={source:copy.deepcopy(groups[source][text]) for source in SOURCES}
        fingerprint=(text,options,tuple((s,sources[s]['status'],repr(sources[s].get('revision'))) for s in SOURCES))
        previous=state.history.get(fingerprint)
        duplicate=text in first_positions
        merged=merge_sources({s:sources[s]['hits'] for s in SOURCES},view)
        for hit in merged:
            delivery=(text,options,hit['origin'],hit['id'],repr(sources[hit['origin']].get('revision')))
            hit_delivery_keys[id(hit)]=delivery
            shown=sources[hit['origin']].get('cache_hit',False) and delivery in state.delivered
            hit['already_shown']=shown
            if shown or duplicate:hit.update(preview='',preview_truncated=True)
        summaries={s:{k:v for k,v in sources[s].items() if k!='hits'} for s in SOURCES}
        entry={'query':text,'hits':merged,'sources':summaries,'partial':any(s['status']!='ok' or s.get('coverage',{}).get('complete') is False for s in sources.values()),
            'repeat_of':previous if previous and not refresh else None,'omitted_by_response_budget':0}
        if duplicate:entry['coalesced_with']=first_positions[text];entry['repeat_of']=request_id
        else:first_positions[text]=position
        output['queries'].append(entry)
        if all(s['status']=='ok' for s in sources.values()):
            state.history[fingerprint]=request_id;state.history.move_to_end(fingerprint)
            while len(state.history)>32:state.history.popitem(last=False)

    def metrics():
        # A conservative reserve avoids self-referential exact-byte counters
        # oscillating at decimal boundaries. The outer budget uses real bytes.
        output['remaining_bytes']=0;output['response_bytes_upper_bound']=0
        upper=len(canonical(output).encode())+32
        output['response_bytes_upper_bound']=upper;output['remaining_bytes']=max(0,budget-upper)
    metrics()
    while len(canonical(output).encode())>budget:
        with_preview=[h for result in output['queries'] for h in result['hits'] if h['preview']]
        if with_preview:
            hit=max(with_preview,key=lambda h:len(h['preview']));hit.update(preview='',preview_truncated=True)
        else:
            target=max(output['queries'],key=lambda r:len(r['hits']))
            if not target['hits']:raise BudgetExceeded('Batch query metadata exceeds the complete response budget; reduce batch size')
            target['hits'].pop();target['omitted_by_response_budget']+=1;target['partial']=True
        metrics()
    for result in output['queries']:
        for source in SOURCES:
            summary=result['sources'][source];shown=sum(h['origin']==source for h in result['hits'])
            summary['shown']=shown
            raw=groups[source][result['query']]
            displayed={h['id'] for h in result['hits'] if h['origin']==source}
            missing=[h['source_offset'] for h in raw['hits'] if h['id'] not in displayed]
            if raw.get('retry_offset') is not None:missing.append(raw['retry_offset'])
            if missing:
                summary['next_offset']=offsets[source] if source=='memory' else min(missing)
                summary['retry_with_more_budget']=True
    metrics()
    # Final fields were reserved by the actual fitting loop as well.
    while len(canonical(output).encode())>budget:
        target=max(output['queries'],key=lambda r:len(r['hits']))
        if not target['hits']:raise BudgetExceeded('Query metadata exceeds response budget')
        removed=target['hits'].pop();target['omitted_by_response_budget']+=1;target['partial']=True
        target['sources'][removed['origin']]['shown']-=1;target['sources'][removed['origin']]['next_offset']=offsets[removed['origin']]+target['sources'][removed['origin']]['shown']
        if removed['origin']=='memory':target['sources']['memory']['next_offset']=offsets['memory'];target['sources']['memory']['retry_with_more_budget']=True
        metrics()
    delivered_now=set(state.delivered)
    for result in output['queries']:
        for hit in result['hits']:
            delivery=hit_delivery_keys[id(hit)]
            if delivery in delivered_now and not refresh:hit['already_shown']=True
            if hit['preview']:
                delivered_now.add(delivery);state.delivered[delivery]=True;state.delivered.move_to_end(delivery)
    while len(state.delivered)>1024:state.delivered.popitem(last=False)
    metrics()
    return output
