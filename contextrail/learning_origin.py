"""Separate recoverable evidence from the deterministic automatic-learning view."""

import json
import re

POLICY='contextrail.learning-origin.v1'
_WRAPPER=re.compile(r'^\s*<(system-reminder|contextrail-history|codex_internal_context)(?:\s[^>]*)?>')


def origin(source_event=None,derived_from=(),history_origin='current_source',eligible=True):
    refs=[]
    for value in derived_from:
        if isinstance(value,str) and value and value not in refs:refs.append(value[:1024])
        elif isinstance(value,dict):
            encoded=json.dumps(value,ensure_ascii=False,sort_keys=True)
            if len(encoded)<=2048 and encoded not in refs:refs.append(encoded)
    return {'schema':POLICY,'source_event':source_event,'derived_from':refs[:32],
            'history_origin':history_origin,'eligible_for_learning':bool(eligible),
            'range_basis':'learning_projection_not_raw_offsets'}


def _frontmatter(text):
    if not text.startswith('---\n') and not text.startswith('---\r\n'):return {}
    lines=text[:32768].splitlines();result={}
    for line in lines[1:]:
        if line.strip()=='---':return result
        if not line or line[0].isspace() or ':' not in line:continue
        key,value=line.split(':',1);value=value.strip()
        try:value=json.loads(value)
        except ValueError:value=value.strip('"\'')
        result[key]=value
    return {}


def _artifact(value):
    if not isinstance(value,dict):return None,[],''
    schema=value.get('schema');refs=[];kind=None;reply=''
    if not isinstance(schema,str):schema=None
    if schema=='contextrail.native-context/v1' and isinstance(value.get('task'),dict) and isinstance(value.get('memory'),dict) and isinstance(value.get('origin'),dict):
        kind='native_context';snapshot=value['task'].get('snapshot')
        if isinstance(snapshot,str):refs.append('rail-snapshot:'+snapshot)
    elif schema=='contextrail.brief.v1' and isinstance(value.get('snapshot'),str) and isinstance(value.get('required'),list) and isinstance(value.get('optional'),list) and isinstance(value.get('loss'),dict):
        kind='context_brief'
    elif schema=='contextrail.metrics.v1' and isinstance(value.get('scope'),dict) and isinstance(value.get('requests'),dict) and isinstance(value.get('tools'),dict):kind='runtime_metrics'
    elif schema=='contextrail.efficiency.v1' and value.get('action') in {'compaction','cache'} and value.get('basis')=='explicit_estimates_not_verified_savings':kind='efficiency_estimate'
    elif schema=='contextrail.resume-intent.v1' and isinstance(value.get('anchors'),dict) and value.get('coverage')=='declared_anchors_only_not_all_user_history':kind='bridge_projection'
    elif schema=='contextrail.user-intent-source.v1' and isinstance(value.get('source'),dict) and isinstance(value.get('loss'),dict):kind='native_event_projection'
    elif schema in {'contextrail.resume-candidates.v1','contextrail.resume-offer.v1','contextrail.resume-accepted.v1'} and isinstance(value.get('scope',value.get('lease',value.get('resume_intent'))),dict):kind='recovery_management'
    elif schema=='contextrail.bridge/v1' and isinstance(value.get('task_id'),str):kind='bridge_projection'
    elif value.get('trust')=='derived_from_bridge_source' and isinstance(value.get('manifest_ref'),dict) and isinstance(value.get('snapshot'),str):
        kind='bridge_projection';refs.append(value['manifest_ref'])
    elif schema in {'contextrail.native-event.v1','contextrail.imported-event.v1'} and isinstance(value.get('id',value.get('event_id')),str):
        kind='native_event_replay';refs.append(value.get('id',value.get('event_id')))
    elif schema in {'contextrail.native-events.v1','contextrail.event-import.v1'} and isinstance(value.get('entries'),list):kind='event_projection'
    elif isinstance(schema,str) and schema.startswith('context-bridge.') and isinstance(value.get('task_id'),str):kind='bridge_projection'
    if kind:
        declared=value.get('origin',{})
        if isinstance(declared,dict):
            if isinstance(declared.get('source_event'),str):refs.append(declared['source_event'])
            if isinstance(declared.get('derived_from'),list):refs.extend(declared['derived_from'])
        if isinstance(value.get('source_event'),str):refs.append(value['source_event'])
        if isinstance(value.get('snapshot'),str):refs.append('rail-snapshot:'+value['snapshot'])
        # An explicit structured reply remains current input; a self-reported
        # eligibility flag alone never changes classification.
        reply=value.get('new_user_response',value.get('user_response',''))
        if not isinstance(reply,str):reply=''
    return kind,refs,reply


