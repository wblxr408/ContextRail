"""Explicit host-selected intent, retaining exact processed UTF-8 provenance."""
from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from .errors import AccessDenied, InvalidRequest, Unavailable
from .models import Ref, Selection, canonical, integer, identifier
from .loss import report

ROLES=('original_goal','latest_request','latest_correction')
UNKNOWN={'status':'not_declared','effective_request':None,'next_action':None,
         'coverage':'unknown_user_intent_not_declared','anchors':{}}


def declared(bundle):
    value=bundle.ledger.get('resume_intent')
    if value is None:return None
    if not isinstance(value,dict) or set(value)!=set(ROLES):
        raise InvalidRequest('Resume intent requires original_goal/latest_request/latest_correction anchors')
    rows={r['id']:r for r in bundle.ledger['requirements']};anchors={}
    for role in ROLES:
        a=value[role]
        if a is None and role=='latest_correction':continue
        if not isinstance(a,dict) or set(a)!={'requirement_id','start','end'}:
            raise InvalidRequest('Each declared intent needs a requirement ID and exact byte range')
        identifier(a['requirement_id'])
        row=rows.get(a['requirement_id'])
        if row is None or role=='original_goal' and row['kind']!='goal' or role!='original_goal' and row['status']!='active':
            raise InvalidRequest('Intent refers to an unknown or superseded current requirement')
        start,end=a['start'],a['end'];integer(start);integer(end,1)
        source=bundle.sources[row['source']['path']]
        if end<=start or end>len(source):raise InvalidRequest('Intent range is outside its exact source')
        try:text=source[start:end].decode('utf-8')
        except UnicodeError:raise InvalidRequest('Intent range must preserve UTF-8 character boundaries') from None
        if text!=row['source']['quote']:raise InvalidRequest('Intent range does not match the requirement quote')
        anchors[role]={'requirement_id':row['id'],'revision':row['revision'],'status':row['status'],
                       'statement':row['statement'],'path':row['source']['path'],'start':start,'end':end,'quote':text}
    if len(canonical(anchors).encode())>16384:raise InvalidRequest('Declared intent exceeds its 16384 byte limit; select precise source spans')
    return anchors


def publish(bundle,anchors,sources,prefix,artifact,selections):
    if anchors is None:return None
    from .learning_origin import origin
    result={'schema':'contextrail.resume-intent.v1','status':'anchored','anchors':{},
            'effective_request':anchors['latest_request']['statement'],'next_action':anchors['latest_request']['statement'],
            'selection':'host_declared_not_semantically_machine_proven','coverage':'declared_anchors_only_not_all_user_history',
            'uninspected_history':True,
            'relationships':[{'id':r['id'],'revision':r['revision'],'status':r['status'],'superseded_by':r.get('superseded_by')}
                             for r in bundle.ledger['requirements']]}
    refs=set()
    for role,item in anchors.items():
        source=sources[item['path']];ref=Ref(source['name'],source['revision'],item['start'],item['end'])
        if ref not in refs:
            selections.append(Selection(ref,required=True,priority=100));refs.add(ref)
        result['anchors'][role]={**item,'ref':asdict(ref),'range_unit':'source_utf8_bytes'}
    result['loss']=report('declared_resume_anchors','anchors',total=len(ROLES),selected=len(anchors),unknown=3-len(anchors),
                          scope='explicit_anchor_roles_only; complete_user_history_unknown')
    start='# START — 当前任务恢复\n\n'
    for role,item in anchors.items():start+=role+' ('+item['requirement_id']+' rev '+str(item['revision'])+', '+item['status']+')\n'+item['statement']+'\n原文：'+item['quote']+'\n\n'
    start+='下一步（宿主明确标注）：'+result['next_action']+'\n\n完整意图来源清单（context.get）：'+prefix+'/resume-intent @ revision 1\n\n替代关系：\n'
    for r in result['relationships']:
        if r['superseded_by']:start+=r['id']+' -> '+r['superseded_by']+'\n'
    result['origin']=origin('resume-intent:'+bundle.ledger['snapshot_id'],[asdict(r) for r in refs],'bridge_projection',False)
    result['start_ref']=artifact(prefix+'/START.md',start.encode(),media='text/markdown',required=True,priority=100)
    # START and precise source windows carry mandatory intent. The full source
    # inventory is a cold metadata artifact, avoiding a second embedded copy.
    ref=artifact(prefix+'/resume-intent',canonical(result).encode(),selected=False)
    return {**result,'ref':ref}


def project_native_user(home,workspace,client,native_session,event_id,*,start=0,end=None,output=None,max_bytes=65536):
    """Original M is the only capture owner. This is a selected read/export."""
    from .event_stream import get_event
    from .source_events import workspace_key
    integer(start);integer(max_bytes,1)
    if end is not None:integer(end)
    if max_bytes>16*1024*1024:raise InvalidRequest('User projection cannot exceed 16 MiB')
    try:
        path=(Path(home)/'state.sqlite').resolve(strict=True)
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
            db.row_factory=sqlite3.Row
            event=get_event(db,workspace_key(str(Path(workspace).resolve())),client,native_session,event_id)
    except (OSError,sqlite3.Error):raise Unavailable('Original memory event source is unavailable') from None
    except ValueError:raise InvalidRequest('Exact source event is not available in the selected native session') from None
    if event.get('role')!='user' or not isinstance(event.get('data',{}).get('text'),str):
        raise AccessDenied('Only explicitly selected native user text can supply a user-intent source')
    raw=event['data']['text'].encode();end=len(raw) if end is None else end
    if start>end or end>len(raw) or end-start>max_bytes:raise InvalidRequest('User source window is outside its declared boundary')
    try:text=raw[start:end].decode('utf-8')
    except UnicodeError:raise InvalidRequest('User source window cuts a UTF-8 character') from None
    source={'reader':'memory.stream_get','workspace':str(Path(workspace).resolve()),'client':client,
            'native_session':native_session,'event_id':event_id,'start':start,'end':end,
            'range_unit':'processed_user_text_utf8_bytes','total_bytes':len(raw)}
    if output is not None:
        with Path(output).open('xb') as stream:stream.write(raw)
        source['exported_full_text']=str(Path(output).resolve())
    return {'schema':'contextrail.user-intent-source.v1','text':text,'source':source,'trust':'host_selected_historical_user_data',
            'loss':report('native_user_text','processed_utf8_bytes',total=len(raw),selected=end-start,unknown=0,
                           scope='this_exact_event_only_not_all_user_requests',recover={'reader':'memory.stream_get','event_id':event_id})}
