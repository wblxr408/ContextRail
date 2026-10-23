"""Stable source coordinates and atomic admission into the existing memory queue.

Adapts ai-memory's MIT insert_observation_keyed transaction pattern, pinned at
1bd866375b01031d63db54862a133376598d3701 (Fabio Akita). No key TTL or new store.
The caller owns the transaction, cursor and trusted source-path resolution.
"""

import json
import os
import time
from uuid import NAMESPACE_URL, uuid5

from .sanitization import require_safe_identity, sanitize_capture


SCHEMA = '''
CREATE TABLE IF NOT EXISTS source_events(
 id TEXT PRIMARY KEY, coordinates TEXT NOT NULL, content TEXT NOT NULL,
 job_id TEXT, status TEXT NOT NULL, source TEXT NOT NULL, source_offset INTEGER NOT NULL,
 first_seen REAL NOT NULL);
CREATE TABLE IF NOT EXISTS source_event_aliases(
 event_id TEXT NOT NULL, job_id TEXT NOT NULL UNIQUE,
 PRIMARY KEY(event_id,job_id));
CREATE TABLE IF NOT EXISTS source_event_conflicts(
 id INTEGER PRIMARY KEY, event_id TEXT NOT NULL, reason TEXT NOT NULL,
 changed_fields TEXT NOT NULL, incoming TEXT NOT NULL, source TEXT NOT NULL,
 source_offset INTEGER NOT NULL, first_seen REAL NOT NULL);
CREATE INDEX IF NOT EXISTS source_event_conflicts_event ON source_event_conflicts(event_id);
CREATE INDEX IF NOT EXISTS jobs_source_event_lookup ON jobs(source);
'''


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def workspace_key(workspace):
    value = str(workspace or '').replace('\\', '/').rstrip('/')
    # This installation is Windows. Preserve case on other hosts rather than
    # treating two case-sensitive workspaces as the same project.
    return value.casefold() if os.name == 'nt' else value


def source_identity(namespace, workspace, session, marker, source, offset, generation=None, *, part=None, imported=False):
    for value in (namespace, workspace, session, marker, source):
        require_safe_identity(value)
        if len(value) > 4096:
            raise ValueError('Source identity field exceeds supported size')
    if type(offset) is not int or offset < 0:
        raise ValueError('Source position must be a nonnegative integer')
    coordinates = {'namespace': namespace, 'workspace': workspace_key(workspace)}
    if imported:
        coordinates.update(source=source, revision=marker, part=part)
        basis = 'explicit_source_revision'
    elif session and marker and workspace:
        coordinates.update(session=session, event=marker)
        basis = 'native_event'
    else:
        coordinates.update(source=source, generation=generation or {}, offset=offset,
                           session=session, event=marker)
        basis = 'source_position'
    identity = 'source-event-v1:' + uuid5(NAMESPACE_URL, canonical(coordinates)).hex
    return {'id': identity, 'coordinates': coordinates, 'basis': basis}


def semantic_content(payload, workspace):
    # Receipt time, transport path/offset, _capture and _event do not turn a
    # replay into a new event. Source timestamps remain on the original payload.
    result = {key: payload.get(key, '') for key in ('user', 'final', 'kind')}
    result['workspace'] = workspace_key(workspace)
    return canonical(result)


def record_conflict(db, event, incoming, source, offset, reason, previous=None):
    old = json.loads(previous) if previous else {}
    new = json.loads(incoming)
    fields = sorted(key for key in old.keys() | new.keys() if old.get(key) != new.get(key))
    # Replaying the same conflicting delivery yields the same diagnostic.
    found = db.execute('SELECT id FROM source_event_conflicts WHERE event_id=? AND reason=? AND incoming=?',
                       (event['id'], reason, incoming)).fetchone()
    if not found:
        db.execute('INSERT INTO source_event_conflicts(event_id,reason,changed_fields,incoming,source,source_offset,first_seen) VALUES(?,?,?,?,?,?,?)',
                   (event['id'], reason, canonical(fields), incoming, source, offset, time.time()))
    return {'outcome': 'identity_conflict', 'event_id': event['id'], 'reason': reason}


def _legacy_candidates(db, payload, source, legacy_ids):
    rows = {r['id']: r for r in db.execute('SELECT * FROM jobs WHERE source=?', (source,))}
    for old_id in legacy_ids:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (old_id,)).fetchone()
        if row:
            rows[row['id']] = row
    selected = []
    for row in rows.values():
        try:
            old = json.loads(row['payload'])
            if old.get('kind') != payload.get('kind') or old.get('marker', '') != payload.get('marker', ''):
                continue
            if not payload.get('marker') and (row['source'] != source or old.get('offset') != payload.get('offset')):
                continue
            # Imported marker is document-wide, unlike a native completed turn.
            if payload.get('kind') in {'document', 'git'} and old.get('offset') != payload.get('offset'):
                continue
            if payload.get('kind') in {'document', 'git'} and row['source'] != source:
                continue
            selected.append((row, old))
        except (ValueError, TypeError, AttributeError):
            continue
    return selected


