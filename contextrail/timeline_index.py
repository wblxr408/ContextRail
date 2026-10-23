"""Shared derived timeline in the original databases; no file reads or models."""
from datetime import datetime,timezone
import json
import math
import os
from ._vendor.context_mode_errors import CATEGORIES,classify_error

INDEXER='timeline-v2-context-mode-90af14e'
MAX_SOURCE=4*1024*1024
SCHEMA='''
CREATE TABLE IF NOT EXISTS timeline_state(
 partition TEXT NOT NULL,name TEXT NOT NULL,revision INTEGER NOT NULL,status TEXT NOT NULL,reason TEXT,
 PRIMARY KEY(partition,name));
CREATE TABLE IF NOT EXISTS timeline_entries(
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,partition TEXT NOT NULL,name TEXT NOT NULL,
 native_session TEXT,source_kind TEXT,role TEXT,call_id TEXT,occurred REAL,error_category TEXT,
 entry TEXT NOT NULL,UNIQUE(partition,name));
CREATE INDEX IF NOT EXISTS timeline_order ON timeline_entries(partition,occurred,sequence);
CREATE INDEX IF NOT EXISTS timeline_calls ON timeline_entries(partition,call_id);
CREATE TABLE IF NOT EXISTS timeline_paths(partition TEXT NOT NULL,name TEXT NOT NULL,path TEXT NOT NULL,
 PRIMARY KEY(partition,name,path));
'''


def canonical(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)


def occurrence(value):
    if value is None or value=='':return None,None,'missing'
    try:
        if type(value) in (float,int):
            if not math.isfinite(value):raise ValueError()
            parsed=datetime.fromtimestamp(value,timezone.utc)
        elif isinstance(value,str):
            parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
            if parsed.tzinfo is None:return None,None,'ambiguous_timezone'
        else:raise ValueError()
        utc=parsed.astimezone(timezone.utc)
        return utc.isoformat(),utc.timestamp(),'provided'
    except (ValueError,OverflowError,OSError):return None,None,'invalid'


def path_key(value,workspace=''):
    result=value.replace('\\','/').rstrip('/')
    base=workspace.replace('\\','/').rstrip('/')
    if os.name=='nt':result=result.casefold();base=base.casefold()
    if base and result.startswith(base+'/'):result=result[len(base)+1:]
    return result.removeprefix('./')


def source_paths(arguments,workspace=''):
    if not isinstance(arguments,dict):return []
    paths=[]
    for field in ('path','file_path','filepath','filePath','target_file','source_paths','paths'):
        value=arguments.get(field)
        for path in value if isinstance(value,list) else [value]:
            if isinstance(path,str) and path and len(path)<=4096 and '\x00' not in path:
                paths.append(path_key(path,workspace))
    return list(dict.fromkeys(paths))[:32]


def set_state(db,partition,name,revision,status,reason=None):
    db.execute('INSERT INTO timeline_state VALUES(?,?,?,?,?) ON CONFLICT(partition,name) DO UPDATE SET revision=excluded.revision,status=excluded.status,reason=excluded.reason',(partition,name,revision,status,reason))
    if status!='indexed':
        db.execute('DELETE FROM timeline_entries WHERE partition=? AND name=?',(partition,name))
        db.execute('DELETE FROM timeline_paths WHERE partition=? AND name=?',(partition,name))


def put_entry(db,partition,name,revision,entry,paths):
    if not db.in_transaction:raise ValueError('Timeline indexing requires the original transaction')
    iso,when,time_state=occurrence(entry.get('occurred_at'))
    declared=entry.get('source_time_state')
    entry.update(occurred_at=iso,source_time_state=declared if iso is None and declared in {'missing','invalid','ambiguous_timezone'} else time_state)
    entry.update(id=entry['origin']+':'+name,indexer=INDEXER,paths=paths,trust='untrusted_evidence')
    db.execute('INSERT INTO timeline_entries(partition,name,native_session,source_kind,role,call_id,occurred,error_category,entry) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(partition,name) DO UPDATE SET native_session=excluded.native_session,source_kind=excluded.source_kind,role=excluded.role,call_id=excluded.call_id,occurred=excluded.occurred,error_category=excluded.error_category,entry=excluded.entry',
        (partition,name,entry.get('native_session'),entry['origin'],entry['role'],entry.get('call_id'),when,entry.get('error_category'),canonical(entry)))
    db.execute('DELETE FROM timeline_paths WHERE partition=? AND name=?',(partition,name))
    db.executemany('INSERT OR IGNORE INTO timeline_paths VALUES(?,?,?)',[(partition,name,p) for p in paths])
    set_state(db,partition,name,revision,'indexed')


