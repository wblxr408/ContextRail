"""Recoverable, processed native event evidence in the existing memory database.

Pure format normalization plus a transaction-bound adapter. Tool aliases are an
attributed ELv2 context-mode port; this module does not start a process or model.
"""

from datetime import datetime, timezone
import base64
import json
import time

from .sanitization import CaptureError, sanitize_capture, require_safe_identity
from .source_events import source_identity, admit_event, workspace_key, canonical
from ._vendor.context_mode_tools import normalize_tool_name
from ._vendor.context_mode_usage import parse_codex_usage
from .capture_policy import SCHEMA as CAPTURE_SCHEMA, claim_owner
from .learning_origin import origin
from .timeline_index import SCHEMA as TIMELINE_SCHEMA, index_native, native_partition, query as query_timeline, set_state as timeline_state, MAX_SOURCE, INDEXER


SCHEMA = '''
CREATE TABLE IF NOT EXISTS normalized_events(
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
 workspace TEXT NOT NULL, client TEXT NOT NULL, native_session TEXT NOT NULL,
 role TEXT NOT NULL, event_type TEXT NOT NULL, metadata TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS normalized_events_scope ON normalized_events(workspace,client,native_session,sequence);
'''
SCHEMA += CAPTURE_SCHEMA
SCHEMA += TIMELINE_SCHEMA


def source_time(value):
    if value is None or value == '':
        return None, 'missing'
    if not isinstance(value, str):
        return None, 'invalid'
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None, 'ambiguous_timezone'
        return parsed.astimezone(timezone.utc).isoformat(), 'provided'
    except (ValueError, OverflowError):
        return None, 'invalid'


def _id(value):
    if not isinstance(value, str) or len(value) > 4096:
        return ''
    try:
        return require_safe_identity(value)
    except CaptureError:
        return ''


def _text(content):
    if isinstance(content, str):
        return content, []
    text, omitted = [], []
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict):
            omitted.append('unsupported_block')
        elif part.get('type') in {'text','input_text','output_text'} and isinstance(part.get('text'), str):
            text.append(part['text'])
        elif part.get('type') not in {'tool_use','tool_result','thinking','redacted_thinking'}:
            omitted.append(str(part.get('type','unsupported_block'))[:80])
    return '\n'.join(text), sorted(set(omitted))


def _arguments(value):
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed,(dict,list)):
                return parsed
        except (ValueError, RecursionError):
            pass
    return value


def _public_tool_output(value,depth=0):
    if depth>40:
        return {'omitted':'tool_structure_limit'},['tool_structure_limit']
    if isinstance(value,list):
        result=[];omitted=[]
        for item in value:
            processed,missing=_public_tool_output(item,depth+1)
            result.append(processed);omitted.extend(missing)
        return result,sorted(set(omitted))
    if isinstance(value,dict):
        kind=value.get('type')
        if isinstance(kind,str) and kind in {'image','input_image','output_image','image_url','audio','input_audio','thinking','redacted_thinking','reasoning'}:
            return {'type':kind,'omitted':True},[kind]
        result={};omitted=[]
        for key,item in value.items():
            result[key],missing=_public_tool_output(item,depth+1);omitted.extend(missing)
        return result,sorted(set(omitted))
    return value,[]


