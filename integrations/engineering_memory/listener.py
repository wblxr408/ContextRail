"""Local Codex/Claude transcript listener; native notifications plus reconciliation."""
from __future__ import annotations
import argparse
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import re
import sqlite3
import subprocess
import sys
import threading
import time
import tomllib
from datetime import datetime
from memory_runtime import MemoryRuntime, runtime_from_config
from memory_integration import capture_claims
from canonical import canonical_path
from capture_sanitizer import sanitize_capture, clean_text, require_safe_identity
from event_identity import SCHEMA as SOURCE_EVENT_SCHEMA, source_identity, admit_event, identity_status, workspace_key
from native_event_stream import SCHEMA as NATIVE_EVENT_SCHEMA, capture_record, list_events, read_event_range, native_timeline, reindex_timeline
from capture_policy import SCHEMA as CAPTURE_SCHEMA, claim_owner, delegate_hook, capture_status
from learning_origin import POLICY as ORIGIN_POLICY, project_candidate

HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'vendor'))
DEFAULT_HOME = Path.home() / '.codex/services/engineering-memory'
SIGNAL = re.compile(r'root cause|fixed|verified|regression|workaround|decision|prefer|always|never|lesson|because|failed|passed|根因|修复|验证|踩坑|经验|教训|决定|约定|以后|始终|不要|失败|通过|原因|采用|回退', re.I)
SENSITIVE = re.compile(r'(?i)(?:\b(?:sk-|ghp_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._-]{12,}|(?:api[_-]?key|password|secret|access[_-]?token|refresh[_-]?token)\s*[:=]\s*["\']?[^\s"\',;]{6,})')


def clean(text, limit=4000):
    return clean_text(str(text),limit)


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def content_text(content):
    if isinstance(content, str):
        return content
    return '\n'.join(b.get('text', '') for b in (content or [])
                     if isinstance(b, dict) and b.get('type') in ('text', 'input_text', 'output_text'))


def meaningful(user, final):
    text = user + '\n' + final
    explicit = len(user.strip()) >= 20 and bool(re.search(r'以后|始终|约定|偏好|记住|from now on|always|prefer',user,re.I))
    return explicit or (len(text.strip()) >= 100 and bool(SIGNAL.search(text)))


def reduce_record(kind, row, context):
    """Returns at most one completed-turn candidate. Never reads reasoning blocks."""
    candidate = None
    if kind == 'codex':
        p = row.get('payload', {})
        if row.get('type') == 'session_meta':
            context['cwd'] = clean(p.get('cwd', ''), 500)
            context['session'] = str(p.get('id', p.get('session_id', '')))
        if row.get('type')=='turn_context' and p.get('cwd'):
            context['cwd']=clean(p['cwd'],500)
        if row.get('type')=='turn_context' and p.get('turn_id'):
            context['turn_id']=str(p['turn_id'])
        if row.get('type') == 'response_item' and p.get('type') == 'message':
            role = p.get('role')
            text = content_text(p.get('content'))
            if role == 'user':
                context['user'] = clean(text, None)
                context['_native_user_flags'] = {'is_meta':bool(p.get('isMeta') or row.get('isMeta')), 'is_compact_summary':bool(p.get('isCompactSummary') or row.get('isCompactSummary'))}
            if role == 'assistant' and p.get('phase', p.get('channel')) == 'final':
                context['final'] = clean(text, None)
        if row.get('type') == 'event_msg' and p.get('type') in ('task_complete', 'turn_complete'):
            final = clean(p.get('last_agent_message') or context.get('final', ''), None)
            candidate = {'user': context.get('user', ''), 'final': final,
                         'cwd': context.get('cwd', ''), 'marker': str(p.get('turn_id') or row.get('turn_id') or context.get('turn_id',''))}
            context.pop('final', None)
    elif kind == 'claude':
        if row.get('isSidechain'):
            return None
        message = row.get('message', {})
        if not isinstance(message, dict):
            return None
        context['cwd'] = clean(row.get('cwd', context.get('cwd', '')), 500)
        context['session'] = str(row.get('sessionId',context.get('session','')))
        if row.get('type') == 'user':
            text = content_text(message.get('content'))
            if text:
                context['user'] = clean(text, None)
                context['_native_user_flags'] = {'is_meta':bool(row.get('isMeta')), 'is_compact_summary':bool(row.get('isCompactSummary'))}
        if row.get('type') == 'assistant':
            text = content_text(message.get('content'))
            mid = message.get('id') or row.get('uuid') or ''
            if context.get('message_id') != mid:
                context['parts'] = []
                context['message_id'] = mid
            if text and text not in context.get('parts', []):
                context.setdefault('parts', []).append(clean(text, None))
            if message.get('stop_reason') in ('end_turn', 'stop_sequence'):
                candidate = {'user': context.get('user', ''),
                             'final': clean('\n'.join(context.get('parts', [])), None),
                             'cwd': context.get('cwd', ''), 'marker': str(mid)}
                context['parts'] = []
    if candidate:
        for field in ('_native_user_flags','_raw_user_event'):
            if field in context:candidate[field]=context[field]
    return candidate


