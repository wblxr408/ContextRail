"""Versioned projections in the existing engineering-memory owner and database."""
import json
from datetime import datetime,timezone
from uuid import uuid4,uuid5,NAMESPACE_URL

TABLES=(
    'CREATE TABLE IF NOT EXISTS memory_page_versions(name TEXT,id TEXT,payload TEXT,created TEXT,PRIMARY KEY(name,id))',
    'CREATE TABLE IF NOT EXISTS memory_page_current(name TEXT PRIMARY KEY,version TEXT,invalid_reason TEXT)',
    'CREATE TABLE IF NOT EXISTS memory_page_attempts(id TEXT PRIMARY KEY,name TEXT,status TEXT,candidate TEXT,report TEXT,created TEXT)',
)


def page(memory,name,query,scope,identity,*,refresh=False,generator=None,history=False,version=None):
    from memory_runtime import atomic_text,markdown,parse_note
    db=memory.db
    for statement in TABLES:db.execute(statement)
    current=db.execute('SELECT * FROM memory_page_current WHERE name=?',(identity,)).fetchone()
    legacy=db.execute('SELECT * FROM memory_pages WHERE name=?',(identity,)).fetchone()
    if current is None and legacy:
        revision='legacy-'+uuid4().hex
        payload={'version':revision,'query':legacy['query'],'content':legacy['content'],'citations':json.loads(legacy['citations']),
                 'generation':legacy['generation'],'source_bindings':[],'validation':{'coverage':'legacy_unverified'}}
        with db:
            db.execute('INSERT INTO memory_page_versions VALUES(?,?,?,?)',(identity,revision,json.dumps(payload,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
            db.execute('INSERT INTO memory_page_current VALUES(?,?,?)',(identity,revision,'legacy_unverified'))
        current=db.execute('SELECT * FROM memory_page_current WHERE name=?',(identity,)).fetchone()
    if history:
        return {'name':name,'scope':scope or 'global','current_version':current['version'] if current else None,
            'versions':[{'version':r['id'],'created':r['created']} for r in db.execute('SELECT id,created FROM memory_page_versions WHERE name=? ORDER BY created DESC LIMIT 20',(identity,))],
            'attempts':[{**dict(r),'candidate':json.loads(r['candidate']),'report':json.loads(r['report'])} for r in
                db.execute('SELECT id,status,candidate,report,created FROM memory_page_attempts WHERE name=? ORDER BY created DESC LIMIT 20',(identity,))]}
    if version:
        row=db.execute('SELECT payload FROM memory_page_versions WHERE name=? AND id=?',(identity,version)).fetchone()
        if row is None:raise ValueError('Unknown page version in this scope')
        return {'name':name,'scope':scope or 'global','historical':True,'stale':True,**json.loads(row[0])}
    previous=None
    if current:
        previous=json.loads(db.execute('SELECT payload FROM memory_page_versions WHERE name=? AND id=?',(identity,current['version'])).fetchone()[0])
    generation=memory._generation()
    stale=not previous or bool(current['invalid_reason']) or previous['generation']!=generation or previous['query']!=query
    stable=uuid5(NAMESPACE_URL,identity).hex
    projection=memory._managed_path('engineering/knowledge-pages',stable+'.md')

    def render(value):
        return markdown({'title':name,'type':'knowledge_page','managed_by':'engineering-memory','source_query':value['query'],
            'scope':scope or 'global','citations':value['citations'],'version':value['version'],
            'current_semantics':'read memory_knowledge_page to validate source versions; file is a cached projection',
            'source_event':'knowledge-page:'+stable,'derived_from':value['citations'][:32],'history_origin':'managed_memory_projection'},
            '# '+name+'\n\n'+value['content']+'\n\n## 来源\n\n'+'\n'.join('- '+c for c in value['citations']))

    def own_projection():
        if projection.is_file() and parse_note(projection,memory.vault)['meta'].get('managed_by')!='engineering-memory':
            raise ValueError('Knowledge page destination contains a human-authored note')

    if refresh or not previous:
        candidate={};attempt=uuid4().hex;failure=None
        try:
            candidate=memory.reflect(query,scope,generator)
            if candidate.get('status')=='insufficient_evidence':raise ValueError('insufficient_evidence')
            if memory._generation()!=generation:raise ValueError('source_changed_during_generation')
            revision=uuid4().hex
            catalog={r[0]:r[1:] for r in json.loads(generation)}
            bindings=[{'source':n['permalink'],'path':n['path'],'version':catalog.get(n['path'])} for n in memory.notes() if n['permalink'] in candidate['citations']]
            value={'version':revision,'query':query,'content':candidate['answer'],'citations':candidate['citations'],
                   'generation':generation,'source_bindings':bindings,'validation':candidate.get('validation',{'coverage':'exact_evidence_only'})}
            own_projection()
            archive=memory._managed_path('engineering/knowledge-pages',stable+'/versions/'+revision+'.md')
            atomic_text(archive,render(value))
            # Archive is complete before the original DB pointer is switched. An orphan is never current.
            with db:
                db.execute('BEGIN IMMEDIATE')
                now_current=db.execute('SELECT version FROM memory_page_current WHERE name=?',(identity,)).fetchone()
                if (now_current[0] if now_current else None)!=(current['version'] if current else None):raise ValueError('page_changed_during_generation')
                if memory._generation()!=generation:raise ValueError('source_changed_before_publish')
                db.execute('INSERT INTO memory_page_versions VALUES(?,?,?,?)',(identity,revision,json.dumps(value,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
                db.execute('INSERT OR REPLACE INTO memory_pages VALUES(?,?,?,?,?,?)',(identity,query,scope or 'global',generation,value['content'],json.dumps(value['citations'])))
                db.execute('INSERT OR REPLACE INTO memory_page_current VALUES(?,?,NULL)',(identity,revision))
            previous=value;stale=False
        except Exception as exc:
            # Preserve the rejected candidate when reflect itself rejected it before returning.
            reflection=getattr(exc,'derivation_attempt',None)
            if reflection:
                row=db.execute('SELECT candidate FROM memory_derivation_attempts WHERE id=?',(reflection,)).fetchone()
                if row:candidate=json.loads(row[0])
            failure={'kind':str(exc) if str(exc) in {'source_changed_during_generation','source_changed_before_publish','page_changed_during_generation','insufficient_evidence'} else type(exc).__name__,
                     'validation':getattr(exc,'report',None),'reflection_attempt':reflection}
            with db:
                db.execute('INSERT INTO memory_page_attempts VALUES(?,?,?,?,?,?)',(attempt,identity,'rejected',json.dumps(candidate,ensure_ascii=False),json.dumps(failure,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
                db.execute('UPDATE memory_page_current SET invalid_reason=? WHERE name=?',(failure['kind'],identity))
            stale=True
        if failure:
            return {'name':name,'query':query,'scope':scope or 'global','status':'refresh_rejected','stale':True,'content':None,
                'citations':[],'previous_version':previous['version'] if previous else None,'attempt':attempt,'error':failure}
    if not stale and previous:
        # The original DB pointer is authoritative; repair a projection after interrupted publication.
        own_projection()
        expected=render(previous)
        if not projection.is_file() or projection.read_text(encoding='utf-8')!=expected:atomic_text(projection,expected)
    return {'name':name,'query':query,'scope':scope or 'global','status':'stale' if stale else 'current','stale':stale,
        'version':None if stale else previous['version'],'previous_version':previous['version'] if stale and previous else None,
        'content':None if stale else previous['content'],'citations':[] if stale else previous['citations'],
        'source_bindings':previous['source_bindings'] if previous else [],
        'validation':previous.get('validation') if previous else None,'source_freshness':'catalog_file_revisions_at_read_not_continuous_monitoring'}
