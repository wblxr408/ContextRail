"""Source-bound product records in original Rail artifacts, formatted by claude-mem."""
from dataclasses import asdict
import json
from pathlib import Path

from .claude_mem import invoke
from .errors import AccessDenied, BudgetExceeded, Conflict, InvalidRequest, RailError
from .models import Ref, canonical, identifier, integer
from .sanitization import sanitize_capture


def _products(fields,source=None,at=None,limit=1000):
    return invoke('work-products',groups=[{'fields':fields,'sourceRef':source,'atEpoch':at}],limit=limit)


def _fingerprint(path,roots):
    target=Path(path).resolve()
    if not any(target.is_relative_to(Path(root)) for root in roots):raise AccessDenied('Product path resolves outside the explicitly registered artifact roots.')
    try:
        stat=target.stat()
        if not target.is_file():raise InvalidRequest('Registered product path must name a regular file.')
        return {'resolved':str(target),'device':stat.st_dev,'file_id':stat.st_ino,'bytes':stat.st_size,
            'mtime_ns':stat.st_mtime_ns,'ctime_ns':stat.st_ctime_ns}
    except FileNotFoundError:return None


def register(store,scope,lease,workspace,payload,*,allowed_roots=()):
    if not isinstance(payload,dict) or payload.keys()-{'registration_id','source','fields','expected_revision','snapshot'}:
        raise InvalidRequest('Registration requires identity, exact source and work-product fields.')
    identity=payload.get('registration_id');identifier(identity)
    if any(char in identity for char in '/\\') or len(identity)>128:raise InvalidRequest('Registration identity must be a simple label up to 128 characters.')
    fields=payload.get('fields')
    if not isinstance(fields,dict) or len(canonical(fields).encode('utf-8'))>32768:raise InvalidRequest('Provide bounded work-product fields up to 32 KiB.')
    expected=payload.get('expected_revision',0);integer(expected)
    try:source=Ref(**payload['source'])
    except (TypeError,KeyError):raise InvalidRequest('An exact source Ref is required.') from None
    roots=[str(Path(root).resolve()) for root in (workspace,*allowed_roots)]
    safe=sanitize_capture(fields,'evidence').value
    products=_products(safe)
    if not products:raise InvalidRequest('No safe supported product path or HTTP(S) link was supplied.')
    normalized=[]
    for product in products:
        value={k:product[k] for k in ('kind','title','url','path') if k in product}
        if 'path' in value:
            # Validate scope before publication; a missing source remains a declared missing file.
            _fingerprint(value['path'],roots)
        normalized.append(value)
    request={'fields':{'work_products':normalized},'source':asdict(source),'roots':roots,'snapshot':payload.get('snapshot')}
    name='deliverables/'+identity+'/record'
    with store.transaction():
        task=store._lease(scope,lease);store.get(scope,source)
        if payload.get('snapshot'):store.load_snapshot(scope,payload['snapshot'],require_current=False)
        head=store.db.execute('SELECT revision FROM heads WHERE scope=? AND name=?',(scope.key,name)).fetchone()
        if head:
            prior_ref=Ref(name,head[0]);prior=json.loads(store.get(scope,prior_ref).content)
            if expected==0 and prior['request']==request:
                return {'record_ref':asdict(prior_ref),'duplicate':True,'task':scope.task,'publication_state':'not_inferred'}
        observed={p['path']:_fingerprint(p['path'],roots) for p in normalized if 'path' in p}
        if head and prior['request']==request and prior['file_observations']==observed:
            return {'record_ref':asdict(prior_ref),'duplicate':True,'task':scope.task,'publication_state':'not_inferred'}
        if (head[0] if head else 0)!=expected:raise Conflict('Product record changed; use its exact expected_revision for a new version.')
        record={'schema':'contextrail.deliverable-record.v1','registration_id':identity,'task':scope.task,'scope':asdict(scope),
            'task_version':task['version']+1,'source':asdict(source),'request':request,'file_observations':observed,
            'registered_at':store.clock(),'basis':'explicit_host_registration_not_publication_receipt'}
        ref=store._put(scope,name,canonical(record).encode('utf-8'),expected_revision=expected,media_type='application/json')
        store._event(scope,'deliverable.registered',record_ref=asdict(ref),source=asdict(source),products=len(products),publication_state='not_inferred')
    return {'record_ref':asdict(ref),'duplicate':False,'task':scope.task,'publication_state':'not_inferred'}