def normalize_record(client, row, state):
    """Return public-text/tool/lifecycle observations; never copy raw envelopes."""
    if not isinstance(row,dict) or row.get('isSidechain'):
        return []
    events = []
    def emit(kind, role, data, marker='', *, message='', call='', turn=None):
        events.append({'type':kind,'role':role,'data':data,'marker':_id(marker),
            'workspace':state.get('cwd',''),'native_session':state.get('session',''),
            'turn_id':_id(turn if turn is not None else state.get('turn','')),
            'message_id':_id(message),'call_id':_id(call)})
    if client == 'codex':
        p = row.get('payload',{})
        if not isinstance(p,dict):
            return []
        typ, kind = row.get('type'), p.get('type')
        if typ == 'session_meta':
            state.update(session=_id(p.get('id') or p.get('session_id')),cwd=p.get('cwd') or '',turn='',turn_open=False)
            state['model']=p.get('model') if isinstance(p.get('model'),str) else None
            emit('session.start','lifecycle',{'client':client},state['session'])
        elif typ == 'turn_context':
            if isinstance(p.get('cwd'),str):state['cwd']=p['cwd']
            if _id(p.get('turn_id')):state.update(turn=_id(p['turn_id']),turn_open=True)
            if isinstance(p.get('model'),str):state['model']=p['model']
        elif typ == 'response_item':
            mid = _id(p.get('id'))
            turn = _id(p.get('turn_id') or row.get('turn_id') or (state.get('turn') if state.get('turn_open') else ''))
            if kind == 'message' and p.get('role') in {'user','assistant'}:
                role = p['role'];phase=p.get('phase',p.get('channel'))
                if role == 'assistant' and phase in {'analysis','reasoning'}:
                    return []
                text, omitted = _text(p.get('content'))
                if text or omitted:
                    final = role == 'assistant' and phase in {'final','final_answer'}
                    emit('assistant.final' if final else role+'.message',role,
                         {'text':text,'omitted_blocks':omitted},mid,message=mid,turn=turn)
                    if final:state['final_turn']=turn or mid
            elif kind in {'function_call','custom_tool_call'}:
                call=_id(p.get('call_id'));name=str(p.get('name') or '')
                emit('tool.call','tool',{'original_tool':name,'tool':normalize_tool_name(name),
                    'arguments':_arguments(p.get('arguments',p.get('input')))},call or mid,message=mid,call=call,turn=turn)
            elif kind in {'function_call_output','custom_tool_call_output'}:
                call=_id(p.get('call_id'))
                flag=p.get('is_error',p.get('isError'))
                output,omitted=_public_tool_output(p.get('output'))
                data={'output':output,'error_state':'source_error' if flag is True else 'source_ok' if flag is False else 'unknown'}
                if omitted:data['omitted_blocks']=omitted
                emit('tool.result','tool',data,
                    call or mid,message=mid,call=call,turn=turn)
        elif typ == 'event_msg':
            turn=_id(p.get('turn_id') or state.get('turn'))
            if kind=='token_count':
                usage=parse_codex_usage(p,state.get('model'))
                # Equal counter values are not native event identities. With no
                # source ID, retain the existing file-generation/offset basis.
                emit('usage.reported','lifecycle',usage,_id(p.get('id')),turn=turn)
            elif kind in {'task_started','turn_started'}:
                state.update(turn=turn,turn_open=True)
                emit('turn.start','lifecycle',{'native_type':kind},turn,turn=turn)
            elif kind in {'task_complete','turn_complete'}:
                if isinstance(p.get('last_agent_message'),str) and p['last_agent_message'] and state.get('final_turn') != turn:
                    emit('assistant.final','assistant',{'text':p['last_agent_message'],'omitted_blocks':[]},turn,turn=turn)
                    state['final_turn']=turn
                emit('turn.complete','lifecycle',{'native_type':kind},turn,turn=turn)
                state['turn_open']=False
            elif kind in {'task_aborted','turn_aborted','session_end'}:
                emit('session.end' if kind=='session_end' else 'turn.aborted','lifecycle',{'native_type':kind},turn,turn=turn)
                state['turn_open']=False
        elif typ == 'compacted':
            emit('context.compacted','lifecycle',{'summary_captured':False},_id(p.get('window_id')))
    elif client == 'claude':
        state['session']=_id(row.get('sessionId') or row.get('session_id') or state.get('session'))
        if isinstance(row.get('cwd'),str):state['cwd']=row['cwd']
        msg=row.get('message',{})
        if not isinstance(msg,dict):msg={}
        mid=_id(msg.get('id'));uid=_id(row.get('uuid'));typ=row.get('type')
        content=msg.get('content',[])
        if typ in {'user','assistant'}:
            text, omitted = _text(content)
            if typ=='user' and (text or omitted):
                state['turn']=_id(row.get('promptId')) or uid
                emit('user.message','user',{'text':text,'omitted_blocks':omitted},uid,message=uid)
            if typ=='assistant' and (text or omitted):
                final=msg.get('stop_reason') in {'end_turn','stop_sequence'}
                emit('assistant.final' if final else 'assistant.message','assistant',
                    {'text':text,'omitted_blocks':omitted},uid or mid,message=mid)
            for index,part in enumerate(content if isinstance(content,list) else []):
                if not isinstance(part,dict):continue
                if part.get('type')=='tool_use':
                    call=_id(part.get('id'));name=str(part.get('name') or '')
                    emit('tool.call','tool',{'original_tool':name,'tool':normalize_tool_name(name),'arguments':part.get('input')},
                         call or (uid+':'+str(index) if uid else ''),message=mid,call=call)
                elif part.get('type')=='tool_result':
                    call=_id(part.get('tool_use_id'));flag=part.get('is_error')
                    output, missing=_text(part.get('content'))
                    emit('tool.result','tool',{'output':output,'omitted_blocks':missing,
                        'error_state':'source_error' if flag is True else 'source_ok' if flag is False else 'unknown'},
                         call or (uid+':'+str(index) if uid else ''),message=uid,call=call)
        elif typ=='system' and row.get('subtype') in {'turn_duration','compact_boundary','stop_hook_summary','session_end'}:
            subtype=row['subtype'];event='turn.complete' if subtype=='turn_duration' else 'context.compacted' if subtype=='compact_boundary' else 'session.end' if subtype=='session_end' else 'lifecycle.notice'
            emit(event,'lifecycle',{'native_type':subtype},uid)
    return events


