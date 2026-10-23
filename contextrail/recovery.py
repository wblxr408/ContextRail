"""Host-only exact recovery. Reading candidates never claims another task."""
from dataclasses import asdict
import json

from .context import Compiler
from .bridge import BridgeEvidence
from .errors import AccessDenied, Conflict, InvalidRequest, NotFound, RailError, StaleSnapshot
from .intent import UNKNOWN
from .models import IndexPolicy, Ref, canonical, identifier, integer
from .navigation import TaskNavigation


class Recovery:
    def __init__(self,store,config,session):
        self.store,self.config,self.scope,self.session=store,config,config.scope,session
        store.session(self.scope,session)

    def _record(self,kind,key,value):
        return self.store.db.execute('SELECT metadata FROM events WHERE scope=? AND kind=? AND json_extract(metadata,?)=? ORDER BY sequence DESC LIMIT 1',
            (self.scope.key,kind,key,value)).fetchone()

    def _fresh(self,sid,version):
        identifier(sid);integer(version)
        state=self.store.load_snapshot(self.scope,sid)
        if state['version']!=version:raise StaleSnapshot('Exact task version differs; select and reconcile a specific candidate')
        nav=TaskNavigation(self.store,self.config,self.session);source=nav._source(sid)
        intent=dict(UNKNOWN);manifest=None
        if source['kind']=='bridge':
            manifest,_,_=BridgeEvidence(self.store,self.scope,self.session)._resolve(sid)
            intent=manifest.get('resume_intent',dict(UNKNOWN))
        return state,source,intent,manifest

    def bind(self,snapshot,version,lease,*,client,native_session,binding_id):
        for x in (client,native_session,binding_id):identifier(x)
        if client not in {'codex','claude'}:raise InvalidRequest('Unsupported native client identity')
        if lease.session!=self.session:raise AccessDenied('Only the bound owner can register native recovery identity')
        with self.store.transaction():
            self.store._lease(self.scope,lease);self._fresh(snapshot,version)
            value={'binding_id':binding_id,'task_scope':asdict(self.scope),'snapshot':snapshot,'version':version,
                   'client':client,'native_session':native_session}
            prior=self._record('resume.binding','$.binding_id',binding_id)
            if prior:
                if json.loads(prior['metadata'])!=value:raise Conflict('Native binding ID was reused with a different identity')
            else:self.store._event(self.scope,'resume.binding',**value)
            return value

    def candidates(self,*,snapshot=None,version=None,client=None,native_session=None,offset=0,limit=20):
        if snapshot is not None:identifier(snapshot)
        if version is not None:integer(version)
        if (client is None)!=(native_session is None):raise InvalidRequest('Native identity requires both client and exact session')
        if client is not None:
            identifier(client);identifier(native_session)
        integer(offset);integer(limit,1)
        if limit>100:raise InvalidRequest('Candidate page size cannot exceed 100')
        with self.store.read_transaction():
            self.store.session(self.scope,self.session);task=self.store.task(self.scope)
            where='scope=?';args=[self.scope.key]
            if version is not None:where+=' AND version=?';args.append(version)
            mappings=[]
            if client is not None:
                records=self.store.db.execute("SELECT sequence,metadata FROM events WHERE scope=? AND kind IN ('resume.binding','native_context.prepared') AND json_extract(metadata,'$.client')=? AND json_extract(metadata,'$.native_session')=? ORDER BY sequence DESC LIMIT 200",
                                               (self.scope.key,client,native_session)).fetchall()
                mappings=[json.loads(r['metadata']) for r in records]
                snapshots=sorted({r['snapshot'] for r in mappings})
                if snapshots:where+=' AND id IN ('+','.join('?' for _ in snapshots)+')';args.extend(snapshots)
                else:where+=' AND 0'
            total=self.store.db.execute('SELECT count(*) FROM snapshots WHERE '+where,args).fetchone()[0]
            rows=self.store.db.execute('SELECT id,version FROM snapshots WHERE '+where+' ORDER BY rowid DESC LIMIT ? OFFSET ?',[*args,limit,offset]).fetchall()
            filter_total=total
            if total==0 and (version is not None or client is not None):
                # Alternatives are visible data, never a substituted selection.
                total=self.store.db.execute('SELECT count(*) FROM snapshots WHERE scope=?',(self.scope.key,)).fetchone()[0]
                rows=self.store.db.execute('SELECT id,version FROM snapshots WHERE scope=? ORDER BY rowid DESC LIMIT ? OFFSET ?',
                    (self.scope.key,limit,offset)).fetchall()
            nav=TaskNavigation(self.store,self.config,self.session);entries=[]
            for row in rows:
                entry={'scope':asdict(self.scope),'snapshot':row['id'],'version':row['version'],
                       'matches_requested_identity':filter_total>0 and (snapshot is None or row['id']==snapshot),
                       'store_current':row['version']==task['version'],'source_current':'not_checked',
                       'native_bindings':[m for m in mappings if m['snapshot']==row['id']],
                       'resume_intent':dict(UNKNOWN)}
                try:
                    source=nav._source(row['id']);entry['source']=source
                    if source['kind']=='bridge':
                        manifest=json.loads(self.store.get(self.scope,Ref(**source['ref'])).content)
                        entry['resume_intent']=manifest.get('resume_intent',dict(UNKNOWN))
                        entry['requirements']=[{'id':rid,'revision':v['revision']} for rid,v in manifest['requirements'].items() if v['required']]
                except RailError as exc:entry['error']={'code':exc.code,'message':str(exc)}
                entries.append(entry)
            selected=None;status='selection_required'
            if filter_total==0:status='not_found'
            elif snapshot is not None:
                matches=[e for e in entries if e['snapshot']==snapshot]
                # An exact selection outside the page is located directly, never replaced.
                if not matches and total>len(entries):
                    exact=self.store.db.execute('SELECT id FROM snapshots WHERE '+where+' AND id=?',[*args,snapshot]).fetchone()
                    if exact:status='selection_outside_page'
                    else:status='not_found'
                elif matches:status='matched';selected=matches[0]
                else:status='not_found'
            elif total==0:status='not_found'
            return {'schema':'contextrail.resume-candidates.v1','status':status,'scope':asdict(self.scope),'selected':selected,
                    'fallback_used':False,'candidates':entries,'total':total,'filter_total':filter_total,'offset':offset,
                    'next_offset':offset+len(entries) if offset+len(entries)<total else None,
                    'loss':{'scope':'bound_task_only; no_project_latest_fallback','outside_page':max(0,total-offset-len(entries)),
                            'unmapped_native_history':'unknown','source_freshness':'not_checked_until_preview'}}

    def preview(self,snapshot,version,*,budget=8192,client=None,native_session=None):
        state,source,intent,manifest=self._fresh(snapshot,version)
        if client is not None or native_session is not None:
            selected=self.candidates(snapshot=snapshot,version=version,client=client,native_session=native_session)
            if selected['status']!='matched':raise NotFound('Exact native session is not mapped to the selected checkpoint')
        result=TaskNavigation(self.store,self.config,self.session).resume(snapshot,budget=budget)
        return {**result,'version':state['version'],'resume_intent':intent,'ownership':'preview_only',
                'workspace_writer':'not_acquired_by_rail_preview_or_lease','source_current':True if manifest else 'not_checked'}

    def _offer(self,operation_id):
        identifier(operation_id);row=self._record('resume.offered','$.request.operation_id',operation_id)
        if row is None:raise NotFound('Exact resume offer is not available in this task')
        return json.loads(row['metadata'])

    def offer(self,snapshot,version,lease,target,*,operation_id,budget=8192):
        identifier(operation_id);integer(budget,1)
        if lease.session!=self.session:raise AccessDenied('Offer belongs to a different source session')
        _,_,intent,manifest=self._fresh(snapshot,version)
        if intent['status']!='anchored':raise InvalidRequest('Declare current intent before offering a writable recovery')
        request={'operation_id':operation_id,'snapshot':snapshot,'version':version,'source':asdict(lease),'target':asdict(target),'budget':budget}
        prior=self._record('resume.issued','$.offer_id',operation_id)
        if prior:
            value=json.loads(prior['metadata'])
            if value['request']!=request:raise Conflict('Resume offer ID already has different parameters')
            h=self.store.handoff(self.scope,value['result']['handoff']);task=self.store.task(self.scope)
            if h.phase=='activated':self.store.activate(self.scope,h.id)
            elif h.phase=='aborted' or task['handoff']!=h.id:raise Conflict('Resume offer is no longer pending')
            return value['result']
        h=self.store.prepare(self.scope,lease,snapshot,target,resume_operation=request)
        try:packet=Compiler(self.store).for_handoff(self.scope,h.id,budget=budget,index=IndexPolicy(max_entries=5))
        except (Conflict,AccessDenied):
            # Another retry may have issued/delivered the same offer meanwhile.
            issued=self._record('resume.issued','$.offer_id',operation_id)
            if issued:
                prior_value=json.loads(issued['metadata'])
                if prior_value['request']==request:
                    self._fresh(snapshot,version)
                    current=self.store.handoff(self.scope,h.id)
                    if current.phase=='activated':self.store.activate(self.scope,h.id)
                    elif current.phase=='aborted' or self.store.task(self.scope)['handoff']!=h.id:raise
                    return prior_value['result']
            raise
        required=[{'id':rid,'revision':item['revision']} for rid,item in manifest['requirements'].items() if item['required']]
        result={'schema':'contextrail.resume-offer.v1','offer_id':operation_id,'snapshot':snapshot,'version':version,
                'handoff':h.id,'target_session':target.session,'required':required,'packet':asdict(packet),
                'resume_intent':intent,'delivery':'not_acknowledged','workspace_writer':'requires_independent_bridge_claim'}
        result=json.loads(canonical(result))
        with self.store.transaction():
            self.store._active_handoff(self.scope,h.id)
            prior=self._record('resume.issued','$.offer_id',operation_id)
            if prior:
                existing=json.loads(prior['metadata'])
                if existing['request']!=request:raise Conflict('Concurrent resume offer has different parameters')
                return existing['result']
            self.store._event(self.scope,'resume.issued',offer_id=operation_id,request=request,result=result)
        return result

    def accept(self,operation_id,receipt):
        offered=self._offer(operation_id);request=offered['request'];h=self.store.handoff(self.scope,offered['handoff'])
        if self.session!=h.target.session:raise AccessDenied('Only the exact target session can accept this offer')
        self._fresh(request['snapshot'],request['version'])
        issued=self._record('resume.issued','$.offer_id',operation_id)
        if issued is None:raise Conflict('Offer has no issued packet; source must continue the exact offer')
        result=json.loads(issued['metadata'])['result']
        if not isinstance(receipt,dict) or set(receipt)!={'offer_id','snapshot','version','target_session','delivered','checks'}:
            raise InvalidRequest('Explicit delivery and per-requirement receipt are required')
        if receipt['offer_id']!=operation_id or receipt['snapshot']!=request['snapshot'] or receipt['version']!=request['version'] or receipt['target_session']!=self.session or receipt['delivered'] is not True:
            raise Conflict('Receipt identity or delivery differs from the exact issued offer')
        checks=receipt['checks'];expected={r['id']:r['revision'] for r in result['required']};seen=set()
        if not isinstance(checks,list):raise InvalidRequest('Requirement receipt must be a list')
        for item in checks:
            if not isinstance(item,dict) or set(item)!={'id','revision','interpretation','next_action'}:raise InvalidRequest('Invalid requirement receipt')
            rid=item['id']
            if not isinstance(rid,str) or rid in seen or rid not in expected or type(item['revision']) is not int or item['revision']!=expected[rid]:raise Conflict('Requirement receipt identity/version differs')
            if any(not isinstance(item[k],str) or not item[k].strip() for k in ('interpretation','next_action')):raise InvalidRequest('Each requirement needs receiver interpretation and next action')
            seen.add(rid)
        if seen!=set(expected):raise Conflict('Receipt misses current required IDs')
        if len(canonical(receipt).encode())>65536:raise InvalidRequest('Requirement receipt exceeds its byte limit')
        with self.store.transaction():
            prior=self._record('resume.receipt','$.offer_id',operation_id)
            if prior and json.loads(prior['metadata'])['receipt']!=receipt:raise Conflict('Acceptance retry differs from its original receipt')
            if not prior:self.store._event(self.scope,'resume.receipt',offer_id=operation_id,receipt=receipt)
        # Each original protocol phase is durable; retry continues after an interrupted phase.
        h=self.store.handoff(self.scope,h.id)
        if h.phase=='prepared':
            try:self.store.acknowledge(self.scope,h.id,self.session,result['packet']['sha256'])
            except (Conflict,AccessDenied):
                if self.store.handoff(self.scope,h.id).phase not in {'hydrated','validated','activated'}:raise
                self._fresh(request['snapshot'],request['version'])
        h=self.store.handoff(self.scope,h.id)
        if h.phase=='hydrated':
            try:self.store.validate(self.scope,h.id)
            except (Conflict,AccessDenied):
                if self.store.handoff(self.scope,h.id).phase not in {'validated','activated'}:raise
                self._fresh(request['snapshot'],request['version'])
        lease=self.store.activate(self.scope,h.id)
        return {'schema':'contextrail.resume-accepted.v1','offer_id':operation_id,'handoff':h.id,'snapshot':h.snapshot,
                'lease':asdict(lease),'requirements':'receiver_attested_not_semantically_machine_proven',
                'delivery':'host_attested_not_model_consumption','workspace_writer':'requires_independent_bridge_claim'}
