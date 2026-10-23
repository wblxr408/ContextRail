"""MODIFIED adaptation of context-mode session/extract.ts, ELv2.

Pinned 90af14e34bf35120c3310f7a75f54fa99d2cabba; see THIRD_PARTY_NOTICES.md.
Direct file/task categories and git invocation shape only. Success comes from
the local correlated result, never from a command regex or optimistic prose.
"""
import json
import re
import shlex


def call_facts(data):
    name=data.get('original_tool') or data.get('tool','');short=name.rsplit('__',1)[-1].rsplit('.',1)[-1]
    args=data.get('arguments',{})
    if isinstance(args,str):
        try:args=json.loads(args)
        except ValueError:args={}
    if not isinstance(args,dict):args={}
    facts=[]
    if short.lower() in {'read','write','edit','multiedit','read_file','write_file','edit_file'}:
        path=args.get('file_path',args.get('path'))
        if isinstance(path,str):facts.append({'category':'file','operation':short,'path':path,
            'range':{k:args[k] for k in ('offset','limit','start_line','end_line') if type(args.get(k)) is int}})
    command=args.get('cmd',args.get('command'));argv=args.get('argv')
    if not isinstance(argv,list) or any(not isinstance(x,str) for x in argv):
        argv=[]
        if isinstance(command,str) and len(command)<=16384 and not re.search(r'[;|&\r\n]|\$\(',command):
            try:argv=shlex.split(command,posix=False)
            except ValueError:pass
    if argv or command:
        facts.append({'category':'command','argv':argv,'command':command,'cwd':args.get('workdir',args.get('cwd'))})
    if argv:
        executable=argv[0].strip('"').replace('\\','/').rsplit('/',1)[-1].lower()
        if executable in {'git','git.exe'}:
            at=1
            while at<len(argv) and argv[at].startswith('-'):
                if argv[at] in {'-C','-c','--git-dir','--work-tree'}:at+=2
                elif argv[at] in {'--no-pager','--no-optional-locks'}:at+=1
                else:at=len(argv)
            if at<len(argv):facts.append({'category':'git','operation':argv[at]})
        if executable.startswith(('python','pip','uv','conda')):
            facts.append({'category':'environment','interpreter':argv[0],'argv':argv,'basis':'requested_argv_not_resolved_environment'})
    for key in ('cwd','workdir'):
        if isinstance(args.get(key),str):facts.append({'category':'environment','cwd':args[key],'basis':'host_declared_cwd'})
    if short in {'TaskCreate','TaskUpdate','TodoWrite','update_plan'}:
        rows=args.get('todos',args.get('plan',[args]))
        if isinstance(rows,list):
            for row in rows[:100]:
                if not isinstance(row,dict):continue
                text=row.get('subject',row.get('content',row.get('step','')))
                topic=row.get('taskId',row.get('id')) or ('plan:'+text[:128] if isinstance(text,str) and text else None)
                if isinstance(topic,str):facts.append({'category':'task','topic':topic,'text':text or topic,
                    'status':'completion_claim' if row.get('status')=='completed' else 'planned','inferred':True})
    return facts


def message_facts(data,role):
    text=data.get('text','')
    if not isinstance(text,str):return []
    mapping={'任务':'planned','计划':'planned','阻塞':'blocked','放弃':'excluded','完成':'completion_claim'}
    return [{'category':'task','topic':m[2],'text':m[3],'status':mapping[m[1]],
             'inferred':role!='user' or m[1]=='完成'} for m in re.finditer(r'(任务|计划|阻塞|放弃|完成)\[([^\]\n]{1,128})\][：:]\s*([^。\n]{1,512})',text)]


def result_info(data):
    output=data.get('output');value=output
    if isinstance(value,str):
        try:value=json.loads(value)
        except ValueError:value={}
    if not isinstance(value,dict):value={}
    code=value.get('exit_code');code=code if type(code) is int else None
    text=value.get('output',value.get('stdout',output if isinstance(output,str) else ''))
    if not isinstance(text,str):text=''
    status='failed' if code is not None and code!=0 or data.get('error_state')=='source_error' else 'observed' if code==0 or data.get('error_state')=='source_ok' else 'unknown'
    result={'exit_code':code,'status':status}
    if code==0:
        match=re.search(r'^\[[^\]\r\n]+ ([0-9a-f]{7,40})\]',text,re.M)
        if match:result.update(commit=match[1],commit_basis='source_reported_not_repository_head_verification')
    return result