def admit_event(db, event, payload, source, *, legacy_ids=(), state='queued', available=None,
                container=False, normalize_workspace=workspace_key):
    if not db.in_transaction:
        raise ValueError('Source event admission requires the caller transaction')
    processed = sanitize_capture(payload, 'memory')
    payload = processed.value
    payload['_capture'] = processed.provenance()
    workspace = event['coordinates']['workspace']
    content = semantic_content(payload, workspace)
    offset = payload.get('offset', 0)
    existing = db.execute('SELECT * FROM source_events WHERE id=?', (event['id'],)).fetchone()
    if existing:
        if existing['status'] == 'ambiguous':
            return record_conflict(db, event, content, source, offset, 'legacy_ambiguous', existing['content'])
        if existing['content'] != content:
            return record_conflict(db, event, content, source, offset, 'content_changed', existing['content'])
        return {'outcome': 'duplicate', 'event_id': event['id'], 'job_id': existing['job_id']}

    candidates = [] if container else _legacy_candidates(db, payload, source, legacy_ids)
    compatible = []
    for row, old in candidates:
        if workspace_key(normalize_workspace(old.get('cwd', ''))) != workspace:
            continue
        bound = db.execute('SELECT event_id FROM source_event_aliases WHERE job_id=?', (row['id'],)).fetchone()
        if bound and bound[0] != event['id']:
            continue
        compatible.append((row, sanitize_capture(old, 'memory').value))
    if len(compatible) > 1:
        db.execute('INSERT INTO source_events VALUES(?,?,?,?,?,?,?,?)',
                   (event['id'], canonical(event), content, None, 'ambiguous', source, offset, time.time()))
        for row, _ in compatible:
            db.execute('INSERT INTO source_event_aliases VALUES(?,?)', (event['id'], row['id']))
        return record_conflict(db, event, content, source, offset, 'legacy_ambiguous')
    job_id = compatible[0][0]['id'] if compatible else (None if container else event['id'])
    previous = semantic_content(compatible[0][1], workspace) if compatible else content
    db.execute('INSERT INTO source_events VALUES(?,?,?,?,?,?,?,?)',
               (event['id'], canonical(event), previous, job_id, 'container' if container else 'bound', source, offset, time.time()))
    if job_id:
        db.execute('INSERT INTO source_event_aliases VALUES(?,?)', (event['id'], job_id))
    if previous != content:
        return record_conflict(db, event, content, source, offset, 'content_changed', previous)
    if compatible:
        return {'outcome': 'legacy_mapped', 'event_id': event['id'], 'job_id': job_id}
    if not container:
        payload['_event'] = event
        db.execute('INSERT INTO jobs(id,source,payload,state,created,available) VALUES(?,?,?,?,?,?)',
                   (job_id, source, canonical(payload), state, time.time(), time.time() if available is None else available))
    return {'outcome': 'inserted', 'event_id': event['id'], 'job_id': job_id}


def identity_status(db, *, event_id=None, offset=0, limit=20):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Event page requires nonnegative offset and limit 1..100')
    if event_id is not None:
        require_safe_identity(event_id)
    where, args = (' WHERE id=?', [event_id]) if event_id else ('', [])
    total = db.execute('SELECT count(*) FROM source_events' + where, args).fetchone()[0]
    entries = []
    for row in db.execute('SELECT * FROM source_events' + where + ' ORDER BY first_seen,id LIMIT ? OFFSET ?', [*args, limit, offset]):
        aliases = [r[0] for r in db.execute('SELECT job_id FROM source_event_aliases WHERE event_id=? ORDER BY job_id LIMIT 100', (row['id'],))]
        conflicts = db.execute('SELECT id,reason,changed_fields,source_offset FROM source_event_conflicts WHERE event_id=? ORDER BY id DESC LIMIT 10', (row['id'],)).fetchall()
        item = {'event': json.loads(row['coordinates']), 'status': row['status'], 'job_id': row['job_id'],
                'aliases': aliases, 'conflicts': [{'id': c[0], 'reason': c[1], 'fields': json.loads(c[2]), 'source_offset': c[3]} for c in conflicts],
                'conflict_count': db.execute('SELECT count(*) FROM source_event_conflicts WHERE event_id=?',(row['id'],)).fetchone()[0]}
        item['aliases_total'] = db.execute('SELECT count(*) FROM source_event_aliases WHERE event_id=?',(row['id'],)).fetchone()[0]
        item['aliases_truncated'] = item['aliases_total'] > len(aliases)
        item['conflicts_truncated'] = item['conflict_count'] > len(conflicts)
        if len(canonical(entries + [item]).encode()) > 60000:
            break
        entries.append(item)
    diagnostics = []
    clause, values = (' WHERE event_id=?', [event_id]) if event_id else ('', [])
    for conflict in db.execute('SELECT id,event_id,reason,changed_fields,source_offset FROM source_event_conflicts' + clause + ' ORDER BY id DESC LIMIT 20', values):
        diagnostics.append({'id':conflict[0], 'event_id':conflict[1], 'reason':conflict[2],
                            'fields':json.loads(conflict[3]), 'source_offset':conflict[4]})
    return {'schema': 'contextrail.source-events.v1', 'entries': entries, 'total': total, 'offset': offset,
            'next_offset': offset + len(entries) if offset + len(entries) < total else None,
            'conflicts_total': db.execute('SELECT count(*) FROM source_event_conflicts').fetchone()[0],
            'recent_conflicts':diagnostics,
            'range': 'processed_source_content; metadata_only', 'fallback_limit': 'source_position_not_global_delivery_identity'}