def _workspace(tools):
    from .federation import bound_sources
    binding=bound_sources(tools)
    view=getattr(tools,'config_view',None)
    return binding.workspace if binding else view['workspace'] if view else None


def _record(tools,ref):
    try:ref=Ref(**ref)
    except (ValueError,TypeError):raise InvalidRequest('Supply an exact product record Ref.') from None
    if not ref.name.startswith('deliverables/') or not ref.name.endswith('/record') or ref.start!=0 or ref.end is not None:
        raise InvalidRequest('Product detail requires a full exact registered record.')
    record=json.loads(tools.store.get(tools.scope,ref).content)
    if record.get('schema')!='contextrail.deliverable-record.v1' or record.get('scope')!=asdict(tools.scope):raise AccessDenied('Product record belongs to another task scope.')
    return record,ref


def _view(tools,product,record,ref):
    source_ref=Ref(**record['source']) if record.get('source') else None
    source_state='declaration_only'
    if source_ref:
        try:
            tools.store.get(tools.scope,source_ref)
            head=tools.store.db.execute('SELECT revision FROM heads WHERE scope=? AND name=?',(tools.scope.key,source_ref.name)).fetchone()
            source_state='current_revision' if head and head[0]==source_ref.revision else 'historical_revision'
        except RailError:source_state='unavailable'
    result={**{k:v for k,v in product.items() if k!='sourceRef'},'task':tools.scope.task,'task_version':record['task_version'],
        'source':record.get('source'),'source_state':source_state,'record_ref':ref,
        'ref':{'record_ref':ref,'index':product['sourceRef']['index']},
        'publication_state':'declared_link_not_verified_publication' if product.get('url') else 'local_file_registration',
        'trust':'source_declaration_not_current_task_policy'}
    if ref:
        head=tools.store.db.execute('SELECT revision FROM heads WHERE scope=? AND name=?',(tools.scope.key,ref['name'])).fetchone()
        result['record_state']='current_revision' if head and head[0]==ref['revision'] else 'historical_revision'
    else:
        result['record_state']='original_task_state_declaration'
    label=product.get('title','product').replace('[','(').replace(']',')').replace('\n',' ')
    if product.get('url'):
        result.update(availability={'state':'not_checked_remote'},markdown_link='['+label+']('+product['url']+')',open_url=product['url'])
    else:
        path=product['path'];roots=record['request']['roots']
        try:
            current=_fingerprint(path,roots);saved=record['file_observations'].get(path)
            state='missing' if current is None else 'appeared_unverified' if saved is None else 'metadata_matches' if current==saved else 'changed'
        except (OSError,RailError):state='unavailable_or_outside_registered_roots'
        result['availability']={'state':state,'basis':'filesystem_identity_size_mtime_ctime_not_content_verification'}
        result['registered_path']=path
        if state in {'metadata_matches','changed','appeared_unverified'}:
            result['open_path']=str(Path(path).resolve())
            result['open_target']='registered_metadata_match' if state=='metadata_matches' else 'current_file_not_verified_registered_version'
            result['markdown_link']='['+label+'](<'+Path(path).resolve().as_posix()+'>)'
        else:result.update(open_path=None,markdown_link=None)
    return result


