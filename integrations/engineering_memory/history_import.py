"""Explicit foreground document/Git ingestion through the existing jobs queue."""
import json
from pathlib import Path
import subprocess
import time
from canonical import canonical_path
from capture_sanitizer import sanitize_capture, require_safe_identity
from event_identity import source_identity, admit_event, record_conflict, semantic_content
from capture_policy import claim_owner, receipt
from learning_origin import project_candidate
from capture_rules import load_policy,decide

def enqueue(store,source,scope,event_key,content,occurred_at,kind):
    from listener import digest
    require_safe_identity(str(source));require_safe_identity(str(scope));require_safe_identity(event_key)
    policy=load_policy(str(scope))
    disposition=decide(policy,'tool','tool.call',{'tool':'Read','arguments':{'file_path':str(source)}},str(scope)) if kind=='document' else {'mode':'metadata_only' if policy['invalid'] else policy['default_mode'],'reason':'invalid_policy' if policy['invalid'] else 'explicit_git_import'}
    full=sanitize_capture(content,'document').value if disposition['mode']=='full' else ''
    # Individual paragraph batches avoid classifying only the final page of an imported document.
    pieces=[];current=''
    for paragraph in full.split('\n\n'):
        if current and len(current)+len(paragraph)>7000:pieces.append(current);current=''
        while len(paragraph)>7000:
            if current:pieces.append(current);current=''
            pieces.append(paragraph[:7000]);paragraph=paragraph[7000:]
        current+=('\n\n' if current else '')+paragraph
    if current:pieces.append(current)
    count=0;conflict=None
    root=source_identity('import/'+kind,store.event_workspace(str(scope)),'explicit-import',event_key,str(source),0,imported=True)
    source_payload={'user':'以下是明确导入的历史资料，仅作来源数据。','final':full,'cwd':str(scope),'kind':kind,'source':str(source),'offset':0,'marker':event_key,'occurred_at':occurred_at}
    learning=project_candidate(source_payload,root['id'])
    if disposition['mode']!='full':learning['_origin']['eligible_for_learning']=False
    source_payload['_capture_disposition']=disposition
    source_payload.update(_origin=learning['_origin'],producer='explicit_import',capture_owner='existing_import_queue')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        store.db.execute('SAVEPOINT source_import')
        owner=claim_owner(store.db,str(scope),kind,'explicit-import',domain='import')
        # Older versions had only part IDs. A shorter replay must not silently
        # orphan the already-bound tail when creating the new container mapping.
        old_positions=set()
        for row in store.db.execute('SELECT payload FROM jobs WHERE source=?',(str(source),)):
            try:old=json.loads(row[0])
            except ValueError:continue
            if isinstance(old,dict) and old.get('kind')==kind and old.get('marker')==event_key and store.event_workspace(old.get('cwd'))==store.event_workspace(str(scope)):
                position=old.get('offset')
                if type(position) is int:old_positions.add(position)
        if old_positions and old_positions!=set(range(len(pieces))):conflict='legacy_part_set_changed'
        if not conflict:
            admitted=admit_event(store.db,root,source_payload,str(source),container=True)
            if admitted['outcome']=='identity_conflict':conflict=admitted['reason']
        if not conflict and learning['_origin']['eligible_for_learning']:
            for i,text in enumerate(pieces):
                payload={**source_payload,'final':text,'offset':i}
                event=source_identity('import/'+kind,store.event_workspace(str(scope)),'explicit-import',event_key,str(source),i,part=i,imported=True)
                admitted=admit_event(store.db,event,payload,str(source),legacy_ids=(digest('import:'+kind+':'+event_key+':'+str(i)),),normalize_workspace=store.event_workspace)
                if admitted['outcome']=='identity_conflict':conflict=admitted['reason'];break
                count+=int(admitted['outcome']=='inserted')
        if not conflict:
            receipt(store.db,str(scope),kind,'explicit-import','explicit_import',owner,'admitted' if learning['_origin']['eligible_for_learning'] else 'policy_excluded' if disposition['mode']!='full' else 'history_only',root['id'],metadata={'origin':learning['_origin'],'disposition':disposition})
        if conflict:
            store.db.execute('ROLLBACK TO source_import')
            previous=store.db.execute('SELECT content FROM source_events WHERE id=?',(root['id'],)).fetchone()
            record_conflict(store.db,root,semantic_content(source_payload,store.event_workspace(str(scope))),str(source),0,conflict,previous[0] if previous else None)
        store.db.execute('RELEASE source_import')
    if conflict:raise ValueError('Source revision conflicts with its existing capture; inspect source event diagnostics')
    return count

def import_document(store,path,scope):
    path=canonical_path(path);scope=canonical_path(scope)
    if path.suffix.casefold() not in {'.md','.txt','.json'}:raise ValueError('Only UTF-8 text/Markdown/JSON documents are supported')
    if not path.is_relative_to(scope):raise ValueError('Document must be inside its explicitly selected workspace')
    from datetime import datetime,timezone
    text=path.read_text(encoding='utf-8-sig')
    revision=str(path.stat().st_mtime_ns)
    return enqueue(store,path,scope,str(path)+':'+revision,text,datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat(),'document')

def import_git(store,repository,limit=50,after=None):
    repo=canonical_path(repository)
    if not 1<=limit<=1000:raise ValueError('Git import limit must be 1..1000')
    # Explicit argv, no shell, no patches/secrets/diffs by default. Git commit IDs identify source revisions.
    cmd=['git','-C',str(repo),'log','--max-count='+str(limit),'--format=%H%x1f%cI%x1f%B%x1e']
    if after:
        from memory_runtime import date_value
        cmd.append('--since='+date_value(after))
    completed=subprocess.run(cmd,capture_output=True,timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if completed.returncode:raise ValueError('Cannot read history of the explicitly selected Git repository')
    count=0
    for record in completed.stdout.decode('utf-8',errors='replace').split('\x1e'):
        parts=record.strip().split('\x1f',2)
        if len(parts)!=3:continue
        revision,occurred,body=parts
        count+=enqueue(store,str(repo)+'@'+revision,repo,str(repo)+':'+revision,body,occurred,'git')
    return count