def project_text(text,*,native_meta=False,source_path=None,depth=0):
    if not isinstance(text,str):text=''
    if depth>6:return {'text':'','kinds':['projection_depth_limit'],'derived_from':[]}
    stripped=text.strip();kind=None;refs=[]
    if native_meta:return {'text':'','kinds':['native_metadata'],'derived_from':[]}
    try:value=json.loads(stripped)
    except (ValueError,RecursionError):value=None
    kind,refs,reply=_artifact(value)
    if kind:return {'text':reply,'kinds':[kind],'derived_from':refs}
    meta=_frontmatter(stripped)
    if meta.get('managed_by')=='context-rail' and meta.get('type')=='context_state_projection' and isinstance(meta.get('source_snapshot'),str):
        return {'text':'','kinds':['context_state_projection'],'derived_from':['rail-snapshot:'+meta['source_snapshot']]}
    if meta.get('type') in {'codex_session','coding_session'} and (meta.get('agent')=='codex' or str(meta.get('title','')).startswith('Codex checkpoint')):
        return {'text':'','kinds':['checkpoint'],'derived_from':[meta.get('permalink') or meta.get('title','')]}
    if (meta.get('managed_by')=='engineering-memory' and meta.get('type') in {'knowledge_page','observation'}) or meta.get('automatic_capture') is True or meta.get('type')=='memory_fact' and meta.get('memory_event'):
        refs=[meta[k] for k in ('source_event','memory_event','permalink') if isinstance(meta.get(k),str)]
        return {'text':'','kinds':['managed_memory_projection'],'derived_from':refs}
    if source_path:
        path=str(source_path).replace('\\','/').casefold()
        if '/.context-rail/bridge/' in path:
            return {'text':'','kinds':['bridge_bundle'],'derived_from':[str(source_path)]}
    if stripped.startswith(('# AGENTS.md','<environment_context>','<permissions')):
        return {'text':'','kinds':['bootstrap_context'],'derived_from':[]}
    lines=text.splitlines(keepends=True);out=[];kinds=[];refs=[];fence=None;index=0
    while index<len(lines):
        line=lines[index];lead=line.lstrip()
        if lead.startswith(('```','~~~')):
            marker=lead[:3]
            if fence is None:fence=marker
            elif fence==marker:fence=None
            out.append(line);index+=1;continue
        match=None if fence else _WRAPPER.match(line)
        if not match:out.append(line);index+=1;continue
        closing='</'+match[1]+'>';chunk=line[match.end():];last=index
        while closing not in chunk and last+1<len(lines):last+=1;chunk+=lines[last]
        if closing not in chunk:out.append(line);index+=1;continue
        inner,suffix=chunk.split(closing,1)
        projected=project_text(inner,depth=depth+1)
        kinds.extend(projected['kinds'] or ['native_injection']);refs.extend(projected['derived_from'])
        if suffix.strip():out.append(suffix)
        index=last+1
    return {'text':''.join(out).strip() if kinds else text,'kinds':list(dict.fromkeys(kinds)),'derived_from':refs}


def project_candidate(payload,source_event=None):
    flags=payload.get('_native_user_flags',{})
    user=project_text(payload.get('user',''),native_meta=bool(flags.get('is_meta') or flags.get('is_compact_summary')))
    final=project_text(payload.get('final',''),source_path=payload.get('source') if payload.get('kind')=='document' else None)
    kinds=user['kinds']+final['kinds'];refs=user['derived_from']+final['derived_from']
    if payload.get('_raw_user_event'):refs.append(payload['_raw_user_event'])
    pure_history_user=bool(user['kinds']) and not user['text'].strip()
    eligible=not pure_history_user and bool(user['text'].strip() or final['text'].strip())
    if payload.get('kind')=='document' and final['kinds'] and not final['text'].strip():eligible=False
    label='mixed_current_and_history' if kinds and eligible else kinds[0] if kinds else 'current_source'
    provenance=origin(source_event or payload.get('_event',{}).get('id'),refs,label,eligible)
    provenance['learning_policy']=POLICY
    return {**payload,'user':user['text'],'final':final['text'],'_origin':provenance}
