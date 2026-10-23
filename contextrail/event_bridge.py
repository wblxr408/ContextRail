"""Trusted host import from existing memory event evidence into a Rail task."""

from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from .errors import BudgetExceeded, Conflict, InvalidRequest, Unavailable
from .event_stream import list_events, get_event
from .models import Ref, canonical, integer
from .source_events import workspace_key
from .loss import report


def _import_report(entries,total,offset,imported):
    return {'schema':'contextrail.event-import.v1','entries':entries,'imported':imported,'total':total,
        'offset':offset,'next_offset':offset+len(entries) if offset+len(entries)<total else None,
        'order':'capture_sequence_not_occurrence_time','trust':'untrusted_source_data',
        'loss':{'native_import':report('native_import','events',total=total,selected=len(entries),unknown=total-len(entries),
            scope='this_selected_session_page_only',reasons={'outside_committed_page':total-len(entries)},
            recover={'command':'event-import','offset':offset+len(entries),'same_session_required':True})}}


def import_events(store,config,lease,memory_home,client,native_session,*,offset=0,limit=20,budget=8192):
    integer(offset);integer(limit,1);integer(budget,1)
    if limit>100 or budget<2048:
        raise InvalidRequest('Event import requires limit 1..100 and at least 2048 response bytes.')
    try:
        path=(Path(memory_home).resolve(strict=True)/'state.sqlite').resolve(strict=True)
    except OSError:
        raise Unavailable('Existing memory event evidence is unavailable.') from None
    entries=[];staged=[];stage_bytes=0;imported=0
    # The original store owns membership/freeze; the model gets only read refs.
    with store.transaction():
        store._lease(config.scope,lease)
        try:
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5)) as source:
                source.row_factory=sqlite3.Row
                source.execute('BEGIN')
                page=list_events(source,workspace_key(str(config.workspace)),client,native_session,offset=offset,limit=limit,budget=65536)
                for item in page['entries']:
                    event=get_event(source,workspace_key(str(config.workspace)),client,native_session,item['id'])
                    body=canonical(event).encode('utf-8')
                    prefix='native-events/'+event['id']
                    body_ref=Ref(prefix+'/body',1);manifest_ref=Ref(prefix+'/manifest',1)
                    manifest={'schema':'contextrail.imported-event.v1','event_id':event['id'],'source_sequence':item['sequence'],
                              'client':client,'native_session':native_session,'workspace':str(config.workspace),
                              'role':event['role'],'event_type':event['event_type'],'turn_id':event['turn_id'],
                              'call_id':event['call_id'],'occurred_at':event['occurred_at'],'source_time_state':event['source_time_state'],
                              'observed_at':event['observed_at'],'capture':event['capture'],'source':event['source'],
                              'body_ref':asdict(body_ref),'range_unit':'processed_utf8_bytes','trust':'untrusted_source_data'}
                    # Old imported revisions remain byte-bound; only new source
                    # events carrying origin receive this additional projection.
                    if event.get('origin'):manifest['origin']={**event['origin'],'history_origin':'native_event_projection','eligible_for_learning':False}
                    encoded_manifest=canonical(manifest).encode('utf-8')
                    if stage_bytes+len(body)+len(encoded_manifest)>16*1024*1024:
                        if not entries:raise InvalidRequest('Event evidence exceeds the bounded import stage.')
                        break
                    head=store.db.execute('SELECT revision FROM heads WHERE scope=? AND name=?',(config.scope.key,body_ref.name)).fetchone()
                    status='existing'
                    if head:
                        if head[0]!=1 or store.get(config.scope,body_ref).content!=body or store.get(config.scope,manifest_ref).content!=encoded_manifest:
                            raise Conflict('Imported source event is already bound to different task evidence.')
                    else:
                        staged.extend([{'name':body_ref.name,'content':body},{'name':manifest_ref.name,'content':encoded_manifest}])
                        status='imported'
                    entry={'event_id':event['id'],'event_type':event['event_type'],'role':event['role'],
                           'status':status,'body_ref':asdict(body_ref),'manifest_ref':asdict(manifest_ref)}
                    # Reserve the complete envelope before any publication.
                    candidate=_import_report(entries+[entry],page['total'],offset,imported+int(status=='imported'))
                    if len(canonical(candidate).encode('utf-8'))>budget:
                        if status=='imported':del staged[-2:]
                        if not entries:raise BudgetExceeded('Event import metadata exceeds response budget.')
                        break
                    entries.append(entry);stage_bytes+=len(body)+len(encoded_manifest);imported+=int(status=='imported')
                for artifact in staged:
                    store._put(config.scope,artifact['name'],artifact['content'],expected_revision=0,media_type='application/json')
        except (sqlite3.Error,OSError):
            raise Unavailable('Selected memory event evidence could not be read.') from None
        except ValueError:
            raise InvalidRequest('Native event selection or evidence is invalid.') from None
        return _import_report(entries,page['total'],offset,imported)