def classification(text,tool,status):
    failed=status in {'failed','timed_out','source_error'}
    category='timeout' if status=='timed_out' else classify_error(text,tool) if failed else None
    return category,{'basis':'source_declared_failure_and_text_heuristic' if failed else 'no_declared_failure',
                     'rule_source':'context-mode@90af14e/classifyError','heuristic':True,'text_limit_bytes':65536}


def native_partition(workspace,client,session):
    from .source_events import workspace_key
    return 'native:'+canonical([workspace_key(workspace),client,session])


def exec_outcomes(output):
    """One envelope layer only; never treat program stdout's JSON as a new run."""
    blocks=output if isinstance(output,list) else [output]
    codes=[];failed=[];incomplete=False
    for block in blocks[:128]:
        value=block.get('text') if isinstance(block,dict) and block.get('type') in {'input_text','text','output_text'} else block
        if isinstance(value,str):
            try:value=json.loads(value)
            except (ValueError,RecursionError):continue
        if not isinstance(value,dict) or not isinstance(value.get('chunk_id'),str) or not isinstance(value.get('output'),str):continue
        wall=value.get('wall_time_seconds')
        if type(wall) not in (int,float) or not math.isfinite(wall) or wall<0:continue
        code=value.get('exit_code')
        if type(code) is int:
            codes.append(code)
            if code:failed.append(value['output'])
        elif value.get('session_id') is not None:incomplete=True
        if len(codes)>=32:incomplete=True;break
    return codes,'\n'.join(failed),incomplete


def index_native(db,event,*,ref=None,partition=None,name=None,revision=1):
    for field in ('workspace','client','native_session','id','event_type','role'):
        value=event.get(field)
        if not isinstance(value,str) or len(value)>4096 or '\x00' in value:raise ValueError('Invalid native source identity')
    data=event.get('data',{})
    if not isinstance(data,dict):raise ValueError('Invalid native event body')
    partition=partition or native_partition(event['workspace'],event['client'],event['native_session'])
    name=name or event['id'];status=data.get('error_state','not_applicable')
    text=canonical(data);sample=text.encode('utf-8')[:65536].decode('utf-8','ignore')
    codes,failed,incomplete=exec_outcomes(data.get('output')) if event.get('client')=='codex' and event.get('event_type')=='tool.result' else ([], '', False)
    native_status=status
    if status=='unknown' and codes:
        status='failed' if any(codes) else 'unknown' if incomplete else 'source_ok'
    tool=data.get('original_tool') or data.get('tool') or ''
    classifier_text=(failed or sample).encode('utf-8')[:65536].decode('utf-8','ignore')
    category,rule=classification(classifier_text,tool,status)
    entry={'source_event':event['id'],'origin':'native_event','native_session':event['native_session'],
        'client':event['client'],'role':event['role'],'event_type':event['event_type'],
        'call_id':event.get('call_id'),'occurred_at':event.get('occurred_at'),'observed_at':event.get('observed_at'),
        'source_time_state':event.get('source_time_state'),'status':status,'error_category':category,'classification':rule,
        'native_error_state':native_status,'exit_codes':codes,'outcomes_incomplete':incomplete,
        'outcome_basis':'codex_exec_result_envelope' if codes else 'native_error_flag_or_unknown',
        'preview':sample[:320],'preview_truncated':len(text)>320,
        'ref':ref or {'reader':'stream-get','workspace':event['workspace'],'client':event['client'],'native_session':event['native_session'],'event_id':event['id']},
        'body_state':event.get('body_state','complete')}
    put_entry(db,partition,name,revision,entry,source_paths(data.get('arguments'),event['workspace']))


