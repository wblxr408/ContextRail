"""Host-observed request accounting and durable read-side diagnostics."""
import base64
from dataclasses import asdict
import json
import sqlite3
import time
from uuid import uuid4

from .errors import BudgetExceeded,Conflict,InvalidRequest,RailError
from .models import Packet,canonical,identifier,integer
from .policy import UsageLedger

SCHEMA_V10='''
ALTER TABLE request_usage ADD COLUMN observation_json TEXT;
CREATE UNIQUE INDEX request_usage_observation ON request_usage(scope,session,json_extract(observation_json,'$.id'))
 WHERE observation_json IS NOT NULL;
'''


def count(value):
    if value is not None:
        integer(value)
        if value>2**63-1:raise InvalidRequest('Measurement exceeds supported integer range')
    return value


def openai_usage(value):
    if value is None:return {k:None for k in ('input_tokens','output_tokens','cached_input_tokens','cache_write_tokens','reasoning_output_tokens')}
    if not isinstance(value,dict):raise InvalidRequest('Usage must be an object or null')
    source={**value}
    for key in ('input_tokens_details','output_tokens_details'):
        if source.get(key) is not None and not isinstance(source[key],dict):raise InvalidRequest('Invalid usage details')
    incoming=source.get('input_tokens_details') or {};outgoing=source.get('output_tokens_details') or {}
    result={'input_tokens':count(source.get('input_tokens')),'output_tokens':count(source.get('output_tokens')),
            'cached_input_tokens':count(incoming.get('cached_tokens')),'cache_write_tokens':count(incoming.get('cache_write_tokens')),
            'reasoning_output_tokens':count(outgoing.get('reasoning_tokens'))}
    for sub,total in (('cached_input_tokens','input_tokens'),('reasoning_output_tokens','output_tokens')):
        if result[sub] is not None and result[total] is not None and result[sub]>result[total]:raise InvalidRequest('Usage subset exceeds its total')
    if source.get('total_tokens') is not None:
        total=count(source['total_tokens'])
        if result['input_tokens'] is not None and result['output_tokens'] is not None and total!=result['input_tokens']+result['output_tokens']:raise InvalidRequest('Usage total is inconsistent')
    return result


def _request_row(row):
    value=dict(row);raw=value.pop('observation_json',None)
    value['observation']=json.loads(raw) if raw else None
    return value


