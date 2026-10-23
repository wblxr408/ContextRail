"""Exact snapshot brief, coverage diagnostics and model-free load explanation."""
from dataclasses import asdict
import json
from pathlib import Path

from .errors import AccessDenied,BudgetExceeded,InvalidRequest,Unavailable
from .models import Packet,Ref,canonical,digest,integer
from .loss import report
from ._vendor.ai_memory_brief import excerpt_from_page,assign_tiers

LEVEL={'directory':0,'L0':1,'L1':2,'L2':3}
MAX_EXPANSION=1024*1024


def _loss_counts(value,depth=0):
    if not isinstance(value,dict) or depth>3:return None
    keys=('source','unit','scope','total','selected','omitted','truncated','unsupported','unknown')
    if any(k in value for k in ('total','selected','unknown')):return {k:value[k] for k in keys if k in value}
    return {k:_loss_counts(v,depth+1) for k,v in value.items() if isinstance(v,dict)}


def trace(store,scope,snapshot,offset,limit):
    actions=store.db.execute('SELECT id,state FROM actions WHERE scope=? ORDER BY id LIMIT ? OFFSET ?',(scope.key,limit,offset)).fetchall()
    total=store.db.execute('SELECT count(*) FROM actions WHERE scope=?',(scope.key,)).fetchone()[0]
    rows=store.db.execute('SELECT sequence,kind,at FROM events WHERE scope=? ORDER BY sequence DESC LIMIT ? OFFSET ?',(scope.key,limit,offset)).fetchall()
    count=store.db.execute('SELECT count(*) FROM events WHERE scope=?',(scope.key,)).fetchone()[0]
    row=store.db.execute("SELECT sequence,at,metadata FROM events WHERE scope=? AND kind='native_context.prepared' ORDER BY sequence DESC LIMIT 1",(scope.key,)).fetchone()
    prepared=None
    if row:
        meta=json.loads(row['metadata']);refs=meta.get('selected_refs')
        prepared={'sequence':row['sequence'],'at':row['at'],'metadata':{k:meta[k] for k in ('snapshot','client','returned_bytes','memory_status','memory_chars','memory_truncated','selection_total') if k in meta},
                  'loss':_loss_counts(meta.get('loss')),
                  'selected_refs':refs[offset:offset+limit] if isinstance(refs,list) else None,
                  'selected_total':len(refs) if isinstance(refs,list) else None,
                  'next_offset':offset+limit if isinstance(refs,list) and offset+limit<len(refs) else None,
                  'scope':'historical_prepared_receipt_not_model_consumption'}
    return {'view':'newly_generated_for_explicit_snapshot','model_calls':0,'model_consumption':'unknown',
            'actions':{'entries':[dict(r) for r in actions],'total':total,'offset':offset,'next_offset':offset+len(actions) if offset+len(actions)<total else None},
            'steps':{'entries':[dict(r) for r in rows],'total':count,'offset':offset,'next_offset':offset+len(rows) if offset+len(rows)<count else None},
            'timeline':{'tool':'context.timeline','arguments':{'limit':5},'scope':'task_owned_evidence_live_view','not_a_full_native_transcript':True},
            'working_scene':{'tool':'context.workscene','arguments':{'limit':5},'scope':'source_linked_observations_tasks_and_host_handles','automatic_retry':False},
            'last_prepared':prepared,'continue_trace':{'tool':'context.explain','arguments':{'snapshot':snapshot,'trace_offset':offset+limit,'trace_limit':limit}}}