def query(db,partition,*,offset=0,limit=20,budget=8192,native_session=None,file=None,role=None,
          origin=None,error_category=None,since=None,until=None,around=None,coverage=None):
    if type(offset) is not int or not 0<=offset<=1000000 or type(limit) is not int or not 1<=limit<=100 or type(budget) is not int or not 1024<=budget<=65536:raise ValueError('Invalid timeline page or budget')
    if role is not None and role not in {'user','assistant','tool','lifecycle'}:raise ValueError('Unsupported timeline role')
    if origin is not None and origin not in {'native_event','tool_result'}:raise ValueError('Unsupported timeline origin')
    if error_category is not None and error_category not in CATEGORIES:raise ValueError('Unsupported error category')
    for value in (native_session,file,around):
        if value is not None and (not isinstance(value,str) or not value or len(value)>8192 or '\x00' in value):raise ValueError('Invalid timeline selector')
    where='e.partition=? AND json_extract(e.entry,\'$.indexer\')=?';args=[partition,INDEXER]
    for field,value in (('native_session',native_session),('role',role),('source_kind',origin),('error_category',error_category)):
        if value is not None:where+=' AND e.'+field+'=?';args.append(value)
    if file is not None:
        where+=' AND EXISTS(SELECT 1 FROM timeline_paths p WHERE p.partition=e.partition AND p.path=? AND (p.name=e.name OR EXISTS(SELECT 1 FROM timeline_entries c WHERE c.partition=e.partition AND c.name=p.name AND c.call_id=e.call_id AND e.call_id IS NOT NULL AND c.native_session=e.native_session AND c.source_kind=e.source_kind AND json_extract(c.entry,\'$.client\')=json_extract(e.entry,\'$.client\'))))';args.append(path_key(file))
    bounds=[]
    for label,value,operator in (('since',since,'>='),('until',until,'<')):
        if value is not None:
            if not isinstance(value,str):raise ValueError('Timeline time boundaries require zoned ISO text')
            _,when,state=occurrence(value)
            if state!='provided':raise ValueError('Timeline time boundaries require a timezone')
            where+=' AND e.occurred'+operator+'?';args.append(when);bounds.append((label,when))
    if len(bounds)==2 and bounds[0][1]>=bounds[1][1]:raise ValueError('Invalid timeline interval')
    order='e.occurred IS NULL,e.occurred,e.sequence'
    if around is not None:
        if offset:raise ValueError('Choose around or offset')
        row=db.execute('SELECT position FROM (SELECT json_extract(e.entry,\'$.id\') AS id,row_number() OVER(ORDER BY '+order+')-1 AS position FROM timeline_entries e WHERE '+where+') WHERE id=?',(*args,around)).fetchone()
        if row is None:raise ValueError('Selected timeline anchor is outside the filtered source')
        offset=max(0,row[0]-limit//2)
    total=db.execute('SELECT count(*) FROM timeline_entries e WHERE '+where,args).fetchone()[0]
    result={'schema':'contextrail.timeline.v1','entries':[],'total':total,'offset':offset,'next_offset':None,
        'order':'occurred_at_ascending_unknown_last','view':'live_offset_page_not_snapshot',
        'index_highwater':db.execute('SELECT coalesce(max(sequence),0) FROM timeline_entries WHERE partition=?',(partition,)).fetchone()[0],'coverage':coverage or {},
        'trust':'untrusted_evidence','range_unit':'source_ref_not_preview_offsets'}
    for row in db.execute('SELECT e.entry FROM timeline_entries e WHERE '+where+' ORDER BY '+order+' LIMIT ? OFFSET ?',(*args,limit,offset)):
        entry=json.loads(row[0]);result['entries'].append(entry)
        result['next_offset']=offset+len(result['entries']) if offset+len(result['entries'])<total else None
        if len(canonical(result).encode('utf-8'))>budget:
            entry['preview']='';entry['preview_truncated']=True
            if len(canonical(result).encode('utf-8'))>budget and entry['paths']:
                entry['paths']=[];entry['paths_truncated']=True
            if len(canonical(result).encode('utf-8'))>budget:
                result['entries'].pop();break
    result['next_offset']=offset+len(result['entries']) if offset+len(result['entries'])<total else None
    if len(canonical(result).encode('utf-8'))>budget or total>offset and not result['entries']:raise ValueError('Timeline response budget cannot preserve source references')
    return result
