"""Generation-aware continuation owned by the existing JSONL Store."""
import json,os,time
from pathlib import Path
from .source_events import canonical
from .sanitization import require_safe_identity,sanitize_capture

SCHEMA='''
CREATE TABLE IF NOT EXISTS cursor_generations(logical_key TEXT NOT NULL,generation TEXT NOT NULL,offset INTEGER NOT NULL,
 context TEXT NOT NULL,read_path TEXT NOT NULL,state TEXT NOT NULL,updated REAL NOT NULL,PRIMARY KEY(logical_key,generation));
CREATE TABLE IF NOT EXISTS stream_aliases(read_path TEXT PRIMARY KEY,logical_key TEXT NOT NULL,generation TEXT NOT NULL,client TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stream_incidents(id INTEGER PRIMARY KEY,logical_key TEXT,generation TEXT,reason TEXT,source_offset INTEGER,at REAL);
'''


def identity(stat):return {'device':stat.st_dev,'inode':stat.st_ino,'birth_ns':getattr(stat,'st_birthtime_ns',None)}


def incident(db,key,generation,reason,offset):
    db.execute('INSERT INTO stream_incidents(logical_key,generation,reason,source_offset,at) VALUES(?,?,?,?,?)',(key,generation,reason,offset,time.time()))


def ingest(store,path,kind,*,baseline=False,history=False,stats=None,line_bytes=8*1024*1024,logical_path=None,rotated=False):
    if kind not in {'codex','claude'} or type(line_bytes) is not int or not 256<=line_bytes<=8*1024*1024:raise ValueError('Unsupported native stream bounds')
    # The original Store resolves these through its strict canonical_path owner.
    path=Path(path).absolute();logical=str(Path(logical_path).absolute()) if logical_path is not None else str(path)
    require_safe_identity(logical);key=('backfill:' if history else '')+logical
    db=store.db;count=0
    with db,path.open('rb') as stream:
        db.execute('BEGIN IMMEDIATE');stat=os.fstat(stream.fileno());physical=identity(stat)
        row=db.execute('SELECT * FROM cursors WHERE path=?',(key,)).fetchone();archived=None
        if rotated:
            archived=db.execute("SELECT * FROM cursor_generations WHERE logical_key=? AND json_extract(context,'$._file.identity')=? ORDER BY updated DESC LIMIT 1",(key,canonical(physical))).fetchone()
            if archived is None:
                incident(db,key,'unknown','rotation_identity_unavailable',0);return 0
            context=json.loads(archived['context']);offset=archived['offset']
        else:context=json.loads(row['context']) if row else {};offset=row['offset'] if row else 0
        prior=context.get('_file');epoch=prior.get('epoch',0) if prior else 0;reason='append'
        if prior and prior['identity']==physical and stat.st_size==prior['size'] and stat.st_mtime_ns==prior['mtime_ns'] and (offset==stat.st_size or prior['state']=='partial_line'):
            if not rotated or db.execute('SELECT 1 FROM stream_aliases WHERE read_path=?',(str(path),)).fetchone():return 0
        changed=bool(prior and (prior['identity']!=physical or stat.st_size<prior['size'] or
                      stat.st_size==prior['size'] and stat.st_mtime_ns!=prior['mtime_ns']))
        if changed:
            reason='replaced' if prior['identity']!=physical else 'truncated_or_rewritten';epoch+=1
            context={};offset=0
        elif offset>stat.st_size:reason='legacy_truncated';epoch+=1;context={};offset=0
        generation={**physical,**({'epoch':epoch} if epoch else {})};gen=canonical(generation)
        if prior is None and row is not None and not changed:reason='legacy_identity_unverified'
        meta={'identity':physical,'epoch':epoch,'generation':generation,'size':stat.st_size,'mtime_ns':stat.st_mtime_ns,
              'state':'reading','reason':reason,'limits':['in_place_truncate_and_regrow_between_observations_not_distinguishable']}
        meta['legacy_identity_unverified']=bool(prior and prior.get('legacy_identity_unverified') or prior is None and row is not None)
        if rotated:meta['reason']='rotated_alias'
        context['_file']=meta
        if row is None and baseline and not rotated:
            # Learn only native routing metadata, never baseline user content.
            stream.seek(0)
            for _ in range(16):
                first=stream.readline(min(line_bytes,65536))
                if not first or not first.endswith(b'\n'):break
                try:
                    value=json.loads(first);p=value.get('payload',{})
                    if kind=='codex' and value.get('type')=='session_meta':
                        context.update(cwd=p.get('cwd',''),session=p.get('id') or p.get('session_id') or '');break
                    if kind=='claude' and value.get('sessionId'):
                        context.update(cwd=value.get('cwd',''),session=value['sessionId']);break
                except (ValueError,AttributeError):pass
            offset=stat.st_size
            if offset:
                begin=max(0,offset-line_bytes);stream.seek(begin);tail=stream.read(offset-begin)
                if not tail.endswith(b'\n'):
                    last=tail.rfind(b'\n');offset=begin+last+1 if last>=0 else begin
            stream.seek(offset);meta['state']='baselined_partial_pending' if offset<stat.st_size else 'baselined'
        else:
            stream.seek(offset);processed_bytes=0
            for _ in range(1000):
                start=stream.tell();line=stream.readline(line_bytes+1);processed_bytes+=len(line)
                if not line:
                    meta['state']='oversized_line_pending' if context.get('_discard_line') is not None else 'caught_up';break
                if context.get('_discard_line') is not None:
                    if line.endswith(b'\n'):context.pop('_discard_line');meta['state']='oversized_line_skipped'
                    else:meta['state']='oversized_line_pending'
                elif len(line)>line_bytes:
                    incident(db,key,gen,'oversized_line',start)
                    if not line.endswith(b'\n'):context['_discard_line']=start
                    meta['state']='oversized_line_pending' if context.get('_discard_line') is not None else 'oversized_line_skipped'
                elif not line.endswith(b'\n'):
                    stream.seek(start);meta['state']='partial_line';break
                else:
                    try:
                        record=json.loads(line)
                        count+=store._ingest_record(kind,record,context,logical,start,stream.tell(),generation,history,stats)
                        meta['state']='reading'
                    except (ValueError,TypeError,AttributeError):incident(db,key,gen,'malformed_record',start)
                if processed_bytes>=16*1024*1024:meta['state']='batch_budget_pending';break
        offset=stream.tell()
        if meta['state']=='reading' and offset>=stat.st_size:meta['state']='caught_up'
        context=sanitize_capture(context,'memory').value;raw=canonical(context)
        if not rotated or row is not None and json.loads(row['context']).get('_file',{}).get('generation')==generation:
            db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?,?)',(key,offset,raw))
        db.execute('INSERT OR REPLACE INTO cursor_generations VALUES(?,?,?,?,?,?,?)',(key,gen,offset,raw,str(path),meta['state'],time.time()))
        if rotated:db.execute('INSERT OR REPLACE INTO stream_aliases VALUES(?,?,?,?)',(str(path),logical,gen,kind))
    return count


def status(store,*,limit=20):
    if type(limit) is not int or not 1<=limit<=100:raise ValueError('Stream page requires 1..100')
    rows=store.db.execute('SELECT logical_key,generation,offset,read_path,state,context FROM cursor_generations ORDER BY updated DESC LIMIT ?',(limit,)).fetchall()
    entries=[{k:r[k] for k in ('logical_key','generation','offset','read_path','state')}|{'reason':json.loads(r['context']).get('_file',{}).get('reason'),'legacy_identity_unverified':json.loads(r['context']).get('_file',{}).get('legacy_identity_unverified')} for r in rows]
    errors=[dict(r) for r in store.db.execute('SELECT logical_key,reason,source_offset,at FROM stream_incidents ORDER BY id DESC LIMIT ?',(limit,))]
    return {'schema':'contextrail.stream-cursors.v1','entries':entries,'incidents':errors,
            'total_generations':store.db.execute('SELECT count(*) FROM cursor_generations').fetchone()[0],
            'coverage':'observed_generations_and_registered_move_aliases; unobserved_rotation_paths_unknown'}
