"""Exact by-file views over original M/R timelines, with an upstream native formatter."""
from contextlib import closing
from dataclasses import asdict
import json
import importlib.util
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import time

from .claude_mem import invoke
from .errors import AccessDenied, BudgetExceeded, InvalidRequest, RailError
from .federation import bound_sources
from .models import Ref, Scope, Lease, canonical, integer
from .source_events import workspace_key
from .store import Store
from .query_sources import SourceFailure

VERSION_SCHEMA='''CREATE TABLE IF NOT EXISTS file_history_versions(
 epoch INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT NOT NULL,path TEXT NOT NULL,source_versions TEXT NOT NULL,
 UNIQUE(scope,path,source_versions));'''


def resolved(binding,file,cwd=None):
    if not isinstance(file,str) or not file or len(file)>4096 or '\x00' in file:raise InvalidRequest('File history requires a bounded literal path.')
    root=Path(binding.workspace).resolve();path=(Path(cwd or root)/file).resolve()
    if not path.is_relative_to(root):raise AccessDenied('File history path resolves outside the bound worktree.')
    return path,path.relative_to(root).as_posix()


def memory_db(binding):
    if binding is None or binding.memory_home is None:raise InvalidRequest('Existing memory source is not bound.')
    db=sqlite3.connect((binding.memory_home/'state.sqlite').as_uri()+'?mode=ro',uri=True,timeout=1)
    db.row_factory=sqlite3.Row
    return db


def _file_pages(fetch,forms):
    rows={};coverage=[]
    for form in forms:
        page=fetch(file=form,limit=40,budget=65536)
        if page['total']>40:page=fetch(file=form,offset=page['total']-40,limit=40,budget=65536)
        coverage.append(page['coverage'])
        for row in page['entries']:rows[row['id']]=row
    return {'entries':list(rows.values()),'coverage':coverage}


def _bounded(value,budget):
    integer(budget,1024)
    while len(canonical(value).encode('utf-8'))>budget:
        if value.get('entries'):
            value['entries'].pop();value['truncated']=True
        else:raise BudgetExceeded('File history references exceed the response budget.')
    return value


def navigation(ref):
    return {'details':{'tool':'context.history_details','arguments':{'refs':[ref]}},
        'neighborhood':{'tool':'context.history_neighborhood','arguments':{'ref':ref}}}


def file_history(tools,file,*,budget=8192,limit=20):
    integer(limit,1);integer(budget,1024)
    if limit>40 or budget>tools.max_read_bytes:raise InvalidRequest('File history exceeds bound limit/budget.')
    binding=bound_sources(tools)
    if binding is None:raise InvalidRequest('File history requires a trusted workspace source binding.')
    absolute,relative=resolved(binding,file)
    from .timeline_index import path_key
    forms=invoke('file-paths',tool='',input={},cwd=binding.workspace,absolute=str(absolute),relative=relative)['query_paths']
    forms=list(dict.fromkeys(path_key(form,binding.workspace) for form in forms))
    result={'schema':'contextrail.file-history.v1','file':str(absolute),'relative_path':relative,'entries':[],
        'source_errors':[],'coverage':[],'truncated':False,'trust':'historical_data_not_current_policy','model_calls':0}
    from .timeline import timeline
    try:
        page=_file_pages(lambda **options:timeline(tools.store,tools.scope,tools.session_id,**options),forms)
        result['coverage'].append({'source':'task','coverage':page['coverage']})
        for row in page['entries']:
            if row.get('availability',{}).get('status')!='available':continue
            result['entries'].append({**row,'ref':{'origin':'task','ref':row['ref'],'id':row['id']},'source_revision':row['ref']['revision'],'capture_sequence':0})
    except (RailError,sqlite3.Error):result['source_errors'].append({'source':'task','code':'task_timeline_unavailable'})
    from .event_stream import native_timeline
    for client,session in binding.native_sessions:
        try:
            with closing(memory_db(binding)) as db:
                page=_file_pages(lambda **options:native_timeline(db,binding.workspace,client,session,**options),forms)
                result['coverage'].append({'source':'events','client':client,'native_session':session,'coverage':page['coverage']})
                ids=[r['source_event'] for r in page['entries']]
                positions=dict(db.execute('SELECT event_id,sequence FROM normalized_events WHERE workspace=? AND client=? AND native_session=? AND event_id IN ('+','.join('?' for _ in ids)+')',
                    (workspace_key(binding.workspace),client,session,*ids))) if ids else {}
                for row in page['entries']:
                    if row['source_event'] not in positions:continue
                    result['entries'].append({**row,'source_revision':row['source_event'],'capture_sequence':positions[row['source_event']],
                        'ref':{'origin':'events','client':client,'native_session':session,'event_id':row['source_event']}})
        except (OSError,ValueError,sqlite3.Error):
            result['source_errors'].append({'source':'events','client':client,'native_session':session,'code':'original_history_unavailable'})
    result['entries'].sort(key=lambda r:(r.get('occurred_at') or '',r['capture_sequence']),reverse=True)
    if len(result['entries'])>limit:result['entries']=result['entries'][:limit];result['truncated']=True
    for row in result['entries']:row['navigation']=navigation(row['ref'])
    return _bounded(result,budget)


