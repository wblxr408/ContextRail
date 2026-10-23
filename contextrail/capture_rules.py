"""Bounded capture decisions before any derived persistence.

Adapted from ai-memory capture_policy.rs at 1bd866375b01031d63db54862a133376598d3701
(MIT). Local Python implementation uses closed direct fields and fnmatch;
it does not interpret scripts, open tool paths, or implement upstream glob algebra.
"""
import fnmatch,os,re,tomllib
from pathlib import Path

MODES={'full','metadata_only','skip'}
FIELDS={'ignore','allow','metadata_only','user_mode','assistant_mode','default_mode','unknown_tools','tool_modes'}


def validate_capture(value):
    if not isinstance(value,dict) or set(value)-FIELDS:raise ValueError('Invalid capture policy fields')
    result={}
    for name in ('ignore','allow','metadata_only'):
        patterns=value.get(name,[])
        if not isinstance(patterns,list) or len(patterns)>128:raise ValueError('Capture patterns require at most 128 entries')
        if any(not isinstance(p,str) or not p or len(p)>1024 or Path(p).is_absolute() or '..' in Path(p).parts or '\x00' in p for p in patterns):
            raise ValueError('Capture patterns must be bounded workspace-relative paths')
        result[name]=list(patterns)
    for name,default in (('user_mode','full'),('assistant_mode','full'),('default_mode','full'),('unknown_tools','metadata_only')):
        mode=value.get(name,default)
        if not isinstance(mode,str) or mode not in MODES:raise ValueError('Capture disposition must be full, metadata_only or skip')
        result[name]=mode
    tools=value.get('tool_modes',{})
    if not isinstance(tools,dict) or len(tools)>128 or any(not isinstance(k,str) or not re.fullmatch(r'[\w.:-]{1,128}',k) or not isinstance(v,str) or v not in MODES for k,v in tools.items()):
        raise ValueError('Invalid explicit tool dispositions')
    result['tool_modes']=dict(tools)
    return result


def load_policy(workspace):
    try:
        root=Path(workspace).absolute()
        marker=next((p/'.contextrail.toml' for p in (root,*root.parents) if (p/'.contextrail.toml').is_file()),None)
        if marker is None:return {'root':str(root),'active':False,'invalid':False,**validate_capture({})}
        with marker.open('rb') as stream:raw=stream.read(65537)
        if len(raw)>65536:raise ValueError('Capture marker exceeds limit')
        data=tomllib.loads(raw.decode('utf-8'));value=data.get('capture',{});rules=validate_capture(value)
        anchor=os.path.abspath(marker.parent/data.get('project',{}).get('workspace','.'))
        return {**rules,'root':anchor,'active':bool(value),'invalid':False}
    except (OSError,ValueError,TypeError,AttributeError):
        return {**validate_capture({}),'root':str(workspace),'active':True,'invalid':True}


def _relative(path,root,cwd):
    if not isinstance(path,str) or not path.strip() or len(path)>4096 or '\x00' in path:return None
    full=os.path.abspath(os.path.join(cwd,path))
    try:relative=os.path.relpath(full,root)
    except ValueError:return None
    if relative=='..' or relative.startswith('..'+os.sep):return None
    return relative.replace('\\','/')


