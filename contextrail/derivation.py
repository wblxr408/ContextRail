"""Original R compaction attempts and source-bound literal material."""
from dataclasses import asdict
import json
from uuid import uuid4

from .fidelity import validate
from .models import Ref,canonical

SCHEMA='''CREATE TABLE IF NOT EXISTS compaction_drafts(
 scope TEXT,id TEXT,compaction TEXT,snapshot TEXT,title TEXT,text TEXT,spans TEXT,status TEXT,
 report TEXT,summary_id TEXT,created REAL,PRIMARY KEY(scope,id));'''


def sources(store,scope,spans):
    result=[]
    for ref in spans:
        artifact=store.get(scope,ref)
        try:text=artifact.content.decode('utf-8')
        except UnicodeError:raise ValueError('Summary source must be exact UTF-8 text') from None
        result.append({'source':asdict(ref),'start':ref.start,'text':text})
    return result


def report(store,scope,state,text,spans):
    required=[*state['constraints'],*state['acceptance']]
    for entry in state['evidence']:
        ref=Ref(**entry['ref'])
        if not entry['required'] and not ref.name.endswith('/resume-intent'):continue
        try:value=json.loads(store.get(scope,ref).content)
        except (UnicodeError,ValueError):continue
        if not isinstance(value,dict):continue
        if value.get('schema')=='contextrail.resume-intent.v1':
            for role in ('latest_request','latest_correction'):
                item=value.get('anchors',{}).get(role)
                if item:required.append(item['statement'])
        elif entry['required'] and isinstance(value.get('statement'),str) and value.get('status')=='active':
            required.append(value['statement'])
    return validate(text,sources(store,scope,spans),required=required)


def record(store,scope,compaction_id,*,title,text,spans,status,report,summary_id=None):
    store.db.execute(SCHEMA)
    identity=uuid4().hex
    store.db.execute('INSERT INTO compaction_drafts VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        (scope.key,identity,compaction_id,store.compaction(scope,compaction_id).snapshot,title,text,
         canonical([asdict(ref) for ref in spans]),status,canonical(report),summary_id,store.clock()))
    store._event(scope,'compaction.draft_'+status,compaction=compaction_id,draft=identity)
    return identity


def attempts(store,scope,compaction_id):
    store.compaction(scope,compaction_id)
    # This is a host management read, not a model mutating surface.
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='compaction_drafts'").fetchone():return []
    return [{**dict(r),'spans':json.loads(r['spans']),'report':json.loads(r['report'])} for r in
        store.db.execute('SELECT * FROM compaction_drafts WHERE scope=? AND compaction=? ORDER BY created,rowid',(scope.key,compaction_id))]


def material(store,scope,summary_id):
    summary=store.summary(scope,summary_id)
    exists=store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='compaction_drafts'").fetchone()
    row=store.db.execute('SELECT report FROM compaction_drafts WHERE scope=? AND summary_id=? AND status=?',(scope.key,summary_id,'accepted')).fetchone() if exists else None
    if row:result=json.loads(row[0])
    else:
        from .fidelity import residue
        result={'accepted':None,'coverage':'legacy_summary_not_prevalidated','residue':residue(sources(store,scope,summary.spans))}
    return {'summary':summary.id,'snapshot_current':store.summary_is_current(scope,summary),**result}


def page(store,scope,summary_id,*,offset=0,limit=20,budget=8192):
    from .errors import InvalidRequest,BudgetExceeded
    from .models import integer
    integer(offset);integer(limit,1);integer(budget,1)
    if limit>100:raise InvalidRequest('Literal page limit exceeds 100')
    value=material(store,scope,summary_id);rows=value['residue']['entries'];selected=[]
    if offset>len(rows):raise InvalidRequest('Literal offset exceeds source entries')
    def output():
        end=offset+len(selected)
        return {'summary':summary_id,'snapshot_current':value['snapshot_current'],'entries':selected,'total':len(rows),
            'offset':offset,'next_offset':end if end<len(rows) else None,'loss':value['residue']['loss'],
            'coverage':value['residue']['coverage'],'range_unit':'source_utf8_bytes','read_original':'context.get'}
    for entry in rows[offset:offset+limit]:
        selected.append(entry)
        if len(canonical(output()).encode())>budget:selected.pop();break
    result=output()
    if len(canonical(result).encode())>budget or not selected and offset<len(rows):raise BudgetExceeded('Literal page cannot fit; raise budget or read original source spans')
    return result