def _event_ref(tools,ref):
    binding=bound_sources(tools)
    if binding is None or (ref.get('client'),ref.get('native_session')) not in binding.native_sessions:
        raise AccessDenied('History reference is outside the exact bound source sessions.')
    if ref.get('workspace') and workspace_key(ref['workspace'])!=workspace_key(binding.workspace):
        raise AccessDenied('History reference belongs to another worktree.')
    return binding


def history_details(tools,refs,*,budget=8192):
    if not isinstance(refs,list) or not 1<=len(refs)<=10:raise InvalidRequest('Supply 1–10 exact history refs.')
    integer(budget,1024)
    if budget>tools.max_read_bytes:raise InvalidRequest('History detail budget exceeds reader limit.')
    result={'items':[],'model_calls':0,'trust':'untrusted_source_data'}
    from .event_stream import get_event
    for ref in refs:
        if not isinstance(ref,dict):raise InvalidRequest('History refs must be objects.')
        item={'ref':ref}
        try:
            if ref.get('origin')=='task' or ref.get('reader')=='context.get':
                exact=ref.get('ref') or {k:v for k,v in ref.items() if k in {'name','revision','start','end'}}
                data=tools.call('context.get',exact)
            elif ref.get('reader')=='basic-memory.read_note':
                binding=bound_sources(tools)
                if binding is None or binding.memory_home is None or ref.get('project')!='main':raise AccessDenied('Only bound main notes may be read.')
                spec=importlib.util.spec_from_file_location('rail_exact_note_transport',binding.memory_home/'app/resource_client.py')
                module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
                response=module.rpc(binding.memory_home,{'jsonrpc':'2.0','id':1,'method':'tools/call','params':{
                    'name':'read_note','arguments':{'identifier':ref['identifier'],'project':'main'}}},timeout=binding.timeout_seconds)
                if response.get('error') or response.get('result',{}).get('isError'):raise SourceFailure('original_note_unavailable')
                data={'original_result':response['result'],'version_state':'current_note_only_historical_revision_not_offered'}
            else:
                binding=_event_ref(tools,ref)
                with closing(memory_db(binding)) as db:data=get_event(db,binding.workspace,ref['client'],ref['native_session'],ref['event_id'])
            item.update(status='available',data=data)
            if len(canonical({**result,'items':[*result['items'],item]}).encode('utf-8'))>budget:
                item={'ref':ref,'status':'unavailable','error':{'code':'detail_exceeds_remaining_budget'},'recover':'use exact existing context.get or memory.stream_get with a narrower range'}
        except (RailError,OSError,ValueError,KeyError,sqlite3.Error,ImportError,RuntimeError,SourceFailure,ConnectionError,TimeoutError):
            item.update(status='unavailable',error={'code':'exact_history_source_unavailable'})
        result['items'].append(item)
    if len(canonical(result).encode('utf-8'))>budget:raise BudgetExceeded('History detail refs exceed response budget.')
    return result