class HostAccounting:
    def __init__(self,store,scope,session):
        self.store,self.scope,self.session=store,scope,session
        store.session(scope,session)

    def record_request(self,packet,*,observation_id,final_request=None,components=None,reserves=None,
                       usage=None,usage_format=None,token_counter=None,counter_id=None,
                       request_id=None,run_id=None,attempt=1,latency_ms=None):
        identifier(observation_id)
        if not isinstance(packet,Packet) or packet.target.session!=self.session:raise InvalidRequest('Packet must belong to the bound accounting session')
        if final_request is not None and (not isinstance(final_request,bytes) or len(final_request)>16*1024*1024):raise InvalidRequest('Final request must be at most 16 MiB serialized bytes')
        components={} if components is None else components
        if not isinstance(components,dict) or components.keys()-{'system','tools','history','working_set','other'}:raise InvalidRequest('Unsupported final request component')
        parts={};ranges=[]
        for name,span in components.items():
            if final_request is None or not isinstance(span,(tuple,list)) or len(span)!=2:raise InvalidRequest('Components require exact ranges in an observed final request')
            start,end=span;count(start);count(end)
            if start is None or end is None or end<start or end>len(final_request):raise InvalidRequest('Component range is outside the final request')
            if start!=end and any(a!=b and start<b and end>a for a,b in ranges):raise InvalidRequest('Final request component ranges overlap')
            ranges.append((start,end));parts[name]=end-start
        if reserves is not None:
            if not isinstance(reserves,dict) or reserves.keys()-{'unit','output','tool','history','safety','context_window'} or not isinstance(reserves.get('unit'),str):raise InvalidRequest('Reserves require a unit and explicit counts')
            identifier(reserves['unit'])
            for k,v in reserves.items():
                if k!='unit':count(v)
        if usage is not None and usage_format!='openai_responses':raise InvalidRequest('Declare supported usage_format for reported usage')
        counts=openai_usage(usage)
        counted=None
        if token_counter is not None:
            if not callable(token_counter) or final_request is None or not counter_id:raise InvalidRequest('A trusted counter requires final bytes and its explicit identity')
            identifier(counter_id);counted=count(token_counter(final_request))
            if counted is None:raise InvalidRequest('Token counter did not return an integer')
        elif counter_id is not None:raise InvalidRequest('Counter identity without an actual counter is unsupported')
        inputs=counts['input_tokens'] if counts['input_tokens'] is not None else counted
        observation={'id':observation_id,'schema':'contextrail.request-observation.v1','source':'trusted_host_submission',
            'final_request_bytes':len(final_request) if final_request is not None else None,
            'component_bytes':parts,'framing_or_unclassified_bytes':len(final_request)-sum(parts.values()) if final_request is not None else None,
            'packet_utf8_bytes':len(packet.body.encode()),'packet_unit':packet.unit,'packet_units':packet.units,
            'packet_inclusion':'host_declared_link_not_outbound_control','reserves':reserves,
            'input_tokens':inputs,'counted_input_tokens':counted,'counter_id':counter_id,
            'reasoning_output_tokens':counts['reasoning_output_tokens'],
            'usage_source':'openai_reported_usage' if usage is not None else 'trusted_counter' if counted is not None else 'not_observed',
            'payload_retention':'counts_only; equal_length_payload_content_is_not_verified'}
        unit='tokens' if usage is not None or counted is not None else 'unknown'
        expected={'unit':unit,'input_units':inputs,'output_units':counts['output_tokens'],'cached_input_units':counts['cached_input_tokens'],
                  'cache_write_units':counts['cache_write_tokens'],'latency_ms':latency_ms,'request_id':request_id,'run_id':run_id,'attempt':attempt,
                  'snapshot':packet.snapshot,'packet_sha256':packet.sha256}
        def existing():
            row=self.store.db.execute("SELECT * FROM request_usage WHERE scope=? AND session=? AND json_extract(observation_json,'$.id')=?",(self.scope.key,self.session,observation_id)).fetchone()
            if row is None:return None
            value=_request_row(row)
            if value['observation']!=observation or any(value[k]!=v for k,v in expected.items()):raise Conflict('Observation identity already has different measurements or attribution')
            return value
        previous=existing()
        if previous is not None:return previous
        try:
            record=UsageLedger(self.store).record_request(self.scope,packet,unit=unit,input_units=inputs,output_units=counts['output_tokens'],
                cached_input_units=counts['cached_input_tokens'],cache_write_units=counts['cache_write_tokens'],latency_ms=latency_ms,
                request_id=request_id,run_id=run_id,attempt=attempt,observation=observation)
        except Conflict:
            previous=existing()
            if previous is not None:return previous
            raise
        return _request_row(self.store.db.execute('SELECT * FROM request_usage WHERE scope=? AND id=?',(self.scope.key,record.id)).fetchone())

    def call_tool(self,tools,name,arguments,*,observation_id=None,request_observation_id=None,invoke=None):
        if tools.scope!=self.scope or tools.session_id!=self.session:raise InvalidRequest('Tool meter scope/session differs')
        observation_id=observation_id or 'tool-'+uuid4().hex;identifier(observation_id)
        if request_observation_id is not None:identifier(request_observation_id)
        existing=self.store.db.execute("SELECT metadata FROM events WHERE scope=? AND kind='telemetry.tool' AND json_extract(metadata,'$.measurement_id')=?",(self.scope.key,self.session+':'+observation_id)).fetchone()
        started=time.perf_counter();status='succeeded';result=None;code=None
        try:
            result=invoke() if invoke is not None else tools.call(name,arguments)
            return result
        except Exception as exc:
            status='failed';code=exc.code if isinstance(exc,RailError) else 'tool_failed';raise
        finally:
            returned=len(canonical(result).encode()) if result is not None else None
            readback=None
            if status=='succeeded' and name=='context.get' and isinstance(result,dict):
                try:readback=len(base64.b64decode(result['content'])) if result.get('encoding')=='base64' else len(result['content'].encode('utf-8'))
                except (KeyError,TypeError,ValueError):pass
            value={'measurement_id':self.session+':'+observation_id,'id':observation_id,'session':self.session,'tool':name,
                   'request_observation_id':request_observation_id,'status':status,'error_code':code,
                   'returned_bytes':returned,'readback_bytes':readback,'latency_ms':max(0,int((time.perf_counter()-started)*1000)),
                   'byte_boundary':'application_result_json_not_transport_wire_or_model_input',
                   'ref':result.get('ref') if isinstance(result,dict) and name=='context.get' else None}
            if existing is None:
                try:save_tool(self.store,self.scope,value)
                except (sqlite3.Error,RailError,OSError):
                    tools._accounting_errors=(getattr(tools,'_accounting_errors',[])+[{'tool':name,'reason':'measurement_not_persisted'}])[-10:]