def compile_brief(compiler,scope,snapshot,session,*,budget,requested=(),offset=0,limit=20,trace_offset=0,trace_limit=5):
    integer(budget,1);integer(offset);integer(limit,1);integer(trace_offset);integer(trace_limit,1)
    if limit>100 or trace_limit>20 or offset>1000000 or trace_offset>1000000:raise InvalidRequest('Brief page limits exceeded')
    if not isinstance(requested,(tuple,list)) or any(not isinstance(r,Ref) for r in requested) or len(set(requested))!=len(requested):raise InvalidRequest('Requested pages must be unique exact refs')
    store=compiler.store;target=store.session(scope,session);state=store.load_snapshot(scope,snapshot)
    entries=state.pop('evidence');groups=state.pop('selection_groups',[])
    entries.sort(key=lambda e:(not e['required'],-e['priority'],canonical(e['ref'])))
    refs=[Ref(**e['ref']) for e in entries];by_ref=dict(zip(refs,entries))
    if any(r not in by_ref for r in requested):raise AccessDenied('Requested evidence is outside this exact snapshot')
    # Union all overlapping groups before promotion; components own L2 atomicity.
    parent={r:r for r in refs}
    def find(r):
        while parent[r]!=r:parent[r]=parent[parent[r]];r=parent[r]
        return r
    group_ids={r:[] for r in refs};group_required=set()
    for group in groups:
        members=[Ref(**r) for r in group['members']]
        for r in members:
            parent[find(r)]=find(members[0]);group_ids[r].append(group['id'])
            if group.get('required'):group_required.add(r)
    components={}
    for r in refs:components.setdefault(find(r),[]).append(r)
    must={r for r in refs if by_ref[r]['required']}|set(requested)|group_required
    forced_roots={find(r) for r in must};forced={r for root in forced_roots for r in components[root]}
    requested_roots={find(r) for r in requested}
    optional=[r for r in refs if not by_ref[r]['required']]
    page=optional[offset:offset+limit]
    displayed=list(dict.fromkeys([*page,*(r for r in refs if r in forced and not by_ref[r]['required'])]))
    exposed=set(displayed)|{r for r in refs if by_ref[r]['required']}
    variants={};unsupported=set();tiers={r:'directory' for r in exposed};reasons={}
    for r in exposed:
        artifact=store.get(scope,r);raw=artifact.content
        base={'ref':asdict(r),'tier':'directory','reason':'optional_budget','priority':by_ref[r]['priority'],
              'required':by_ref[r]['required'],'groups':group_ids[r],'trust':'untrusted_evidence','source_bytes':len(raw),'read_tool':'context.get'}
        variants[r]={'directory':base}
        try:
            if len(raw)>MAX_EXPANSION:raise InvalidRequest('expansion_source_over_1mib')
            text=raw.decode('utf-8')
        except UnicodeError:
            unsupported.add(r);reasons[r]='non_utf8_text';continue
        except InvalidRequest:
            unsupported.add(r);reasons[r]='expansion_source_over_1mib';continue
        front={};prose=text
        if text.startswith('---\n') or text.startswith('---\r\n'):
            lines=text.splitlines();end=next((i for i in range(1,len(lines)) if lines[i].strip()=='---'),None)
            if end is not None:prose='\n'.join(lines[end+1:])
        elif artifact.media_type=='application/json':
            try:
                loaded=json.loads(text)
                if isinstance(loaded,dict):front=loaded
            except ValueError:pass
        l0,l1=excerpt_from_page(front,prose)
        variants[r]['L2']={**base,'tier':'L2','text':text,'media_type':artifact.media_type,'preview':False}
        for tier,excerpt in (('L0',l0),('L1',l1)):
            if excerpt:variants[r][tier]={**base,'tier':tier,'text':excerpt,'media_type':artifact.media_type,'preview':True,'reason':'coverage_before_depth'}
    for r in forced:
        if 'L2' not in variants[r]:raise InvalidRequest('Required/requested evidence '+r.name+' cannot be injected whole; '+reasons.get(r,'unsupported'))
        tiers[r]='L2'
    bridge=None;manifest=None;bridge_rows=[]
    manifests=[r for r in refs if r.name.startswith('bridge/') and '/exports/' in r.name and r.name.endswith('/manifest')]
    if manifests:
        if len(manifests)!=1:raise InvalidRequest('Brief requires exactly one Bridge authority')
        from .bridge import BridgeEvidence
        owner=BridgeEvidence(store,scope,session);manifest,bundle,manifest_ref=owner._resolve(snapshot)
        bridge={'manifest_ref':asdict(manifest_ref),'bridge_snapshot':manifest['snapshot_id'],'bridge_scope':manifest['bridge_scope'],
                'required_ids':manifest['required_ids'],'source_current':True}
        for row in bundle.ledger['requirements']:
            if row['status']=='active':bridge_rows.append(row)
    explanation=trace(store,scope,snapshot,trace_offset,trace_limit)

    def rendered():
        selected={r for r in exposed if tiers[r]=='L2'}
        def material(r):
            value={**variants[r][tiers[r]]}
            if r in forced:
                value['reason']='requested_group' if find(r) in requested_roots and len(components[find(r)])>1 else ('requested' if r in requested else 'required_group' if len(components[find(r)])>1 else 'required')
            elif len(components[find(r)])>1 and any(member in unsupported for member in components[find(r)]):
                value['reason']='group_contains_unsupported'
                if r in reasons:value['unsupported_reason']=reasons[r]
            elif r in reasons:value['reason']=reasons[r]
            elif tiers[r]=='L2':value['reason']='optional_selected'
            return value
        optional_rows=[material(r) for r in displayed]
        required_rows=[material(r) for r in refs if by_ref[r]['required']]
        navigation={'tool':'context.brief','arguments':{'snapshot':snapshot,'offset':offset,'limit':limit},'total':len(optional),
                    'offset':offset,'shown':len([r for r in page if r in exposed]),
                    'next_offset':offset+len(page) if offset+len(page)<len(optional) else None,
                    'list_tool':'context.list','read_tool':'context.get','range_unit':'bytes'}
        losses={'task_evidence':report('task_evidence','refs',total=len(refs),selected=len(selected),
                    truncated=sum(tiers[r] in ('L0','L1') for r in exposed),unsupported=len(unsupported&exposed),unknown=len(refs)-len(exposed),
                    reasons={'not_in_directory_page':len(refs)-len(exposed),'body_not_loaded':len(refs)-len(selected)},scope='declared_snapshot',recover=navigation),
                'native_history':report('native_history','events',reasons={'not_selected_by_snapshot':None},scope='outside_snapshot',recover={'tool':'context.query'}),
                'main_history':report('main_history','notes',reasons={'not_queried_by_brief':None},scope='outside_snapshot',recover={'tool':'context.query'})}
        origin=None
        if bridge:
            rows=[];loaded=0
            for row in bridge_rows:
                binding=manifest['requirements'][row['id']];r=Ref(**binding['ref']);included=r in selected;loaded+=included
                reason='required' if binding['required'] else 'optional_selected' if included else 'outside_bridge_scope' if row['scope']!=manifest['bridge_scope'] and not row['always_load'] else 'optional_budget'
                rows.append({'id':row['id'],'revision':row['revision'],'ref':binding['ref'],'loaded':included,'reason':reason})
            origin={**bridge,'requirements':rows}
            losses['bridge_requirements']=report('bridge_requirements','requirements',total=len(rows),selected=loaded,unknown=0,
                reasons={'active_not_loaded':len(rows)-loaded},scope='active_ledger_requirements',recover={'reader':'context.get','ref':manifest['ledger_ref']})
        from .learning_origin import origin as make_origin
        return {'schema':'contextrail.brief.v1','snapshot':snapshot,'scope':asdict(scope),'target':asdict(target),'task':state,
                'origin':make_origin('rail-brief:'+snapshot,['rail-snapshot:'+snapshot],'context_brief',False),
                'required':required_rows,'optional':optional_rows,'navigation':navigation,'loss':losses,'bridge':origin,'explanation':explanation,
                'budget':budget,'unit':compiler.unit,'preview_boundary':'L0/L1_are_extracts_not_full_evidence; only_L2_loaded_whole'}
    def size():return compiler._size(canonical(rendered()))
    # Compact trace first; fixed task/required data is never silently removed.
    while size()>budget:
        reduced=False
        for name in ('steps','actions'):
            value=explanation[name]
            if value['entries']:
                value['entries'].pop();value['next_offset']=value['offset']+len(value['entries']);reduced=True;break
        if reduced:continue
        last=explanation.get('last_prepared')
        if last and last.get('selected_refs'):
            last['selected_refs'].pop();last['next_offset']=trace_offset+len(last['selected_refs']);continue
        candidates=[r for r in page if r not in forced and r in exposed]
        if not candidates:raise BudgetExceeded('Required evidence, loss and navigation exceed brief budget; increase it')
        r=candidates[-1];exposed.remove(r);displayed.remove(r);page.remove(r)
    def promote(root,tier):
        members=components[root]
        if tier!='L2':members=[r for r in members if r in exposed]
        elif any(r not in exposed or r in unsupported for r in members):return False
        if any(r in unsupported for r in components[root]):return False
        if not members:return False
        if any(r not in variants or tier not in variants[r] for r in members):return False
        if all(LEVEL[tiers[r]]>=LEVEL[tier] for r in members):return True
        if sum(len(variants[r][tier].get('text','').encode()) for r in members)>budget:return False
        previous={r:tiers[r] for r in members}
        for r in members:
            if LEVEL[tiers[r]]<LEVEL[tier]:tiers[r]=tier
        if size()<=budget:return True
        tiers.update(previous);return False
    candidates=[]
    for root,members in components.items():
        if root in forced_roots or not any(r in exposed for r in members):continue
        costs={tier:sum(len(variants[r].get(tier,variants[r]['directory']).get('text','').encode()) for r in members if r in variants) for tier in ('L0','L1','L2')}
        candidates.append((root,costs))
    assign_tiers(candidates,promote)
    value=rendered();body=canonical(value);selected=tuple(r for r in refs if r in exposed and tiers[r]=='L2')
    return Packet(snapshot,target,body,digest(body.encode()),compiler._size(body),budget,compiler.unit,selected,tuple(r for r in refs if r not in selected))


def state_markdown(value):
    header='---\nmanaged_by: context-rail\ntype: context_state_projection\nsource_snapshot: '+json.dumps(value['snapshot'],ensure_ascii=False)+'\n---\n\n'
    return header+'# ContextRail recovery STATE\n\n本文件记录生成时的精确快照与覆盖缺口；prepared不证明模型消费，短述不代表完整恢复。\n\n```json\n'+json.dumps(value,ensure_ascii=False,indent=2)+'\n```\n'


def export_state(value,path):
    try:
        with Path(path).open('x',encoding='utf-8',newline='\n') as stream:stream.write(state_markdown(value))
    except FileExistsError:raise InvalidRequest('STATE destination already exists; choose a new file') from None
    except OSError:raise Unavailable('STATE destination could not be written') from None