def decide(policy,role,typ,data,cwd):
    if policy['invalid']:return {'mode':'metadata_only','reason':'invalid_policy','path_coverage':'unknown'}
    if role in {'user','assistant'}:
        return {'mode':policy[role+'_mode'],'reason':'explicit_'+role+'_mode','path_coverage':'independent_user_text' if role=='user' else 'not_applicable'}
    if role!='tool':return {'mode':'full','reason':'lifecycle_metadata','path_coverage':'not_applicable'}
    tool=data.get('tool','');args=data.get('arguments');paths=[]
    if tool in policy['tool_modes']:return {'mode':policy['tool_modes'][tool],'reason':'explicit_tool_mode','path_coverage':'host_override'}
    if not policy['active']:return {'mode':'full','reason':'no_project_capture_policy','path_coverage':'not_inspected'}
    if tool not in {'Read','Write','Edit','MultiEdit'} or not isinstance(args,dict):
        return {'mode':policy['unknown_tools'],'reason':'unsupported_path_schema','path_coverage':'unknown'}
    for key in ('file_path','path','file_paths','paths'):
        value=args.get(key)
        if isinstance(value,str):paths.append(value)
        elif isinstance(value,list):paths.extend(value)
    if not paths or len(paths)>32:return {'mode':'metadata_only','reason':'missing_or_excess_paths','path_coverage':'unknown'}
    relative=[_relative(p,policy['root'],cwd) for p in paths]
    if None in relative:return {'mode':'metadata_only','reason':'outside_or_invalid_path','path_coverage':'unknown'}
    fold=lambda x:x.casefold() if os.name=='nt' else x
    def match(name):return any(fnmatch.fnmatchcase(fold(p),fold(pattern.replace('\\','/'))) for p in relative for pattern in policy[name])
    if match('ignore'):mode,reason='skip','path_ignore'
    elif policy['allow'] and any(not any(fnmatch.fnmatchcase(fold(p),fold(pattern)) for pattern in policy['allow']) for p in relative):mode,reason='skip','outside_path_allow'
    elif match('metadata_only'):mode,reason='metadata_only','path_metadata_only'
    else:mode,reason=policy['default_mode'],'matched_project_policy'
    return {'mode':mode,'reason':reason,'path_coverage':'direct_fields','path_count':len(paths)}


def project_item(item,context):
    policy=load_policy(item['workspace'] or '.');role=item['role'];typ=item['type'];data=item['data']
    if typ in {'user.message','turn.start'}:
        context['_capture_calls']={};context['_capture_suppress_assistant']=False
    if typ=='tool.result':
        decision=context.setdefault('_capture_calls',{}).get(item['call_id'])
        if decision is None:decision={'mode':policy['unknown_tools'] if policy['active'] else 'full','reason':'unknown_tool_call','path_coverage':'unknown'}
    else:decision=decide(policy,role,typ,data,item['workspace'])
    if typ=='tool.call':
        calls=context.setdefault('_capture_calls',{})
        if len(calls)>=1024:calls.clear()
        calls[item['call_id']]=decision
    if role=='tool' and decision['mode']!='full':context['_capture_suppress_assistant']=True
    if role=='assistant' and context.get('_capture_suppress_assistant'):
        decision={'mode':'metadata_only','reason':'restricted_tool_derived_text','path_coverage':'inherited_turn'}
    context.setdefault('_capture_row_modes',{})[role]=decision['mode']
    if decision['mode']=='full':return data,decision
    safe={'capture_disposition':decision['mode'],'capture_reason':decision['reason']}
    if decision['mode']=='metadata_only':
        if isinstance(data.get('tool'),str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,64}',data['tool']):safe['tool']=data['tool']
        if data.get('error_state') in {'source_ok','source_error','unknown'}:safe['error_state']=data['error_state']
    return safe,decision


def learning_row(client,row,context):
    modes=context.get('_capture_row_modes',{});out=dict(row)
    if client=='codex':
        p=dict(row.get('payload') or {});role=p.get('role')
        if role in {'user','assistant'} and modes.get(role) not in {None,'full'}:p['content']=[]
        if context.get('_capture_suppress_assistant') or modes.get('assistant') not in {None,'full'}:p.pop('last_agent_message',None)
        out['payload']=p
    else:
        msg=dict(row.get('message') or {});role=row.get('type')
        if role in {'user','assistant'} and modes.get(role) not in {None,'full'}:msg['content']=[]
        out['message']=msg
    if modes.get('user') not in {None,'full'}:context.pop('user',None)
    if context.get('_capture_suppress_assistant') or modes.get('assistant') not in {None,'full'}:
        context.pop('final',None);context.pop('parts',None)
    return out