def list_products(tools,*,offset=0,limit=20,budget=8192):
    integer(offset);integer(limit,1);integer(budget,1024)
    if offset>10000 or limit>100 or budget>tools.max_read_bytes:raise InvalidRequest('Product page exceeds bound limits.')
    tools.store.session(tools.scope,tools.session_id)
    rows=tools.store.db.execute("SELECT name,revision FROM heads WHERE scope=? AND name LIKE 'deliverables/%/record' ORDER BY rowid DESC LIMIT 129",(tools.scope.key,)).fetchall()
    groups=[];records={};errors=[]
    for row in rows[:128]:
        ref=asdict(Ref(row['name'],row['revision']))
        try:
            record,_=_record(tools,ref);key='record:'+canonical(ref);records[key]=(record,ref)
            groups.append({'fields':record['request']['fields'],'atEpoch':record['registered_at'],
                'sourceRef':{'kind':'registration','id':key}})
        except (RailError,ValueError,KeyError):errors.append({'record_ref':ref,'code':'registered_source_unavailable'})
    # Existing source-backed task state also projects through the same upstream field reader.
    workspace=_workspace(tools)
    if workspace:
        for item in tools.store.task_state(tools.scope):
            if len(groups)>=192:break
            try:fields=json.loads(item.text)
            except ValueError:continue
            if not isinstance(fields,dict):continue
            key='state:'+item.id
            records[key]=({'task_version':None,'source':asdict(item.sources[0]) if item.sources else None,
                'request':{'roots':[str(Path(workspace).resolve())]},'file_observations':{}},None)
            groups.append({'fields':fields,'atEpoch':item.created_at,'sourceRef':{'kind':'task_state','id':key}})
    products=invoke('work-products',groups=groups,limit=1000) if groups else []
    items=[]
    for product in products[offset:offset+limit]:
        key=product['sourceRef']['id'];record,ref=records[key]
        view=_view(tools,product,record,ref)
        if ref is None:view['ref']={'task_state_id':key.removeprefix('state:'),'index':product['sourceRef']['index']}
        items.append(view)
    result={'schema':'contextrail.deliverables.v1','items':items,'total_observed':len(products),
        'offset':offset,'next_offset':offset+len(items) if offset+len(items)<len(products) else None,
        'source_errors':errors,'coverage':{'record_scan_limit':128,'records_truncated':len(rows)>128,'external_publication_checked':False},'model_calls':0}
    while len(canonical(result).encode('utf-8'))>budget and result['items']:
        result['items'].pop();result['next_offset']=offset+len(result['items'])
    if len(canonical(result).encode('utf-8'))>budget or products[offset:offset+limit] and not result['items']:
        raise BudgetExceeded('Product references do not fit the requested page budget.')
    return result


def detail(tools,*,record_ref=None,index=0,task_state_id=None,budget=8192):
    integer(index);integer(budget,1024)
    if budget>tools.max_read_bytes or (record_ref is None)==(task_state_id is None):raise InvalidRequest('Choose one exact record or task-state identity within reader budget.')
    if record_ref is not None:
        record,ref=_record(tools,record_ref);exact=asdict(ref)
        products=_products(record['request']['fields'],{'kind':'registration','id':canonical(exact)})
    else:
        identifier(task_state_id);workspace=_workspace(tools)
        rows=[item for item in tools.store.task_state(tools.scope,active_only=False) if item.id==task_state_id]
        if not rows or not workspace:raise InvalidRequest('Exact task-state product is unavailable.')
        item=rows[0];record={'task_version':None,'source':asdict(item.sources[0]) if item.sources else None,
            'request':{'roots':[str(Path(workspace).resolve())]},'file_observations':{}}
        exact=None;products=_products(json.loads(item.text),{'kind':'task_state','id':item.id})
    product=next((p for p in products if p['sourceRef']['index']==index),None)
    if product is None:raise InvalidRequest('Exact product index is unavailable in this source version.')
    result=_view(tools,product,record,exact)
    if record_ref is None:result['ref']={'task_state_id':task_state_id,'index':index}
    result['source_read']={'tool':'context.get','arguments':record['source']} if record.get('source') else None
    if len(canonical(result).encode('utf-8'))>budget:raise BudgetExceeded('Product detail exceeds reader budget.')
    return result
