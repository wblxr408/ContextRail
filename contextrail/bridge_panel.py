"""Read-only browsing of original workspace Bridge bundles; no task takeover."""
import json
import os
from pathlib import Path

from .bridge import _inside, _read, _json, MAX_LEDGER
from .errors import InvalidRequest, AccessDenied, Unavailable
from .sanitization import sanitize_capture, CaptureError


class BridgePanel:
    def __init__(self,workspace):
        self.workspace=Path(workspace).resolve()
        self.root=self.workspace/'.context-rail/bridge'
        if not self.root.resolve().is_relative_to(self.workspace):raise AccessDenied('Bridge library must stay inside its workspace.')

    def bundle(self,selected):
        root=_inside(self.root,selected)
        if not root.is_dir():raise Unavailable('Selected Bridge version is no longer available.')
        value=_json(_read(_inside(root,'requirements.json'),MAX_LEDGER))
        if not isinstance(value,dict) or value.get('schema')!=1 or not isinstance(value.get('requirements'),list) or not all(isinstance(value.get(k),str) for k in ('task_id','snapshot_id')):
            raise InvalidRequest('This file is not a supported Bridge requirement ledger.')
        rows=value['requirements']
        if len(rows)>1000 or any(not isinstance(r,dict) or not isinstance(r.get('source'),dict) or not isinstance(r.get('statement'),str) or not isinstance(r.get('evidence'),list) for r in rows):raise InvalidRequest('Bridge requirement rows exceed the viewing bound or are invalid.')
        return root,value

    def detail(self,selected):
        root,value=self.bundle(selected);rows=value['requirements'];active=[r for r in rows if r.get('status')=='active']
        files={'requirements.json'}
        for row in rows:
            files.update(n for n in (row['source'].get('path'),*row['evidence']) if isinstance(n,str))
        for name in ('START.md','STATE.md','state.md','todo.md'):
            if (root/name).is_file():files.add(name)
        result={'id':selected,'task_id':value['task_id'],'snapshot_id':value['snapshot_id'],
            'goals':[r['statement'] for r in active if r.get('kind')=='goal'],
            'active_count':len(active),'superseded_count':len(rows)-len(active),'requirements':rows,
            'files':sorted(files),'bundle_path':str(root),'workspace':str(self.workspace),
            'authority':'saved_history_not_current_instructions','validation':'not_run_read_only_view',
            'resume_intent':value.get('resume_intent')}
        try:return sanitize_capture(result,'evidence').value
        except CaptureError:raise InvalidRequest('Selected Bridge view exceeds the processed-text limit.') from None

    def listing(self):
        items=[];errors=[];visited=0;truncated=False
        for directory,dirs,files in os.walk(self.root,followlinks=False):
            visited+=1
            dirs[:]=sorted(d for d in dirs if not (Path(directory)/d).is_symlink())
            if visited>3000 or len(items)+len(errors)>=500:truncated=True;break
            if 'requirements.json' not in files:continue
            selected=Path(directory).relative_to(self.root).as_posix()
            try:
                _,value=self.bundle(selected);active=[r for r in value['requirements'] if r.get('status')=='active']
                item={'id':selected,'task_id':value['task_id'],'snapshot_id':value['snapshot_id'],
                    'active_count':len(active),'requirement_count':len(value['requirements']),
                    'goal':'\n'.join(r['statement'] for r in active if r.get('kind')=='goal')[:600]}
                items.append(sanitize_capture(item,'evidence').value)
            except (OSError,ValueError,InvalidRequest,AccessDenied,Unavailable):errors.append({'id':selected,'reason':'交接账本当前不可读或格式不支持'})
        return {'items':items,'errors':errors,'truncated':truncated,'workspace':str(self.workspace),
            'library':str(self.root),'scope':'workspace_bridge_bundles_only','authority':'saved_history_not_current_instructions'}

    def source(self,selected,name):
        root,_=self.bundle(selected);detail=self.detail(selected)
        if not isinstance(name,str) or name not in detail['files']:raise AccessDenied('Read only files declared by this Bridge version.')
        path=_inside(root,name)
        with path.open('rb') as stream:raw=stream.read(65537)
        text=raw[:65536].decode('utf-8','replace')
        try:text=sanitize_capture(text,'document').value
        except CaptureError:raise InvalidRequest('Source cannot be displayed within the processed-text limit.') from None
        return {'bundle':selected,'file':name,'text':text,'truncated':len(raw)>65536,
            'limit_bytes':65536,'projection':'redacted_visible_text','authority':'saved_history_not_current_instructions'}