def capture_record(db, client, row, context, source, start, end, generation, normalize_workspace):
    from .capture_rules import project_item
    context['_capture_row_modes']={}
    state=context.setdefault('_stream',{'session':_id(context.get('session')),
        'cwd':context.get('cwd',''),'turn':'','turn_open':False})
    emitted=normalize_record(client,row,state)
    occurred,status=source_time(row.get('timestamp'))
    outcomes=[]
    for index,item in enumerate(emitted):
        workspace=normalize_workspace(item['workspace'])
        session=_id(item['native_session'])
        owner=claim_owner(db,workspace,client,session)
        # Multi-observation records with no native IDs still need distinct keys.
        event=source_identity(client+'/'+item['type'],workspace,session,item['marker'],source,start,
                              {**generation,'record_part':index})
        body_state='partial_blocks_omitted' if item['data'].get('omitted_blocks') else 'complete'
        data,disposition=project_item(item,context)
        if disposition['mode']!='full':body_state='policy_'+disposition['mode']
        try:
            processed=sanitize_capture(data,item['role'])
        except CaptureError as exc:
            processed=sanitize_capture({'omitted':str(exc)},item['role']);body_state='omitted_limit_or_format'
        payload={'kind':'native-evidence','cwd':workspace,'offset':start,'user':'','final':processed.value}
        try:
            admitted=admit_event(db,event,payload,source,container=True)
        except CaptureError as exc:
            # The aggregate persistence boundary also counts envelope fields.
            # Retain an explicit omission if those exceed the same input cap.
            processed=sanitize_capture({'omitted':str(exc)},item['role']);body_state='omitted_limit_or_format'
            payload['final']=processed.value
            admitted=admit_event(db,event,payload,source,container=True)
        if admitted['outcome']=='inserted':
            metadata={'schema':'contextrail.native-event.v1','id':event['id'],'client':client,
                'workspace':workspace,'native_session':session,'role':item['role'],'event_type':item['type'],
                'turn_id':item['turn_id'] or None,'message_id':item['message_id'] or None,'call_id':item['call_id'] or None,
                'occurred_at':occurred,'source_time_state':status,'observed_at':time.time(),
                'body_state':body_state,'body_bytes':len(canonical(processed.value).encode('utf-8')),
                'capture':{**processed.provenance(),'disposition':disposition},'identity':event,
                'source':{'path':source,'generation':generation,'record_start':start,'record_end':end,
                          'reference_kind':'origin_metadata_not_persistent_raw_byte_ref'},
                'producer':'existing_jsonl_listener','capture_owner':owner,
                'origin':origin(event['id'],history_origin='native_event_evidence',eligible=False),
                'trust':'untrusted_source_data'}
            metadata=sanitize_capture(metadata,'lifecycle').value
            db.execute('INSERT INTO normalized_events(event_id,workspace,client,native_session,role,event_type,metadata) VALUES(?,?,?,?,?,?,?)',
                       (event['id'],workspace,client,session,item['role'],item['type'],canonical(metadata)))
            index_native(db,{**metadata,'data':processed.value})
        outcomes.append(admitted['outcome'])
        if item['role']=='user' and item['type']=='user.message':context['_raw_user_event']=event['id']
    return outcomes


def timeline_coverage(db,workspace,client,session):
    selection=_selection(workspace,client,session);partition=native_partition(*selection)
    total=db.execute('SELECT count(*) FROM normalized_events WHERE workspace=? AND client=? AND native_session=?',selection).fetchone()[0]
    counts=dict(db.execute("SELECT t.status,count(*) FROM timeline_state t LEFT JOIN timeline_entries e ON e.partition=t.partition AND e.name=t.name WHERE t.partition=? AND (t.status!='indexed' OR json_extract(e.entry,'$.indexer')=?) GROUP BY t.status",(partition,INDEXER)).fetchall())
    counts['pending']=max(0,total-sum(counts.values()))
    gaps=[dict(r) for r in db.execute("SELECT name,status,reason FROM timeline_state WHERE partition=? AND status!='indexed' LIMIT 10",(partition,))]
    return {'states':counts,'complete':counts.get('indexed',0)==total,'gaps':gaps,'scope':'exact_workspace_client_session'}


def native_timeline(db,workspace,client,session,**options):
    selection=_selection(workspace,client,session)
    return query_timeline(db,native_partition(*selection),coverage=timeline_coverage(db,*selection),**options)