def save_tool(store,scope,value):
    # Original Store's write boundary serializes competing process receipts.
    with store.transaction():
        row=store.db.execute("SELECT 1 FROM events WHERE scope=? AND kind='telemetry.tool' AND json_extract(metadata,'$.measurement_id')=?",(scope.key,value['measurement_id'])).fetchone()
        if row is None:store._event(scope,'telemetry.tool',**value)


def record_capture(store,scope,session,result,projection):
    value={'measurement_id':session+':capture-'+uuid4().hex,'session':session,'tool':'capture-result','id':'capture-'+uuid4().hex,
           'request_observation_id':None,'status':result.status(),'error_code':None,'returned_bytes':len(canonical(projection).encode()),
           'readback_bytes':None,'latency_ms':None,'raw_bytes':len(result.stdout)+len(result.stderr),'source_call_id':result.metadata['call_id'],
           'byte_boundary':'capture_projection_json_not_model_input'}
    try:save_tool(store,scope,value)
    except (sqlite3.Error,RailError):pass  # Business capture remains authoritative.


def efficiency(**value):
    allowed={'action','trigger','unit','before_input','after_input','future_turns','generation_cost','validation_cost','expected_recovery_cost','cache_loss_cost','cache_write_cost'}
    if value.keys()-allowed or value.get('action') not in {'compaction','cache'} or not isinstance(value.get('unit'),str):raise InvalidRequest('Invalid efficiency proposal')
    identifier(value['unit']);trigger=value.get('trigger')
    answer={'schema':'contextrail.efficiency.v1','action':value['action'],'unit':value['unit'],'model_calls':0,'basis':'explicit_estimates_not_verified_savings','inputs':value,
            'decision':'insufficient_data','net_savings':None,'break_even_turns':None}
    if trigger not in {'phase_boundary','source_changed','budget_pressure','recovery_spike','explicit_request'}:
        return {**answer,'decision':'keep_current','reason':'no_material_event_trigger'}
    names=allowed-{'action','trigger','unit'}
    for name in names:count(value.get(name))
    missing=sorted(name for name in names if value.get(name) is None)
    if missing:return {**answer,'missing':missing,'reason':'comparable_costs_or_future_use_unknown'}
    per_turn=value['before_input']-value['after_input'];turns=value['future_turns']
    costs=value['generation_cost']+value['validation_cost']+value['expected_recovery_cost']+value['cache_loss_cost']+value['cache_write_cost']
    from .compaction import CompactionEstimate
    estimate=CompactionEstimate(max(0,per_turn)*turns,value['generation_cost'],value['validation_cost']+value['cache_loss_cost']+value['cache_write_cost'],value['expected_recovery_cost'])
    net=per_turn*turns-costs
    return {**answer,'decision':'consider' if per_turn>0 and estimate.worthwhile else 'keep_current','net_savings':net,
            'break_even_turns':costs//per_turn+1 if per_turn>0 else None,'reason':'estimated_positive_net_gain' if net>0 else 'generation_recovery_and_cache_cost_not_amortized'}


def metrics(tools,*,offset=0,limit=20,budget=16384):
    integer(offset);integer(limit,1);integer(budget,1)
    if offset>1000000 or limit>100 or budget<2048 or budget>tools.max_read_bytes:raise InvalidRequest('Invalid metrics page/budget')
    db=tools.store.db;scope=tools.scope
    with tools.store.read_transaction():
        total=db.execute('SELECT count(*) FROM request_usage WHERE scope=?',(scope.key,)).fetchone()[0]
        requests=[_request_row(r) for r in db.execute('SELECT * FROM request_usage WHERE scope=? ORDER BY rowid DESC LIMIT ? OFFSET ?',(scope.key,limit,offset))]
        tool_total=db.execute("SELECT count(*) FROM events WHERE scope=? AND kind='telemetry.tool'",(scope.key,)).fetchone()[0]
        calls=[{**json.loads(r['metadata']),'sequence':r['sequence'],'observed_at':r['at']} for r in db.execute("SELECT sequence,at,metadata FROM events WHERE scope=? AND kind='telemetry.tool' ORDER BY sequence DESC LIMIT ? OFFSET ?",(scope.key,limit,offset))]
        sums=db.execute("SELECT sum(json_extract(metadata,'$.returned_bytes')),sum(json_extract(metadata,'$.readback_bytes')),sum(json_extract(metadata,'$.latency_ms')),sum(json_extract(metadata,'$.returned_bytes') IS NULL),sum(json_extract(metadata,'$.latency_ms') IS NULL) FROM events WHERE scope=? AND kind='telemetry.tool'",(scope.key,)).fetchone()
        indexed=db.execute("SELECT sum(a.size) FROM evidence_index_state s JOIN artifacts a ON a.scope=s.scope AND a.name=s.name AND a.revision=s.revision WHERE s.scope=? AND s.status='indexed' AND a.invalidated=0 AND (a.expires IS NULL OR a.expires>?)",(scope.key,tools.store.clock())).fetchone()[0]
        captures=db.execute("SELECT sum(bytes) FROM (SELECT json_extract(metadata,'$.source_call_id') id,max(json_extract(metadata,'$.raw_bytes')) bytes FROM events WHERE scope=? AND kind='telemetry.tool' AND json_extract(metadata,'$.tool')='capture-result' GROUP BY id)",(scope.key,)).fetchone()[0]
        unit_totals=[dict(r) for r in db.execute('SELECT unit,count(*) requests,sum(input_units) known_input_units,sum(output_units) known_output_units,sum(cached_input_units) known_cached_input_units,sum(input_units IS NULL) unknown_input_requests,sum(output_units IS NULL) unknown_output_requests FROM request_usage WHERE scope=? GROUP BY unit',(scope.key,))]
        legacy=[dict(r) for r in db.execute('SELECT unit,count(*) aggregate_records FROM usage_records WHERE scope=? GROUP BY unit',(scope.key,))]
    result={'schema':'contextrail.metrics.v1','scope':asdict(scope),'reader_session':tools.session_id,
        'requests':{'total':total,'scope':'recorded_usage_rows_not_all_native_requests','entries':requests,'offset':offset,'next_offset':offset+len(requests) if offset+len(requests)<total else None,
                    'unknown_final_payloads_in_page':sum(r['observation'] is None or r['observation'].get('final_request_bytes') is None for r in requests),'totals_by_unit':unit_totals,'legacy_aggregate_ledger':legacy,'legacy_overlap':'not_automatically_combined'},
        'tools':{'total':tool_total,'entries':calls,'offset':offset,'next_offset':offset+len(calls) if offset+len(calls)<tool_total else None,
                 'known_returned_bytes':sums[0],'known_readback_bytes':sums[1],'known_latency_ms':sums[2],'unknown_returned_bytes':sums[3] if tool_total else 0,'unknown_latency':sums[4] if tool_total else 0},
        'storage':{'indexed_source_bytes':indexed,'unique_captured_raw_bytes':captures,'boundary':'stored_or_processed_source_bytes_not_provider_input_savings'},
        'native_usage':native_usage(tools),'comparison':{'provider_input_savings':None,'cost_savings':None,'reason':'no_paired_final_request_baseline'},
        'instrumentation':{'coverage':'observed_calls_only; unrecorded_or_predeployment_calls_unknown','excluded_tools':['context.metrics','context.efficiency'],'host_final_request_control':'not_observed_by_this_adapter','errors_in_this_reader':getattr(tools,'_accounting_errors',[])},
        'continue':{'tool':'context.metrics','arguments':{'offset':offset+limit,'limit':limit}},'unit_boundary':'bytes_tokens_and_latency_are_not_added'}
    while len(canonical(result).encode())>budget:
        if calls:calls.pop();result['tools']['next_offset']=offset+len(calls);continue
        if requests:
            requests.pop();result['requests']['next_offset']=offset+len(requests)
            result['requests']['unknown_final_payloads_in_page']=sum(r['observation'] is None or r['observation'].get('final_request_bytes') is None for r in requests);continue
        raise BudgetExceeded('Metrics source summaries exceed budget; increase it')
    return result


def native_usage(tools):
    from contextlib import closing
    from .federation import bound_sources
    from .query_sources import event_selection
    binding=bound_sources(tools)
    if not binding or not binding.memory_home or not binding.native_sessions:return {'status':'not_configured','sessions':[]}
    try:
        with closing(sqlite3.connect((binding.memory_home/'state.sqlite').as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
            db.row_factory=sqlite3.Row;where,args=event_selection(binding)
            total=db.execute("SELECT count(*) FROM normalized_events n WHERE "+where+" AND event_type='usage.reported'",args).fetchone()[0]
            rows=db.execute("SELECT n.metadata,s.content FROM normalized_events n JOIN source_events s ON s.id=n.event_id WHERE "+where+" AND n.event_type='usage.reported' ORDER BY n.sequence DESC LIMIT 200",args).fetchall()
            sessions={}
            for row in rows:
                meta=json.loads(row['metadata']);data=json.loads(row['content'])['final'];key=(meta['client'],meta['native_session'])
                value=sessions.setdefault(key,{'client':key[0],'native_session':key[1],'latest_cumulative':None,'latest_report_ref':None,'reports_in_window':0,'unknown_reports_in_window':0})
                value['reports_in_window']+=1
                if data.get('counts') is None:value['unknown_reports_in_window']+=1
                cumulative=data.get('cumulative')
                if value['latest_cumulative'] is None and isinstance(cumulative,dict):
                    value.update(latest_cumulative=cumulative,reported_model=data.get('model'),latest_report_ref={'reader':'memory.stream_get','workspace':binding.workspace,'client':key[0],'native_session':key[1],'event_id':meta['id']},occurred_at=meta['occurred_at'])
            return {'status':'observed' if total else 'no_usage_observed','sessions':list(sessions.values()),'total_reports':total,'returned_reports':len(rows),
                    'granularity':'native_session_reports_not_rail_requests','aggregation':'latest_reported_cumulative_never_sum_cumulative','coverage':'latest_200_bound_reports; predeployment_reports_not_backfilled'}
    except (sqlite3.Error,OSError,ValueError,KeyError,TypeError):return {'status':'unavailable','sessions':[],'reason':'existing_native_usage_source_unavailable'}