def history_neighborhood(tools,ref,*,budget=8192,limit=10):
    integer(limit,1);integer(budget,1024)
    if limit>40 or budget>tools.max_read_bytes:raise InvalidRequest('Neighborhood exceeds bound limit/budget.')
    if not isinstance(ref,dict):raise InvalidRequest('Supply an exact history ref.')
    if ref.get('origin')=='task':return tools.call('context.timeline',{'around':ref['id'],'limit':limit,'budget':budget})
    if ref.get('reader')=='context.get':
        exact=Ref(**{k:v for k,v in ref.items() if k in {'name','revision','start','end'}})
        tools.store.get(tools.scope,exact)
        name=exact.name
        if name.startswith('tool-result/') and name.rsplit('/',1)[-1] in {'stdout','stderr'}:name=name.rsplit('/',1)[0]+'/result'
        row=tools.store.db.execute('SELECT json_extract(entry,\'$.id\') FROM timeline_entries WHERE partition=? AND name=?',(tools.scope.key,name)).fetchone()
        if not row:raise InvalidRequest('This exact task artifact has no event-time neighborhood; use context.get.')
        return tools.call('context.timeline',{'around':row[0],'limit':limit,'budget':budget})
    if ref.get('reader')=='basic-memory.read_note':
        return {'entries':[],'source_ref':ref,'coverage':{'complete':False,'reason':'note_ref_has_no_exact_event_time_anchor'},
            'recover':{'tool':'context.history_details','arguments':{'refs':[ref]}},'model_calls':0}
    binding=_event_ref(tools,ref)
    from .event_stream import native_timeline
    try:
        with closing(memory_db(binding)) as db:
            return native_timeline(db,binding.workspace,ref['client'],ref['native_session'],around='native_event:'+ref['event_id'],limit=limit,budget=budget)
    except (OSError,ValueError,sqlite3.Error):raise InvalidRequest('The exact history neighborhood is unavailable; no newer source was substituted.') from None


def _code_mode_command(value):
    """Read a literal exec_command call in the observed native wrapper; no eval."""
    if not isinstance(value,str) or len(value)>16384:return None
    match=re.fullmatch(r'\s*text\(\s*await\s+tools\.exec_command\(\s*(\{.*\})\s*\)\s*\)\s*;?\s*',value,re.S)
    if not match:
        match=re.fullmatch(r'\s*await\s+tools\.exec_command\(\s*(\{.*\})\s*\)\s*;?\s*',value,re.S)
    if not match:return None
    text=match[1];i=1;fields={};decoder=json.JSONDecoder()
    def whitespace(index):
        while index<len(text) and text[index].isspace():index+=1
        return index
    try:
        while True:
            i=whitespace(i)
            if text[i]=='}':
                if text[i+1:].strip():return None
                command=fields.get('cmd',fields.get('command'))
                return command if isinstance(command,str) else None
            if text[i]=='"':key,i=decoder.raw_decode(text,i)
            else:
                keymatch=re.match(r'[A-Za-z_$][A-Za-z0-9_$]*',text[i:])
                if not keymatch:return None
                key=keymatch[0];i+=len(key)
            if not isinstance(key,str) or key in fields:return None
            i=whitespace(i)
            if text[i]!=':':return None
            i=whitespace(i+1);fields[key],i=decoder.raw_decode(text,i)
            i=whitespace(i)
            if text[i]==',':i+=1
            elif text[i]!='}':return None
    except (ValueError,IndexError,RecursionError):return None