def reindex_timeline(db,workspace,client,session,*,limit=20):
    selection=_selection(workspace,client,session);partition=native_partition(*selection)
    if type(limit) is not int or not 1<=limit<=100:raise ValueError('Invalid timeline rebuild limit')
    count=0;used=0
    with db:
        db.execute('BEGIN IMMEDIATE')
        rows=db.execute('SELECT n.event_id,length(s.content) AS source_bytes FROM normalized_events n LEFT JOIN source_events s ON s.id=n.event_id LEFT JOIN timeline_state t ON t.partition=? AND t.name=n.event_id LEFT JOIN timeline_entries e ON e.partition=t.partition AND e.name=t.name WHERE n.workspace=? AND n.client=? AND n.native_session=? AND (t.status IS NULL OR t.status=\'pending\' OR (t.status=\'indexed\' AND json_extract(e.entry,\'$.indexer\')!=?)) ORDER BY n.sequence LIMIT ?', (partition,*selection,INDEXER,limit)).fetchall()
        for row in rows:
            try:
                if row['source_bytes'] is None:
                    timeline_state(db,partition,row[0],1,'unavailable','source_unavailable');count+=1;continue
                if row['source_bytes']>MAX_SOURCE:
                    timeline_state(db,partition,row[0],1,'excluded','source_over_4mib');count+=1;continue
                event=get_event(db,*selection,row[0]);size=len(canonical(event).encode('utf-8'))
                if used+size>16*1024*1024 and count:break
                if size>MAX_SOURCE:timeline_state(db,partition,row[0],1,'excluded','source_over_4mib')
                else:index_native(db,event)
                used+=size
            except (ValueError,TypeError,KeyError):timeline_state(db,partition,row[0],1,'unavailable','source_unavailable')
            count+=1
        return {'processed':count,'source_bytes':used,'coverage':timeline_coverage(db,*selection)}


def _selection(workspace,client,session):
    if client not in {'codex','claude'} or not isinstance(workspace,str) or not workspace or not isinstance(session,str) or not session:
        raise ValueError('Exact workspace, client and native session are required')
    require_safe_identity(workspace);require_safe_identity(session)
    return workspace_key(workspace),client,session


def list_events(db,workspace,client,session,*,offset=0,limit=20,budget=8192,role=None):
    selection=_selection(workspace,client,session)
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100 or type(budget) is not int or not 1024<=budget<=65536:
        raise ValueError('Invalid bounded event page')
    if role is not None and role not in {'user','assistant','tool','lifecycle'}:
        raise ValueError('Unsupported event role')
    where='workspace=? AND client=? AND native_session=?';args=list(selection)
    if role:where+=' AND role=?';args.append(role)
    total=db.execute('SELECT count(*) FROM normalized_events WHERE '+where,args).fetchone()[0]
    result={'schema':'contextrail.native-events.v1','entries':[],'total':total,'offset':offset,'next_offset':None,
            'order':'capture_sequence_not_occurrence_time','trust':'untrusted_source_data'}
    for row in db.execute('SELECT sequence,metadata FROM normalized_events WHERE '+where+' ORDER BY sequence LIMIT ? OFFSET ?',[*args,limit,offset]):
        item=json.loads(row['metadata']);item['sequence']=row['sequence']
        result['entries'].append(item)
        if len(canonical(result).encode('utf-8'))+64>budget:
            result['entries'].pop();break
    result['next_offset']=offset+len(result['entries']) if offset+len(result['entries'])<total else None
    if total>offset and not result['entries']:
        raise ValueError('Event metadata exceeds response budget; increase budget')
    return result


def get_event(db,workspace,client,session,event_id):
    selection=_selection(workspace,client,session);require_safe_identity(event_id)
    row=db.execute('SELECT n.metadata,s.content FROM normalized_events n JOIN source_events s ON n.event_id=s.id WHERE n.workspace=? AND n.client=? AND n.native_session=? AND n.event_id=?',(*selection,event_id)).fetchone()
    if row is None:raise ValueError('Native event is not available in the selected source session')
    return {**json.loads(row['metadata']),'data':json.loads(row['content'])['final']}


def read_event_range(db,workspace,client,session,event_id,*,start=0,end=None,limit_bytes=65536):
    body=canonical(get_event(db,workspace,client,session,event_id)).encode('utf-8')
    if type(start) is not int or start<0 or end is not None and (type(end) is not int or end<start) or start>len(body) or end is not None and end>len(body):
        raise ValueError('Event byte range is outside its processed evidence')
    end=len(body) if end is None else end
    if end-start>limit_bytes:raise ValueError('Event read exceeds limit; request a narrower byte range')
    selected=body[start:end]
    try:content=selected.decode('utf-8');encoding='utf-8'
    except UnicodeError:content=base64.b64encode(selected).decode('ascii');encoding='base64'
    return {'event_id':event_id,'start':start,'end':end,'total_bytes':len(body),'encoding':encoding,'content':content,
            'range_unit':'processed_utf8_bytes','trust':'untrusted_source_data'}
