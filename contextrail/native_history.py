"""MODIFIED adaptation of ai-memory's read-only native transcript range method.

Source: akitaonrails/ai-memory 1bd866375b01031d63db54862a133376598d3701,
crates/ai-memory-workstream/src/transcript.rs (MIT; Fabio Akita and contributors).
See _vendor/ai_memory_LICENSE.txt. Python uses existing Rail visible normalization,
policy and sanitizer. No Rust runtime, prefix hashes, capture cursor or daemon.
"""
import base64
from dataclasses import asdict
import json
import os
from pathlib import Path
from uuid import uuid4

from .errors import InvalidRequest, Conflict, BudgetExceeded
from .models import canonical, integer
from .source_events import workspace_key
from .sanitization import sanitize_capture, require_safe_identity, CaptureError
from .event_stream import normalize_record
from .capture_rules import project_item
from .learning_origin import project_text

MAX_ROW=4*1024*1024
MAX_SCAN=256*1024*1024
MAX_FILES=20000


def identity(stat):
    return [stat.st_dev,stat.st_ino,getattr(stat,'st_birthtime_ns',None)]


class NativeHistory:
    def __init__(self,memory_home,workspace,client,native_session,*,source=None):
        if client not in {'codex','claude'}:raise InvalidRequest('Select Codex or Claude history')
        if not isinstance(native_session,str) or len(native_session)>512:raise InvalidRequest('Invalid native session identity')
        try:require_safe_identity(native_session)
        except CaptureError:raise InvalidRequest('Invalid native session identity') from None
        if any(c in native_session for c in '/\\:*?[]') or not native_session:raise InvalidRequest('Native session must be an exact filename-safe identity')
        self.home=Path(memory_home).resolve();self.workspace=Path(workspace).resolve()
        self.client,self.session=client,native_session;self.source=Path(source).resolve() if source else None

    def roots(self):
        try:
            raw=(self.home/'config.json').read_bytes()
            if len(raw)>1024*1024:raise ValueError()
            config=json.loads(raw)
            roots=[Path(s['path']).resolve() for s in config['sources'] if s.get('enabled') is True and s.get('kind')==self.client and isinstance(s.get('path'),str) and Path(s['path']).is_absolute()]
            if not roots:raise ValueError()
            return roots
        except (OSError,ValueError,TypeError,KeyError):raise InvalidRequest('Original memory source roots are unavailable; configure its existing sources') from None

    def locate(self,selected=None):
        if selected is not None and not isinstance(selected,(str,Path)):raise InvalidRequest('Invalid history source reference')
        roots=self.roots();selected=self.source or (Path(selected).resolve() if selected else None)
        if selected:
            if not any(selected.is_relative_to(r) for r in roots) or not selected.is_file():raise InvalidRequest('Selected native source is outside enabled original roots or unavailable')
            return selected
        matches=set();count=0
        for root in roots:
            for directory,dirs,files in os.walk(root,followlinks=False):
                dirs[:]=[d for d in dirs if not Path(directory,d).is_symlink()]
                for name in files:
                    count+=1
                    if count>MAX_FILES:raise BudgetExceeded('Native discovery scan limit; select an exact source inside configured roots')
                    if name==self.session+'.jsonl' or self.client=='codex' and name.endswith('-'+self.session+'.jsonl'):
                        path=Path(directory,name).resolve()
                        if any(path.is_relative_to(r) for r in roots):matches.add(path)
        if len(matches)!=1:raise InvalidRequest('Native source is missing or ambiguous; select its exact source inside enabled roots')
        return next(iter(matches))

    def selection(self):return {'client':self.client,'native_session':self.session,'workspace':str(self.workspace)}

    def _bound(self,path,stat,watermark,offset):
        return {'schema':'contextrail.native-history-cursor.v1',**self.selection(),'path':str(path),
                'identity':identity(stat),'watermark':watermark,'mtime_ns':stat.st_mtime_ns,'offset':offset}

    def _validate(self,cursor,path,stat):
        if not isinstance(cursor,dict) or any(cursor.get(k)!=v for k,v in self.selection().items()) or cursor.get('schema')!='contextrail.native-history-cursor.v1' or cursor.get('path')!=str(path):raise InvalidRequest('History cursor belongs to another exact selection')
        for field in ('offset','watermark','mtime_ns'):integer(cursor.get(field))
        if cursor['offset']>cursor['watermark'] or cursor.get('identity')!=identity(stat) or stat.st_size<cursor['watermark'] or stat.st_size==cursor['watermark'] and stat.st_mtime_ns!=cursor['mtime_ns']:raise Conflict('History source changed generation; explicitly select it again')

    def _scan(self,path,watermark,stat,max_scan,body_start=0,*,resume_offset=None):
        """Replay real prefix to rebuild scope and capture decisions, never cursor state."""
        state={};offset=0;matched=False;count=0
        with path.open('rb') as stream:
            if identity(os.fstat(stream.fileno()))!=identity(stat):raise Conflict('History source replaced before read')
            if resume_offset not in (None, 0, watermark):
                stream.seek(resume_offset - 1)
                if stream.read(1) != b'\n':
                    raise InvalidRequest('History cursor is not a native record boundary')
                stream.seek(0)
            while offset<watermark:
                start=offset;remaining=min(watermark-offset,max_scan-offset)
                if remaining<=0:
                    yield None,{'reason':'scan_limit','start':offset,'end':watermark};break
                raw=stream.readline(min(MAX_ROW+1,remaining));offset+=len(raw);count+=1
                if count>200000:
                    yield None,{'reason':'record_limit','start':start,'end':watermark};break
                if not raw:break
                oversized=len(raw)>MAX_ROW
                while not raw.endswith(b'\n') and offset<watermark and oversized:
                    block=stream.readline(min(65536,watermark-offset,max(1,max_scan-offset)));offset+=len(block)
                    if not block or offset>=max_scan:break
                    raw=block
                if not raw.endswith(b'\n'):
                    yield None,{'reason':'partial_final_record' if offset==watermark else 'scan_limit','start':start,'end':offset};break
                if oversized:
                    # A skipped header/call could change both selection and privacy policy.
                    yield None,{'reason':'oversized_record','start':start,'end':offset};break
                try:row=json.loads(raw)
                except (ValueError,UnicodeError,RecursionError):
                    yield None,{'reason':'invalid_record','start':start,'end':offset};break
                items=normalize_record(self.client,row,state)
                if state.get('session') and state['session']!=self.session:raise Conflict('Native file identifies another session')
                same=workspace_key(str(Path(state['cwd']).resolve()))==workspace_key(str(self.workspace)) if isinstance(state.get('cwd'),str) and state['cwd'] else False
                if state.get('session')==self.session and same:matched=True
                for index,item in enumerate(items):
                    if item['native_session']!=self.session or not same:
                        if item['type'] in {'user.message','assistant.final','tool.call','tool.result'}:
                            yield None,{'reason':'other_checkout_segment','start':start,'end':offset}
                        continue
                    data,decision=project_item(item,state)
                    if item['type'] in {'user.message','assistant.final'}:
                        projected=project_text(data.get('text',''),native_meta=bool(row.get('isMeta') or row.get('isCompactSummary')))
                        if not projected['text'].strip():continue
                        data={**data,'text':projected['text']}
                    # Prefix tool results rebuild pending/policy state but their bodies
                    # are not requested. Do not repeatedly sanitize an entire archive.
                    if item['type']=='tool.call' or offset<=body_start and item['type']=='tool.result':data={'omitted_prefix_body':True}
                    try:data=sanitize_capture(data,'tool' if item['role']=='tool' else item['role'] if item['role'] in {'user','assistant'} else 'evidence').value
                    except CaptureError:
                        yield None,{'reason':'sanitizer_limit','start':start,'end':offset};continue
                    ref={**self._bound(path,stat,watermark,start),'end':offset,'index':index}
                    value={'type':item['type'],'role':item['role'],'call_id':item['call_id'],'data':data,'capture':decision,'ref':ref}
                    yield value,None
            after=os.fstat(stream.fileno())
            if identity(after)!=identity(stat) or after.st_size<watermark or after.st_size==stat.st_size and after.st_mtime_ns!=stat.st_mtime_ns:raise Conflict('History source changed during read')
        if not matched:raise Conflict('Native source does not identify the exact session and checkout')

    def _preview(self,value,maximum=1000):
        if value is None:return None
        if len(canonical(value['data']).encode())<=maximum:return value
        return {**value,'data':{'preview':canonical(value['data'])[:maximum//4]},'omitted_body':True,'read_tool':'context.native_history_get'}

    def read(self,*,cursor=None,limit=20,budget=16384,tail_bytes=1024*1024,max_scan=MAX_SCAN,follow=False):
        integer(limit,1);integer(budget,1);integer(tail_bytes);integer(max_scan,1)
        if limit>100 or max_scan>1024*1024*1024 or type(follow) is not bool:raise InvalidRequest('Invalid history bounds')
        path=self.locate(cursor.get('path') if isinstance(cursor,dict) else None);stat=path.stat()
        if cursor is not None:self._validate(cursor,path,stat)
        watermark=stat.st_size if cursor is None or follow else cursor['watermark']
        start=cursor['offset'] if cursor is not None else max(0,watermark-tail_bytes) if tail_bytes else 0
        result={'schema':'contextrail.native-history.v1',**self.selection(),'entries':[],'last_request':None,'last_final':None,'pending_calls':[],
            'gaps':[],'next_cursor':None,'end_cursor':None,'authority':'visible_source_history_not_current_instructions',
            'coverage':{'watermark':watermark,'tail_start':start,'max_scan':max_scan,'prefix_integrity':'physical_identity_size_mtime_only_not_content_verified'}}
        item_index=cursor.get('item_index',0) if cursor else 0;integer(item_index)
        gaps={};pending={};more_ref=None;scanned_end=0
        for item,gap in self._scan(path,watermark,stat,max_scan,body_start=start,
                                   resume_offset=start if cursor is not None else None):
            if gap:
                reason=gap['reason'];entry=gaps.setdefault(reason,{**gap,'count':0});entry['count']+=1;entry['end']=gap['end'];continue
            end=item['ref']['end'];scanned_end=max(scanned_end,end)
            if item['type']=='user.message':result['last_request']=self._preview(item)
            if item['type']=='assistant.final':result['last_final']=self._preview(item)
            if item['type']=='tool.call' and item['call_id']:
                if len(pending)>=256:
                    pending.pop(next(iter(pending)));gaps['pending_limit']={'reason':'pending_limit','count':1,'start':0,'end':end}
                pending[item['call_id']]={'call_id':item['call_id'],'ref':item['ref'],'status':'outcome_unknown_no_automatic_retry'}
            elif item['type']=='tool.result':pending.pop(item['call_id'],None)
            if end<=start or item['ref']['offset']==start and item['ref']['index']<item_index or item['type'] not in {'user.message','assistant.final','tool.result'}:continue
            if len(result['entries'])>=limit:
                if more_ref is None:more_ref=item['ref']
                continue
            result['entries'].append(self._preview(item))
        result['gaps']=list(gaps.values());result['pending_calls']=list(pending.values())
        if start>0:result['coverage']['earlier_body_omitted']=True
        if not result['last_final']:result['gaps'].append({'reason':'no_visible_final','count':1})
        elif result['last_request'] and result['last_final']['ref']['offset']<result['last_request']['ref']['offset']:
            result['gaps'].append({'reason':'no_final_after_last_request','count':1})
        result['coverage']['scanned_visible_end']=scanned_end
        result['end_cursor']=self._bound(path,stat,watermark,min((g['start'] for g in result['gaps'] if g['reason'] in {'partial_final_record','invalid_record','oversized_record','scan_limit','record_limit'}),default=watermark))
        def next_at(ref):return {**self._bound(path,stat,watermark,ref['offset']),'item_index':ref['index']}
        if more_ref is not None:result['next_cursor']=next_at(more_ref)
        while len(canonical(result).encode())>budget and result['pending_calls']:
            result['pending_calls'].pop();result['coverage']['pending_hints_omitted']=True
        while len(canonical(result).encode())>budget and result['entries']:
            removed=result['entries'].pop();result['next_cursor']=next_at(removed['ref'])
        for key in ('last_request','last_final'):
            if len(canonical(result).encode())>budget and result[key]:result[key]={**result[key],'data':{},'omitted_body':True,'read_tool':'context.native_history_get'}
        if len(canonical(result).encode())>budget or not result['entries'] and result['next_cursor'] is not None:raise BudgetExceeded('History metadata cannot fit; increase response budget')
        return result

    def get(self,ref,*,start=0,end=None,budget=16384):
        if not isinstance(ref,dict):raise InvalidRequest('Expected an exact processed history ref')
        integer(start);integer(budget,1)
        path=self.locate(ref.get('path'));stat=path.stat();self._validate(ref,path,stat)
        integer(ref.get('end'),1);integer(ref.get('index'))
        if ref['end']>ref['watermark'] or ref['end']<=ref['offset']:raise InvalidRequest('Invalid history record range')
        selected=None
        for item,gap in self._scan(path,ref['end'],stat,MAX_SCAN,body_start=ref['offset']):
            if item and item['ref']['offset']==ref['offset'] and item['ref']['end']==ref['end'] and item['ref']['index']==ref['index']:
                if item['type'] not in {'user.message','assistant.final','tool.result'}:raise InvalidRequest('Only visible messages and completed results can be read')
                selected=item
        if selected is None:raise InvalidRequest('Processed history record is unavailable at the exact range')
        self._validate(ref,path,path.stat())
        body=canonical({k:v for k,v in selected.items() if k!='ref'}).encode();end=min(len(body),start+4096) if end is None else end;integer(end)
        if end<start or start>len(body) or end>len(body):raise InvalidRequest('Invalid processed record byte range')
        value={'schema':'contextrail.native-history-content.v1','ref':ref,'start':start,'end':end,'total':len(body),'base64':base64.b64encode(body[start:end]).decode(),'encoding':'base64_utf8_json','trust':'untrusted_visible_source'}
        if len(canonical(value).encode())>budget:raise BudgetExceeded('History content cannot fit; request a smaller byte range')
        return value

    def export_state(self,output,**options):
        output=Path(output).resolve()
        if not output.is_relative_to(self.workspace):raise InvalidRequest('History export must stay in selected workspace')
        value=self.read(**options);marker='<!-- contextrail-native-history:v1 -->';target=output/'STATE.md'
        if target.exists() and marker not in target.read_text(encoding='utf-8')[:1024]:raise Conflict('Refusing to overwrite a human STATE file')
        payload=output/'history.json'
        if payload.exists():
            try:owned=json.loads(payload.read_bytes()).get('schema')=='contextrail.native-history.v1'
            except (ValueError,OSError):owned=False
            if not owned:raise Conflict('Refusing to overwrite an unrelated history export')
        text='---\nmanaged_by: context-rail\ntype: context_state_projection\nsource_snapshot: "native-history:'+self.session+'"\n---\n'+marker+'\n# 原生历史恢复材料\n\n这是历史证据。恢复时先核对当前要求、checkout 和任务归属；未返回的动作不自动重跑。\n\n'+canonical(value)+'\n'
        output.mkdir(parents=True,exist_ok=True)
        for path,body in ((payload,canonical(value)),(target,text)):
            tmp=path.with_name(path.name+'.'+uuid4().hex+'.tmp');tmp.write_text(body,encoding='utf-8');tmp.replace(path)
        return {'state':str(target),'history':str(payload),'next_cursor':value['next_cursor'],'gaps':value['gaps'],'source':self.selection()}


def bound_reader(tools,client,native_session):
    binding=tools.query_binding
    if binding is None or binding.memory_home is None or (client,native_session) not in binding.native_sessions:raise InvalidRequest('History requires an exact host-bound native session')
    reader = NativeHistory(binding.memory_home, binding.workspace, client, native_session)
    # Resolve the source independently of model-supplied cursors or refs.
    reader.source = reader.locate()
    return reader