def literal_paths(payload):
    tool=payload.get('tool_name','');args=payload.get('tool_input',{})
    if not isinstance(tool,str):return []
    if tool in {'exec','functions.exec'}:
        command=_code_mode_command(args.get('input',args.get('code')) if isinstance(args,dict) else args)
        if command is None:return []
        tool='exec_command';args={'cmd':command}
    if not isinstance(args,dict):return []
    if tool in {'Read','Edit','Write','MultiEdit','read_file','view_file'}:
        value=args.get('file_path',args.get('path'))
        return [value] if isinstance(value,str) else []
    if tool in {'exec_command','functions.exec_command','Bash','shell_command'}:
        command=args.get('cmd',args.get('command'))
        if not isinstance(command,str) or len(command)>16384:return []
        try:
            r=subprocess.run(['pwsh.exe','-NoProfile','-NonInteractive','-File',str(Path(__file__).with_name('file_literal_path.ps1'))],
                input=json.dumps({'command':command}).encode('utf-8'),capture_output=True,timeout=3,shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            return json.loads(r.stdout).get('paths',[]) if r.returncode==0 else []
        except (OSError,ValueError,subprocess.TimeoutExpired):return []
    # Upstream extractFromMcp rejects every other tool name immediately.
    # Keep that negative result without launching its Node process.
    if not re.fullmatch(r'mcp__.+__(read|view|cat)(?:_file|_files)?',tool):return []
    return invoke('file-paths',tool=tool,input=args,cwd=payload['cwd'])['paths']


def hook_file_history(binding,query,client,payload):
    from .host import ContextTools
    scope=Scope(**binding['scope']);owner=Lease(**binding['owner'])
    paths=literal_paths(payload)
    if not paths:return {'systemMessage':'ContextRail file history skipped: no unambiguous supported literal file path; ordinary tool policy remains native.'}
    try:
        path,_=resolved(query,paths[0],payload['cwd'])
        with Store(binding['store']) as store:
            from dataclasses import replace
            # Automatic reminders must not grow from the reads they just caused.
            # Explicit MCP history retains the current session when host-bound.
            historical=replace(query,native_sessions=tuple(p for p in query.native_sessions if p!=(client,payload['session_id']))) if binding.get('native_session_binding')=='current_hook' else query
            tools=ContextTools(store,scope,binding['session'],query_binding=historical)
            history=file_history(tools,str(path),budget=min(8192,binding['hook_bytes']-512),limit=15)
            if not history['entries']:
                return {'systemMessage':'ContextRail file history '+('source unavailable; ordinary read can continue.' if history['source_errors'] else 'has no observed matching sources.')}
            versions=canonical(sorted((canonical(r['ref']),str(r['source_revision'])) for r in history['entries']))
            store.db.executescript(VERSION_SCHEMA)
            with store.transaction():
                store._lease(scope,owner)
                store.db.execute('INSERT OR IGNORE INTO file_history_versions(scope,path,source_versions) VALUES(?,?,?)',(scope.key,str(path),versions))
                epoch=store.db.execute('SELECT epoch FROM file_history_versions WHERE scope=? AND path=? AND source_versions=?',(scope.key,str(path),versions)).fetchone()[0]
            observations=[]
            for row in history['entries']:
                from .timeline_index import occurrence
                _,when,_=occurrence(row.get('occurred_at'))
                observations.append({'id':row['id'],'memory_session_id':row.get('native_session') or row['id'],
                    'title':row.get('preview') or row.get('event_type'),'type':'discovery','created_at_epoch':when*1000 if when is not None else None,
                    'files_read':json.dumps([history['relative_path']]),'files_modified':'[]'})
            data={'filePath':str(path),'absolutePath':str(path),'relativePath':history['relative_path'],'newestObservationMs':epoch,
                'displayedObservations':observations,'fileStatVerified':False,'fileSizeBytes':0}
            session=canonical([scope.key,client,payload['session_id']])
            rendered=invoke('file-history',histories=[data],allowed=[str(path)],session=session,root=query.workspace)
            gate=rendered.pop('gate');specific=rendered.get('hookSpecificOutput')
            if not specific:return rendered
            envelope={**history,'text':specific['additionalContext'],'gate_epoch':epoch}
            specific['additionalContext']=canonical(envelope)
            if len(canonical(rendered).encode('utf-8'))>binding['hook_bytes'] or client=='claude' and len(specific['additionalContext'].encode('utf-16-le'))//2>10000:
                return {'systemMessage':'ContextRail optional file history exceeds the native budget; exact history remains available through context.file_history.'}
            store.db.executescript(gate['ddl'])
            with store.transaction():
                store._lease(scope,owner)
                claimed=store.db.execute(gate['claim'],(session,str(path),epoch,int(store.clock()*1000))).fetchone()
                if claimed is None:return {'suppressOutput':True}
                store._event(scope,'native_file_history.prepared',client=client,native_session=payload['session_id'],file=str(path),
                    source_versions=versions,gate_epoch=epoch,model_consumption='unknown')
            return rendered
    except (RailError,OSError,ValueError,KeyError,sqlite3.Error):
        return {'systemMessage':'ContextRail optional file history unavailable or outside the bound worktree; ordinary tool policy remains native.'}