class Store:
    def __init__(self, home):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.home / 'state.sqlite', timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS cursors(path TEXT PRIMARY KEY, offset INTEGER, context TEXT);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, source TEXT, payload TEXT,
          state TEXT, created REAL, available REAL, attempts INTEGER DEFAULT 0,
          result TEXT, error TEXT, files TEXT);
        CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY, started REAL, tokens INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
        ''')
        self.db.commit()
        self.db.executescript(SOURCE_EVENT_SCHEMA)
        self.db.executescript(NATIVE_EVENT_SCHEMA)
        self.db.executescript(CAPTURE_SCHEMA)

    @staticmethod
    def event_workspace(value):
        if not value:
            return ''
        try:
            return workspace_key(canonical_path(value))
        except OSError:
            return workspace_key(os.path.normpath(value))

    def ingest(self, path, kind, baseline=False, history=False, stats=None):
        path = Path(path)
        key = require_safe_identity(str(canonical_path(path)))
        cursor_key = 'backfill:'+key if history else key
        row = self.db.execute('SELECT * FROM cursors WHERE path=?', (cursor_key,)).fetchone()
        size = path.stat().st_size
        if row is not None and row['offset'] == size:
            return 0
        if row is None and baseline:
            # Existing history is not backfilled automatically.
            with self.db:
                self.db.execute('INSERT INTO cursors VALUES(?,?,?)', (cursor_key, size, '{}'))
            return 0
        offset = row['offset'] if row and row['offset'] <= size else 0
        context = json.loads(row['context']) if row and row['offset'] <= size else {}
        count = 0
        with self.db, path.open('rb') as f:
            self.db.execute('BEGIN IMMEDIATE')
            stat=os.fstat(f.fileno())
            generation={'device':stat.st_dev,'inode':stat.st_ino,'birth_ns':getattr(stat,'st_birthtime_ns',None)}
            f.seek(offset)
            for _ in range(1000):
                start = f.tell()
                line = f.readline(8 * 1024 * 1024)
                if not line or not line.endswith(b'\n'):
                    f.seek(start)
                    break
                try:
                    record = json.loads(line)
                    outcomes=capture_record(self.db,kind,record,context,key,start,f.tell(),generation,self.event_workspace)
                    if stats is not None:
                        stats['raw_events']=stats.get('raw_events',0)+outcomes.count('inserted')
                    c = reduce_record(kind, record, context)
                except (ValueError, TypeError, AttributeError):
                    logging.warning('Malformed record skipped: %s:%s', path.name, start)
                    continue
                if not c or not c['final']:
                    continue
                since=self.db.execute("SELECT value FROM meta WHERE key='since'").fetchone()
                if since and not history and record.get('timestamp'):
                    try:
                        if datetime.fromisoformat(record['timestamp'].replace('Z','+00:00')).timestamp()<float(since[0]):
                            continue
                    except (ValueError,TypeError):
                        pass
                c.update(kind=kind, source=key, offset=start, occurred_at=record.get('timestamp') or datetime.now().astimezone().isoformat())
                require_safe_identity(context.get('session') or path.stem)
                require_safe_identity(c['marker'])
                processed=sanitize_capture(c,'memory')
                c=processed.value
                c['_capture']=processed.provenance()
                legacy_id = digest(kind + (context.get('session') or path.stem) + c['marker'] + c['final'])
                event=source_identity(kind+'/completed-turn',self.event_workspace(c.get('cwd')),
                    context.get('session',''),c['marker'],key,start,generation)
                event['workspace_resolution']='canonical_or_declared_if_unavailable' if c.get('cwd') else 'missing_workspace'
                owner=claim_owner(self.db,event['coordinates']['workspace'],kind,context.get('session',''))
                learning=project_candidate(c,event['id'])
                c.update(_origin=learning['_origin'],producer='existing_jsonl_listener',capture_owner=owner)
                state='queued' if learning['_origin']['eligible_for_learning'] and meaningful(learning['user'],learning['final']) else 'filtered'
                admitted=admit_event(self.db,event,c,key,legacy_ids=(legacy_id,),state=state,
                    available=time.time()+30,normalize_workspace=self.event_workspace)
                inserted=int(admitted['outcome']=='inserted')
                if admitted['outcome']!='inserted' and stats is not None:
                    name=admitted['outcome'];stats[name]=stats.get(name,0)+1
                count += inserted
                if stats is not None and inserted:stats[state]=stats.get(state,0)+inserted
            self.db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?,?)',
                            (cursor_key, f.tell(), json.dumps(sanitize_capture(context,'memory').value, ensure_ascii=False)))
        return count

    def backfill(self,path,kind,stats=None):
        """Explicit replay uses a separate cursor and the same event identities and queue."""
        return self.ingest(path,kind,history=True,stats=stats)

    def retry_jobs(self,identities,reason):
        """Explicitly retry selected failures; retain their audit and quota charges."""
        identities=list(dict.fromkeys(identities))
        if not identities or not isinstance(reason,str) or not reason.strip():
            raise ValueError('Recovery requires selected event IDs and a reason')
        count=0
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            rows=[self.db.execute('SELECT * FROM jobs WHERE id=?',(identity,)).fetchone() for identity in identities]
            if any(row is None for row in rows):raise ValueError('Recovery selection contains an unknown event')
            self.db.execute('CREATE TABLE IF NOT EXISTS job_recoveries(id INTEGER PRIMARY KEY,job_id TEXT,prior_state TEXT,prior_attempts INTEGER,prior_error TEXT,recovered_at REAL,reason TEXT)')
            for row in rows:
                if row['state'] not in {'failed','verification_failed'}:continue
                self.db.execute('INSERT INTO job_recoveries(job_id,prior_state,prior_attempts,prior_error,recovered_at,reason) VALUES(?,?,?,?,?,?)',
                    (row['id'],row['state'],row['attempts'],row['error'],time.time(),clean(reason,1000)))
                state='awaiting_index' if row['state']=='verification_failed' else ('ready_to_save' if row['result'] else 'queued')
                self.db.execute('UPDATE jobs SET state=?,attempts=0,available=?,error=NULL WHERE id=?',(state,time.time(),row['id']))
                count+=1
        return count


def resolve_codex():
    base = Path.home() / 'AppData/Roaming/npm/node_modules/@openai'
    paths = list(base.rglob('codex.exe'))
    if not paths:
        raise RuntimeError('Installed Codex CLI executable not found')
    return str(paths[0])


def judge(config, home, payload, existing, schema=None, instructions=None):
    run = Path(home) / 'judge'
    run.mkdir(exist_ok=True)
    result_file = run / 'result.json'
    result_file.unlink(missing_ok=True)
    user_cfg = tomllib.loads((Path.home()/'.codex/config.toml').read_text(encoding='utf-8-sig'))
    model = config.get('model') or user_cfg.get('model')
    cmd = [resolve_codex(), 'exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check',
           '--sandbox', 'read-only', '--json', '--color', 'never', '-C', str(run),
           '--output-schema', str(schema or HERE/'judge-schema.json'), '-o', str(result_file),
           '-c', 'project_doc_max_bytes=1', '-c', 'model_reasoning_effort="low"',
           '-c', 'model_instructions_file='+json.dumps(str(instructions or HERE/'judge-instructions.txt'))]
    if model:
        cmd += ['-m', model]
    for f in ['plugins','hooks','apps','shell_tool','unified_exec','multi_agent','browser_use',
              'computer_use','image_generation','workspace_dependencies','skill_search','code_mode',
              'code_mode_host','goals','request_permissions_tool']:
        cmd += ['--disable', f]
    skills = []
    for root in (Path.home()/'.codex/skills', Path.home()/'.agents/skills'):
        for p in root.rglob('SKILL.md'):
            skills.append('{path='+json.dumps(str(p))+',enabled=false}')
    cmd += ['-c', 'skills.config=['+','.join(skills)+']', '-']
    data = json.dumps({'event': payload, 'existing_notes': existing}, ensure_ascii=False)
    input_file=run/'input.json'
    input_file.write_text(data,encoding='utf-8')
    stdout_file=run/'last-events.jsonl';stderr_file=run/'last-stderr.txt'
    environment=os.environ.copy()
    if config.get('codex_runtime_home'):environment['CODEX_HOME']=str(config['codex_runtime_home'])
    with input_file.open('rb') as stream, stdout_file.open('wb') as stdout, stderr_file.open('wb') as stderr:
        p = subprocess.Popen(cmd, stdin=stream, stdout=stdout, stderr=stderr,
                             env=environment,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        p.wait(timeout=config['judge_timeout_seconds'])
    except subprocess.TimeoutExpired:
        try:
            subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],capture_output=True,timeout=10)
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
        out,err=stdout_file.read_bytes(),stderr_file.read_bytes()
        raise TimeoutError('Judge timed out: '+clean((err+out).decode('utf-8',errors='replace')[-800:],800))
    finally:
        input_file.unlink(missing_ok=True)
    out,err=stdout_file.read_bytes(),stderr_file.read_bytes()
    if p.returncode or not result_file.exists():
        raise RuntimeError(clean((err+out).decode('utf-8', errors='replace')[-1500:],1500))
    usage = {}
    for line in out.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('type') == 'turn.completed':
            usage = event.get('usage', {})
        if event.get('type') == 'item.completed':
            item=event.get('item',{})
            if item.get('type')=='error':
                logging.warning('Classifier diagnostic: %s',clean(item.get('message',''),500))
            elif item.get('type') not in ('agent_message','reasoning'):
                raise RuntimeError('Judge attempted an unexpected tool operation: '+str(item.get('type')))
    result = json.loads(result_file.read_text(encoding='utf-8'))
    atomic_json(run/'last-usage.json', usage)
    return result, usage


def existing_notes(vault, payload, runtime=None):
    owned = runtime is None
    if owned:
        # In-memory lifecycle cache for direct calls; persistent service reuses its own state DB.
        runtime = MemoryRuntime(vault, ':memory:', use_models=False)
    try:
        query = (payload.get('user', '')[-2400:] + '\n' + payload.get('final', '')[-2400:]).strip()
        if not query:
            return []
        result = runtime.recall(query, scope=payload.get('cwd'), limit=6)
        return [{'path':r['path'], 'excerpt':r['excerpt'], 'status':r['status'],
                 'permalink':r['permalink'], 'date':r['date']} for r in result['results']]
    finally:
        if owned:
            runtime.close()


def validate_notes(result, payload, existing):
    if not isinstance(result,dict) or not isinstance(result.get('notes'),list) or len(result['notes']) > 3:
        raise ValueError('Invalid judge response')
    approved=[]
    for note in result['notes']:
        if note.get('kind') not in ('pitfall','decision','preference','pattern'):
            raise ValueError('Invalid kind')
        for field,limit in [('title',120),('scope',600),('lesson',1800),('limitations',600)]:
            if not isinstance(note.get(field),str) or not note[field].strip() or len(note[field])>limit:
                raise ValueError('Invalid '+field)
        for field in ('subject','predicate','value'):
            if field in note and (not isinstance(note[field],str) or not note[field].strip() or len(note[field])>1800):
                raise ValueError('Invalid observation '+field)
        quotes=note.get('evidence',[])
        if not 1 <= len(quotes) <= 3:
            raise ValueError('Missing evidence')
        for q in quotes:
            if q.get('source') not in ('user','final') or not 12<=len(q.get('quote',''))<=600 or q['quote'] not in payload[q['source']]:
                raise ValueError('Evidence quote not present in source')
        if note['kind']=='preference' and not any(q['source']=='user' for q in quotes):
            raise ValueError('Preference must have explicit user evidence')
        target=note.get('related_path','')
        if target and target not in {x['path'] for x in existing}:
            raise ValueError('Unknown related note')
        if SENSITIVE.search(json.dumps(note,ensure_ascii=False)):
            raise ValueError('Sensitive-looking value rejected')
        approved.append(note)
    return approved


def save_notes(config, jid, payload, notes):
    vault=Path(config['vault']).resolve()
    folder=vault/'40-Automatic'
    folder.mkdir(exist_ok=True)
    saved=[]
    for index,note in enumerate(notes):
        # Stable file names make crash/retry idempotent. Never overwrite human notes.
        path=folder/(jid[:24]+'-'+str(index)+'.md')
        status='accepted' if note['kind']=='preference' else 'experimental'
        meta={'title':note['title'],'type':note['kind'],'status':status,
              'date':(payload.get('occurred_at') or time.strftime('%Y-%m-%d')),'permalink':'main/40-automatic/'+path.stem,
              'cwd':payload.get('cwd') or 'global',
              'source_client':payload['kind'],'source_event':jid,'automatic_capture':True,
              'derived_from':payload.get('_origin',{}).get('derived_from',[]),
              'history_origin':'automatic_capture'}
        fm='\n'.join(k+': '+json.dumps(v,ensure_ascii=False) for k,v in meta.items())
        evidence='\n'.join('- ['+q['source']+'] '+q['quote'].replace('\n',' ') for q in note['evidence'])
        related='\n\n## Related\n\n'+note['related_path'] if note.get('related_path') else ''
        text=f'---\n{fm}\n---\n\n# {note["title"]}\n\n## Applies when\n\n{note["scope"]}\n\n## Lesson\n\n{note["lesson"]}\n\n## Evidence\n\n{evidence}\n\nSource: {payload["source"]} (byte {payload["offset"]})\n\n## Limits\n\n{note["limitations"]}\n\nAutomatically extracted from conversation; technical claims have not been independently rerun by this listener.{related}\n'
        if not path.exists():
            with path.open('x',encoding='utf-8') as f:
                f.write(text)
        if path.read_text(encoding='utf-8') != text:
            raise RuntimeError('Existing capture differs; manual review required')
        saved.append({'path':str(path),'permalink':meta['permalink'],'title':note['title']})
    return saved


def verify_files(config, files):
    for file in files:
        python=Path(sys.executable)
        if python.name.lower()=='pythonw.exe': python=python.with_name('python.exe')
        cmd=[str(python),config['query_helper'],'--identifier',file['permalink'],'--timeout','30']
        p=subprocess.run(cmd,capture_output=True,timeout=95,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if p.returncode:
            raise RuntimeError('Basic Memory verification failed')
        result=json.loads(p.stdout)
        text='\n'.join(b.get('text','') for b in result.get('content',[]) if b.get('type')=='text')
        if file['title'] not in text or 'source_event:' not in text:
            raise RuntimeError('Saved note not visible through Basic Memory yet')


class Service:
    def __init__(self, home, config):
        self.home=Path(home); self.config=config; self.store=Store(home)
        self.lock=threading.Lock(); self.events=set(); self.wake=threading.Event()
        self.stop=threading.Event(); self.observer=None
        self.memory = runtime_from_config(config, home)

    def roots(self):
        return [(s['kind'],Path(s['path']).expanduser().resolve()) for s in self.config['sources'] if s.get('enabled',True)]

    def discover(self,baseline=False):
        count=0
        for kind,root in self.roots():
            if not root.exists(): continue
            for path in root.rglob('*.jsonl'):
                if 'subagents' in path.parts: continue
                try: count+=self.store.ingest(path,kind,baseline)
                except (OSError,sqlite3.Error): logging.exception('Cannot ingest %s',path.name)
        return count

    def changed(self,path):
        if str(path).endswith('.jsonl'):
            with self.lock: self.events.add(str(path))
            self.wake.set()

    def flush_events(self):
        with self.lock:
            events,self.events=self.events,set()
        for name in events:
            path=Path(name)
            for kind,root in self.roots():
                if path.is_relative_to(root) and 'subagents' not in path.parts and path.exists():
                    try: self.store.ingest(path,kind)
                    except (OSError,sqlite3.Error): logging.exception('Event ingestion failed')
                    break

    def process_one(self):
        db=self.store.db; now=time.time()
        job=db.execute("SELECT * FROM jobs WHERE state IN ('queued','retry','ready_to_save','awaiting_index') AND available<=? ORDER BY (state='awaiting_index') DESC,(state='ready_to_save') DESC,created LIMIT 1",(now,)).fetchone()
        if not job: return
        jid=job['id']; payload=json.loads(job['payload'])
        try:
            processed=sanitize_capture(payload,'memory')
            payload=processed.value
            payload['_capture']=processed.provenance()
            with db: db.execute('UPDATE jobs SET payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),jid))
            if job['state']=='awaiting_index':
                verify_files(self.config,json.loads(job['files']))
                with db: db.execute("UPDATE jobs SET state='saved',error=NULL WHERE id=?",(jid,))
                return
            learning=project_candidate(payload,payload.get('_event',{}).get('id') or jid)
            prior_policy=payload.get('_origin',{}).get('learning_policy')
            payload['_origin']=learning['_origin']
            with db:db.execute('UPDATE jobs SET payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),jid))
            if not learning['_origin']['eligible_for_learning'] or not meaningful(learning['user'],learning['final']):
                with db:db.execute("UPDATE jobs SET state='filtered',error=NULL WHERE id=?",(jid,))
                return
            # Old results may quote generated history that is absent from the new
            # learning view. Preserve their audit/charges and rejudge once.
            if job['state']=='ready_to_save' and prior_policy!=ORIGIN_POLICY and any(learning[k]!=payload[k] for k in ('user','final')):
                with db:
                    db.execute('CREATE TABLE IF NOT EXISTS origin_rejudgments(job_id TEXT,prior_result TEXT,rejudged_at REAL)')
                    db.execute('INSERT INTO origin_rejudgments VALUES(?,?,?)',(jid,job['result'],time.time()))
                    db.execute("UPDATE jobs SET state='queued',result=NULL,available=? WHERE id=?",(time.time(),jid))
                return
            payload=learning
            if job['state']=='ready_to_save':
                envelope=sanitize_capture(json.loads(job['result']),'memory').value
                with db: db.execute('UPDATE jobs SET result=? WHERE id=?',(json.dumps(envelope,ensure_ascii=False),jid))
                notes=validate_notes(envelope['judgment'],payload,envelope['existing'])
                files=save_notes(self.config,jid,payload,notes)
                capture_claims(self.memory,jid,payload,notes)
                with db:
                    db.execute('UPDATE jobs SET state=?,files=?,available=?,error=NULL WHERE id=?',
                        ('awaiting_index' if files else 'skipped',json.dumps(files),time.time()+10,jid))
                return
            from model_synthesis import reserve_call,BudgetExceeded
            reserve=self.config.get('reserve_tokens_per_call',12000)
            try:
                callid=reserve_call(db,self.config)
            except BudgetExceeded:
                return
            with db:
                db.execute("UPDATE jobs SET state='processing',attempts=attempts+1 WHERE id=?",(jid,))
            atomic_json(self.home/'status.json',self.status())
            existing=existing_notes(self.config['vault'],payload,self.memory)
            selected=dict(payload)
            # Full redacted evidence stays in the durable queue. Model windows prioritize final user corrections.
            for role in ('user','final'):
                text=payload[role]
                selected[role]=text if len(text)<=4000 else text[:500]+'\n[中段省略，完整原文在源事件]\n'+text[-3400:]
            selected['known_observation_keys']=[{'subject':o['subject'],'predicate':o['predicate']} for o in self.memory.observations(payload.get('cwd'))][:30]
            if self.config.get('resource_manager',{}).get('enabled'):
                result,usage=self.memory.invoke('judge',selected,existing)
            else:
                result,usage=judge(self.config,self.home,selected,existing)
            result=sanitize_capture(result,'memory').value
            total=usage.get('input_tokens',0)+usage.get('output_tokens',0)
            with db:
                db.execute('UPDATE calls SET tokens=? WHERE id=?',(total or reserve,callid))
                db.execute("UPDATE jobs SET result=?,state='ready_to_save' WHERE id=?",(json.dumps({'judgment':result,'existing':existing},ensure_ascii=False),jid))
            notes=validate_notes(result,payload,existing)
            files=save_notes(self.config,jid,payload,notes)
            capture_claims(self.memory,jid,payload,notes)
            with db:
                db.execute('UPDATE jobs SET state=?,files=?,available=?,error=NULL WHERE id=?',
                    ('awaiting_index' if files else 'skipped',json.dumps(files),time.time()+10,jid))
            logging.info('Judged %s: notes=%s tokens=%s',jid[:12],len(files),total)
        except Exception as exc:
            attempt=job['attempts']+1
            state='awaiting_index' if job['state']=='awaiting_index' else ('retry' if attempt<3 else 'failed')
            current=db.execute('SELECT state FROM jobs WHERE id=?',(jid,)).fetchone()[0]
            if current=='ready_to_save': state='ready_to_save' if attempt<3 else 'failed'
            if job['state']=='awaiting_index' and attempt>=6: state='verification_failed'
            with db:
                db.execute('UPDATE jobs SET state=?,attempts=?,available=?,error=? WHERE id=?',
                    (state,attempt,time.time()+min(3600,300*attempt),clean(str(exc),1200),jid))
            logging.warning('Job %s %s: %s',jid[:12],state,clean(str(exc),300))

    def status(self):
        db=self.store.db
        return {'pid':os.getpid(),'updated':time.time(),'sources':[{'kind':k,'path':str(p),'exists':p.exists()} for k,p in self.roots()],
                'native_observer':bool(self.observer and self.observer.is_alive()),
                'jobs':dict(db.execute('SELECT state,count(*) FROM jobs GROUP BY state').fetchall()),
                'calls_24h':db.execute('SELECT count(*) FROM calls WHERE started>?',(time.time()-86400,)).fetchone()[0],
                'tokens_24h':db.execute('SELECT coalesce(sum(tokens),0) FROM calls WHERE started>?',(time.time()-86400,)).fetchone()[0]}

    def run(self):
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
        service=self
        class Handler(FileSystemEventHandler):
            def on_modified(self,e): service.changed(e.src_path)
            def on_created(self,e): service.changed(e.src_path)
            def on_moved(self,e): service.changed(e.dest_path)
        self.observer=Observer()
        for _,root in self.roots():
            if root.exists(): self.observer.schedule(Handler(),str(root),recursive=True)
        # On first installation, baseline old files before accepting new notifications.
        initialized=self.store.db.execute("SELECT value FROM meta WHERE key='initialized'").fetchone()
        if not initialized:
            with self.store.db:
                self.store.db.execute("INSERT OR REPLACE INTO meta VALUES('since',?)",(str(time.time()),))
        self.discover(baseline=not bool(initialized))
        with self.store.db:
            self.store.db.execute("INSERT OR REPLACE INTO meta VALUES('initialized','1')")
            self.store.db.execute("UPDATE jobs SET state='retry' WHERE state='processing'")
        self.observer.start()
        self.discover()  # Covers the small baseline-to-watcher gap.
        # Finish already-due approved maintenance on restart before a model job
        # can occupy this loop; subsequent scans keep the configured cadence.
        next_scan=time.monotonic()
        broker=None
        if self.config.get('resource_manager',{}).get('enabled'):
            from resource_broker import ResourceBroker
            broker=ResourceBroker(self.home,self.config)
            broker.start()
        logging.info('Listener started')
        try:
            while not (self.home/'stop').exists():
                self.wake.wait(2); self.wake.clear()
                self.flush_events()
                if time.monotonic()>=next_scan:
                    self.discover(); next_scan=time.monotonic()+self.config['reconcile_seconds']
                    if self.config.get('checkpoint_lifecycle_enabled'):
                        try:
                            from checkpoint_lifecycle import from_config
                            result=from_config(self.config,self.home).purge()
                            if result['purged'] or result['blocked']:logging.info('Checkpoint maintenance: %s',result)
                        except Exception:
                            logging.exception('Checkpoint maintenance deferred; originals retained')
                self.process_one()
                atomic_json(self.home/'status.json',self.status())
        finally:
            if broker:broker.close()
            self.memory.close()
            self.observer.stop(); self.observer.join(timeout=5)
            logging.info('Listener stopped')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['watch','status','stop','scan','events','stream','stream-get','capture-hook','capture-status','timeline','timeline-reindex'])
    parser.add_argument('--home',default=str(DEFAULT_HOME))
    parser.add_argument('--event-id')
    parser.add_argument('--offset',type=int,default=0)
    parser.add_argument('--limit',type=int,default=20)
    parser.add_argument('--workspace')
    parser.add_argument('--client',choices=['codex','claude'])
    parser.add_argument('--native-session')
    parser.add_argument('--role',choices=['user','assistant','tool','lifecycle'])
    parser.add_argument('--budget',type=int,default=8192)
    parser.add_argument('--start',type=int,default=0)
    parser.add_argument('--end',type=int)
    parser.add_argument('--hook-event',choices=['UserPromptSubmit','PostToolUse','Stop','SessionStart','PreCompact'])
    parser.add_argument('--file')
    parser.add_argument('--error-category')
    parser.add_argument('--since')
    parser.add_argument('--until')
    parser.add_argument('--around')
    args=parser.parse_args(); home=Path(args.home); home.mkdir(parents=True,exist_ok=True)
    if args.command in {'timeline','timeline-reindex'}:
        try:
            if args.command=='timeline-reindex':
                store=Store(home);db=store.db
            else:
                db=sqlite3.connect((home/'state.sqlite').resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
                db.execute('BEGIN')
            try:
                selection=(db,Store.event_workspace(args.workspace),args.client,args.native_session)
                output=reindex_timeline(*selection,limit=args.limit) if args.command=='timeline-reindex' else native_timeline(*selection,offset=args.offset,limit=args.limit,budget=args.budget,file=args.file,role=args.role,error_category=args.error_category,since=args.since,until=args.until,around=args.around)
                print(json.dumps(output,ensure_ascii=False))
            finally:db.close()
        except (ValueError,TypeError,sqlite3.Error):
            print(json.dumps({'error':'timeline_unavailable','message':'Invalid timeline selection or unavailable source index'}));raise SystemExit(1)
        return
    if args.command=='capture-hook':
        try:
            raw=sys.stdin.buffer.read(1048577)
            if len(raw)>1048576:raise ValueError('Hook input exceeds limit')
            data=json.loads(raw)
            if not isinstance(data,dict):raise ValueError('Invalid Hook envelope')
            workspace=Store.event_workspace(data.get('cwd'))
            session=data.get('session_id');event=data.get('turn_id') or data.get('tool_use_id')
            if not workspace or not isinstance(session,str) or not session:raise ValueError('Exact Hook workspace/session required')
            store=Store(home)
            with store.db:
                store.db.execute('BEGIN IMMEDIATE')
                output=delegate_hook(store.db,workspace,args.client,session,args.hook_event,event)
            print(json.dumps(output,ensure_ascii=False))
        except (ValueError,TypeError,sqlite3.Error):
            print(json.dumps({'error':'capture_hook_rejected'}));raise SystemExit(1)
        return
    if args.command=='capture-status':
        db=sqlite3.connect((home/'state.sqlite').resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
        try:print(json.dumps(capture_status(db,Store.event_workspace(args.workspace),offset=args.offset,limit=args.limit),ensure_ascii=False))
        finally:db.close()
        return
    if args.command in {'stream','stream-get'}:
        try:
            db=sqlite3.connect((home/'state.sqlite').resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
            try:
                selection=(db,Store.event_workspace(args.workspace),args.client,args.native_session)
                output=list_events(*selection,offset=args.offset,limit=args.limit,budget=args.budget,role=args.role) if args.command=='stream' else read_event_range(*selection,args.event_id,start=args.start,end=args.end)
                print(json.dumps(output,ensure_ascii=False))
            finally:db.close()
        except (ValueError,TypeError,sqlite3.Error):
            print(json.dumps({'error':'native_event_unavailable','message':'Invalid source selection, range or unavailable event evidence'}))
            raise SystemExit(1)
        return
    if args.command=='events':
        db=sqlite3.connect((home/'state.sqlite').resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
        try: print(json.dumps(identity_status(db,event_id=args.event_id,offset=args.offset,limit=args.limit),ensure_ascii=False))
        finally: db.close()
        return
    if args.command=='stop': (home/'stop').touch(); return
    if args.command=='status':
        print((home/'status.json').read_text(encoding='utf-8') if (home/'status.json').exists() else '{"running":false}')
        return
    import msvcrt
    handle=(home/'listener.lock').open('a+b'); handle.seek(0)
    try: msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
    except OSError: return
    handler=RotatingFileHandler(home/'listener.log',maxBytes=1000000,backupCount=3,encoding='utf-8')
    logging.basicConfig(level=logging.INFO,handlers=[handler],format='%(asctime)s %(levelname)s %(message)s')
    config=json.loads((home/'config.json').read_text(encoding='utf-8'))
    service=Service(home,config)
    if args.command=='scan': service.discover(); service.process_one(); print(json.dumps(service.status())); return
    if (home/'stop').exists(): return
    service.run()


if __name__=='__main__':
    main()
