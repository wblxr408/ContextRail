"""Grounded memory features embedded in the existing listener and Basic Memory bridge.

Markdown in the configured vault is authoritative. SQLite tables below are rebuildable
retrieval/lifecycle caches inside the listener's existing state database, not another vault.
"""
from __future__ import annotations
from collections import Counter, OrderedDict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import sys
import unicodedata
from uuid import uuid4, uuid5, NAMESPACE_URL

import yaml
from canonical import canonical_path
from capture_sanitizer import sanitize_capture, require_safe_identity


class ConflictError(ValueError):
    pass


def normalize(text):
    text = unicodedata.normalize('NFKC', str(text)).lower()
    # Domain synonyms are query-independent; dense models handle broader paraphrases.
    for group in [('重试', '重放', '重复传输'),
                  ('去重', '幂等'), ('回收', '清理'), ('约定', '规则', '政策'),
                  ('后台', '监听器'), ('冒烟', '启动一下'), ('检查点', 'checkpoint')]:
        for word in group[1:]:
            text = text.replace(word, group[0])
    return text


def tokens(text):
    text = normalize(text)
    result = re.findall(r'[a-z0-9_][a-z0-9_.-]*', text)
    for run in re.findall(r'[\u3400-\u9fff]+', text):
        result.extend(run[i:i+2] for i in range(len(run)-1))
    return [t for t in result if t not in {'我们', '之前', '怎样', '如何', '什么', '现在', '哪些', '一下', '是否', '需要', '可以', 'must', 'the', 'and', 'or'}]


def scope_key(scope):
    value = str(scope or 'global').replace('\\', '/').rstrip('/').casefold()
    return value or 'global'

def concept_key(text):
    # Identity normalization must not equate different business operations such as debit and refund.
    return unicodedata.normalize('NFKC',str(text)).strip().casefold()


def scope_matches(note_scope, requested):
    if not requested:
        return True
    note, request = scope_key(note_scope), scope_key(requested)
    return note == 'global' or note == request or request.startswith(note + '/')


def date_value(value):
    if not value:
        return ''
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError as exc:
        raise ValueError('Invalid ISO date: ' + str(value)) from exc


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def markdown(meta, body):
    return '---\n' + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False) + '---\n\n' + body


def parse_note(path, vault):
    raw = path.read_text(encoding='utf-8-sig')
    meta, body = {}, raw
    if raw.startswith('---\n'):
        parts = raw.split('\n---', 1)
        if len(parts) == 2:
            loaded = yaml.safe_load(parts[0][4:])
            if isinstance(loaded, dict):
                meta = loaded
            body = parts[1].lstrip('\r\n')
    name = str(path.relative_to(vault)).replace('\\', '/')
    permalink = str(meta.get('permalink') or 'main/' + name.removesuffix('.md')).strip('/')
    stamp = meta.get('date') or meta.get('updated') or meta.get('started')
    try:
        stamp = date_value(stamp)
    except ValueError:
        stamp = ''
    if not stamp:
        explicit = re.search(r'\d{4}-\d{2}-\d{2}', str(meta.get('title', '')))
        stamp = date_value(explicit.group()) if explicit else ''
    declared=next(((field,meta[field]) for field in ('date','updated','started') if meta.get(field)),None)
    time_info={'occurred_at':None,'state':'missing','source_date':None,'basis':'not_declared'}
    if declared:
        field,value=declared;raw_time=str(value)
        time_info.update(source_date=raw_time,basis=field)
        try:
            parsed=datetime.fromisoformat(raw_time.replace('Z','+00:00'))
            if len(raw_time)==10:time_info['state']='date_only'
            elif parsed.tzinfo is None:time_info['state']='ambiguous_timezone'
            else:time_info.update(occurred_at=parsed.astimezone(timezone.utc).isoformat(),state='provided')
        except (ValueError,OverflowError):time_info['state']='invalid'
    elif stamp:time_info.update(state='title_inferred_date',source_date=stamp,basis='title_not_event_time')
    return {'title': str(meta.get('title') or path.stem), 'permalink': permalink,
            'path': name, 'content': body, 'meta': meta, 'date': stamp,
            'scope': meta.get('cwd') or meta.get('repository') or meta.get('scope') or 'global',
            'status': str(meta.get('status') or 'unknown'),'time_provenance':time_info,
            'links': re.findall(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', body)}


class LocalModels:
    def __init__(self, cache, embedding='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
                 reranker='BAAI/bge-reranker-base'):
        self.cache, self.embedding_name, self.reranker_name = str(cache), embedding, reranker
        self._embedding = self._reranker = None
        self._embedding_type = self._reranker_type = None
        self.reranker_path = None
        self.embedding_path = None
        self.inference_lock=threading.RLock()

    def load_dependencies(self):
        # Native numeric DLLs must be loaded on the owning main thread before
        # the stdio adapter accepts inference work on its executor thread.
        if self._embedding_type is None:
            os.environ.setdefault('HF_HOME',str(Path(self.cache)/'hf'))
            from fastembed import TextEmbedding
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            self._embedding_type, self._reranker_type = TextEmbedding, TextCrossEncoder

    def embed(self, texts):
        with self.inference_lock:
            return self._embed(texts)

    def _embed(self, texts):
        if self._embedding is None:
            self.load_dependencies()
            if self.embedding_path:
                from compact_reranker import CompactEmbedding
                self._embedding=CompactEmbedding(self.embedding_name,self.embedding_path,self.cache)
            else:
                self._embedding = self._embedding_type(self.embedding_name, cache_dir=self.cache,
                                            threads=2, providers=['CPUExecutionProvider'],enable_cpu_mem_arena=False)
        # Bound activation memory while preserving every passage and its embedding.
        return [list(map(float, vector)) for vector in self._embedding.embed(texts, batch_size=2)]

    def rerank(self, query, texts):
        with self.inference_lock:
            return self._rank(query,texts)

    def _rank(self,query,texts):
        if self._reranker is None:
            self.load_dependencies()
            if self.reranker_path:
                from compact_reranker import CompactReranker
                self._reranker=CompactReranker(self.reranker_path)
            else:
                self._reranker = self._reranker_type(self.reranker_name, cache_dir=self.cache,
                                             threads=2, providers=['CPUExecutionProvider'],enable_cpu_mem_arena=False)
        return list(map(float, self._reranker.rerank(query, texts,batch_size=2)))


class MemoryRuntime:
    def __init__(self, vault, state, use_models=True, cache=None, model_backend=None):
        self.vault = canonical_path(vault)
        self.state = Path(state).absolute()
        if str(state) != ':memory:':
            self.state.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(':memory:' if str(state)==':memory:' else self.state, timeout=30, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS memory_events(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memory_vectors(path TEXT, version TEXT, model TEXT, vector TEXT,
          PRIMARY KEY(path, version, model));
        CREATE TABLE IF NOT EXISTS memory_pages(name TEXT PRIMARY KEY, query TEXT, scope TEXT,
          generation TEXT, content TEXT, citations TEXT);
        ''')
        self.db.commit()
        self.models = model_backend or (LocalModels(cache or self.state.parent / 'models') if use_models else None)
        self._passages={}
        self._note_cache=OrderedDict()
        self.note_cache_limit=16*1024*1024
        self.note_cache_bytes=0

    def close(self):
        self._note_cache.clear();self.note_cache_bytes=0
        self.db.close()

    def notes(self):
        found = []
        seen=set()
        for path in self.vault.rglob('*.md'):
            relative = path.relative_to(self.vault)
            if '_templates' in relative.parts or any(p.startswith('.') for p in relative.parts):
                continue
            if 'knowledge-pages' in relative.parts or 'observations' in relative.parts:
                continue  # Materialized projections must not feed their own evidence loop.
            try:
                # Canonical file containment guards junction/symlink escape without directory handles.
                if not canonical_path(path).is_relative_to(self.vault):
                    continue
                seen.add(path)
                stamp=path.stat();version=(stamp.st_mtime_ns,stamp.st_size,stamp.st_ctime_ns)
                cached=self._note_cache.get(path)
                if cached and cached[0]==version:
                    self._note_cache.move_to_end(path);found.append(cached[1]);continue
                if cached:
                    self.note_cache_bytes-=cached[2];del self._note_cache[path]
                note=parse_note(path,self.vault)
                # Do not cache a torn version if a source changed during its read.
                after=path.stat()
                if version==(after.st_mtime_ns,after.st_size,after.st_ctime_ns):
                    def weight(value):
                        size=sys.getsizeof(value)
                        if isinstance(value,dict):size+=sum(weight(k)+weight(v) for k,v in value.items())
                        elif isinstance(value,(list,tuple)):size+=sum(weight(v) for v in value)
                        return size
                    size=weight(note)+weight(version)+weight(str(path))+256
                    while self._note_cache and self.note_cache_bytes+size>self.note_cache_limit:
                        _,old=self._note_cache.popitem(last=False);self.note_cache_bytes-=old[2]
                    if size<=self.note_cache_limit:
                        self._note_cache[path]=(version,note,size);self.note_cache_bytes+=size
                found.append(note)
            except (OSError, yaml.YAMLError):
                continue
        for path in set(self._note_cache)-seen:
            self.note_cache_bytes-=self._note_cache.pop(path)[2]
        return found

    def _selected(self, scope, after, before, include_history, metadata):
        result = []
        after, before = date_value(after), date_value(before)
        notes = self.notes()
        fact_events={n['meta'].get('memory_event') for n in notes if n['meta'].get('memory_event')}
        current = {h['event_id']: [] for row in self.observations(scope) for h in row['current_evidence']} if not include_history else {}
        if not include_history:
            for row in self.observations(scope):
                for h in row['current_evidence']:
                    current[h['event_id']].append(h)
        superseded = {target for note in notes for target in re.findall(r'-\s*(?:supersedes|replaces)\s+\[\[([^\]|]+)', note['content'])}
        for note in notes:
            if not include_history:
                from checkpoint_lifecycle import is_history
                if is_history(note['meta']):continue
            if note['meta'].get('automatic_capture') and note['meta'].get('source_event') in fact_events:
                continue  # One event's facts carry its source identity; avoid duplicate derived capture results.
            if not scope_matches(note['scope'], scope):
                continue
            if not include_history and note['status'] in {'superseded', 'revoked', 'rejected', 'obsolete'}:
                continue
            if not include_history and (note['title'] in superseded or note['permalink'] in superseded):
                continue
            if not include_history and note['meta'].get('memory_event'):
                active = current.get(note['meta']['memory_event'], [])
                if not active:
                    continue
                note = dict(note,content='\n\n'.join(h['value']+'\n\n> '+h['quote'] for h in active))
            if after and (not note['date'] or note['date'] < after):
                continue
            if before and (not note['date'] or note['date'] > before):
                continue
            if metadata and any(note['meta'].get(k) != v for k, v in metadata.items()):
                continue
            result.append(note)
        return result

    def _dense(self, query, notes):
        q = self.models.embed([query])[0]
        ranked = []
        for note in notes:
            source = self.vault / note['path']
            stamp = source.stat()
            version = json.dumps([stamp.st_mtime_ns,stamp.st_size,note['content'] if note['meta'].get('memory_event') else None],ensure_ascii=False)
            model = getattr(self.models, 'embedding_name', type(self.models).__name__)
            row = self.db.execute('SELECT vector FROM memory_vectors WHERE path=? AND version=? AND model=?',
                                  (note['path'], version, model)).fetchone()
            if row:
                vectors = json.loads(row[0])
            else:
                # Paragraph windows keep deep evidence within the model's finite token window.
                chunks = [note['title'] + '\n' + note['content'][i:i+350]
                          for i in range(0, max(1, len(note['content'])), 280)]
                vectors = self.models.embed(chunks)
                with self.db:
                    self.db.execute('DELETE FROM memory_vectors WHERE path=?', (note['path'],))
                    self.db.execute('INSERT INTO memory_vectors VALUES(?,?,?,?)',
                                    (note['path'], version, model, json.dumps(vectors)))
            def cosine(v):
                denom = math.sqrt(sum(x*x for x in q) * sum(x*x for x in v))
                return sum(a*b for a,b in zip(q,v)) / denom if denom else 0.0
            scores=[cosine(v) for v in vectors]
            best=max(range(len(scores)),key=scores.__getitem__) if scores else 0
            score=scores[best] if scores else 0
            self._passages[note['permalink']]=best*280
            if score >= 0.45:
                ranked.append((score, note['permalink']))
        return sorted(ranked, reverse=True)

    def recall(self, query, *, scope=None, after=None, before=None, include_history=False,
               mode='hybrid', limit=5, page=1, metadata=None, graph_hops=2,rerank_limit=20,include_revision=False):
        if type(include_revision) is not bool:raise ValueError('include_revision must be boolean')
        if not isinstance(query, str) or not query.strip():
            raise ValueError('A nonempty query is required')
        if not isinstance(rerank_limit,int) or isinstance(rerank_limit,bool) or not 1<=rerank_limit<=100:
            raise ValueError('Rerank limit must be an integer from 1 to 100')
        if mode not in {'text', 'hybrid', 'vector'} or not 1 <= limit <= 100 or page < 1:
            raise ValueError('Invalid search mode or pagination')
        if type(graph_hops) is not int or not 0<=graph_hops<=3:raise ValueError('Graph hops must be 0..3')
        with self.lock:
            self._passages={}
            notes = self._selected(scope, after, before, include_history, metadata)
            # Deduplicate BEFORE ranking/pagination and prefer current/newest source versions.
            by_id = {}
            for note in sorted(notes, key=lambda n: (n['date'], n['path'])):
                by_id[note['permalink']] = note
            notes = list(by_id.values())
            counters = {n['permalink']: Counter(tokens(n['title'] * 3 + '\n' + n['content'])) for n in notes}
            df = Counter(t for c in counters.values() for t in c)
            lengths = {pid: sum(c.values()) for pid, c in counters.items()}
            avg = sum(lengths.values()) / max(1, len(lengths)) or 1
            terms = set(tokens(query))
            lexical = []
            boolean = bool(re.search(r'\b(AND|OR|NOT)\b|"[^\"]+"', query))
            expressions = re.findall(r'"([^"]+)"|\b(AND|OR|NOT)\b|([^\s"]+)', query)
            def literal_match(body):
                if not boolean:
                    return True
                # Explicit literal Boolean expressions use OR groups of AND/NOT terms.
                groups, current, negate = [], [], False
                for quoted, op, bare in expressions:
                    if op == 'OR':
                        groups.append(all(current)); current = []; negate = False
                    elif op == 'NOT':
                        negate = True
                    elif op != 'AND':
                        hit = (quoted or bare).casefold() in body.casefold()
                        current.append(not hit if negate else hit); negate = False
                return any(groups + [all(current)])
            for note in notes:
                pid, body = note['permalink'], note['title'] + '\n' + note['content']
                if not literal_match(body):
                    continue
                counter = counters[pid]
                score = 0.0
                for term in terms:
                    count = counter.get(term, 0)
                    if count:
                        idf = math.log(1 + (len(notes)-df[term]+0.5)/(df[term]+0.5))
                        score += idf * count * 2.2 / (count + 1.2*(0.25+0.75*lengths[pid]/avg))
                if score or (boolean and literal_match(body)):
                    lexical.append((score or 0.001, pid))
            lexical.sort(reverse=True)
            dense = self._dense(query, notes) if self.models and mode != 'text' and not boolean else []
            if mode == 'vector' and not self.models:
                raise ValueError('Vector retrieval requires the configured multilingual model')
            ranks, reasons = {}, {}
            for name, listing in [('keyword', lexical if mode != 'vector' else []), ('semantic', dense)]:
                for rank, (_, pid) in enumerate(listing, 1):
                    ranks[pid] = ranks.get(pid, 0) + 1/(60+rank)
                    reasons.setdefault(pid, {})[name] = rank
            # Graph expansion is constrained to the already scope/time/status-filtered corpus.
            names = {n['title']: n['permalink'] for n in notes}
            adjacency={n['permalink']:set() for n in notes}
            for note in notes:
                pid=note['permalink']
                for link in note['links']:
                    other=names.get(link) or (link if link in by_id else None)
                    if other:
                        adjacency[pid].add(other);adjacency[other].add(pid)
            frontier=list(sorted(ranks,key=ranks.get,reverse=True)[:3]);visited=set(frontier)
            for hop in range(0 if boolean else graph_hops):
                next_frontier=[]
                for pid in frontier:
                    for linked in sorted(adjacency[pid]):
                        if linked and linked not in visited and len(visited)<60:
                            ranks[linked]=ranks.get(linked,0)+1/((60+len(next_frontier)+1)*(hop+1))
                            reasons.setdefault(linked,{})['graph']=pid
                            visited.add(linked);next_frontier.append(linked)
                frontier=next_frontier
            order = sorted(ranks, key=lambda p: (ranks[p], by_id[p]['date'], p), reverse=True)
            if self.models and mode != 'text' and order:
                candidate_count=rerank_limit
                leading = order[:candidate_count]
                texts = [by_id[p]['title'] + '\n' + self._excerpt(by_id[p], terms) for p in leading]
                reranked = self.models.rerank(query, texts)
                if len(reranked) != len(leading):
                    raise ValueError('Reranker returned incomplete results')
                order = [p for _,p in sorted(zip(reranked, leading), key=lambda x: (x[0], ranks[x[1]]), reverse=True)] + order[candidate_count:]
            page_ids = order[(page-1)*limit:page*limit]
            results = [{'title': by_id[p]['title'], 'permalink': p, 'path': by_id[p]['path'],
                        'excerpt': self._excerpt(by_id[p], terms), 'date': by_id[p]['date'],
                        'status': by_id[p]['status'], 'scope': by_id[p]['scope'],
                        'score': ranks[p], 'retrieval': reasons[p],
                        'time_provenance':by_id[p].get('time_provenance'),
                        'source_session':by_id[p]['meta'].get('session_id'),
                        'source_event':by_id[p]['meta'].get('source_event') or by_id[p]['meta'].get('memory_event')} for p in page_ids]
            return {'results': results, 'total': len(order), 'page': page, 'mode': mode,
                    'model': getattr(self.models, 'embedding_name', None),
                    **({'catalog_revision':self._query_revision_value()} if include_revision else {}),
                    'evidence_boundary': 'Retrieved history; verify applicability against current instructions.'}

    def _excerpt(self,note, terms):
        if note['permalink'] in self._passages:
            start=max(0,self._passages[note['permalink']]-150)
            return note['content'][start:start+1200]
        parts = [note['content'][i:i+1200] for i in range(0, max(1,len(note['content'])), 900)]
        return max(parts, key=lambda p: len(set(tokens(p)) & terms), default='')

    def _event_path(self, event_id):
        return self._managed_path('engineering/memory-facts',uuid5(NAMESPACE_URL,event_id).hex+'.md')

    def _managed_path(self,folder,name):
        path=self.vault/folder/name
        ancestor=path
        while not (ancestor.exists() or ancestor.is_symlink() or getattr(ancestor,'is_junction',lambda:False)()):
            ancestor=ancestor.parent
        if not canonical_path(ancestor).is_relative_to(self.vault):raise ValueError('Managed memory path escapes the configured vault')
        return path

    def retain(self, event_id, claims, sources, occurred_at=None):
        if not isinstance(event_id, str) or not event_id.strip() or not isinstance(claims, list) or not claims:
            raise ValueError('Event identity and nonempty claims are required')
        require_safe_identity(event_id)
        for claim in claims:
            if not isinstance(claim,dict):raise ValueError('Invalid claim shape')
            require_safe_identity(claim.get('scope','global'))
            require_safe_identity(claim.get('source',''))
        processed=sanitize_capture({'claims':claims,'sources':sources},'memory')
        claims=processed.value['claims'];sources=processed.value['sources']
        occurred = date_value(occurred_at or datetime.now(timezone.utc).isoformat())
        for claim in claims:
            for field in ('subject', 'predicate', 'value', 'quote', 'source'):
                if not isinstance(claim.get(field), str) or not claim[field].strip():
                    raise ValueError('Missing claim field: ' + field)
            if claim['source'] not in {'user', 'final'} or claim['quote'] not in sources.get(claim['source'], ''):
                raise ValueError('Claim evidence is not present in the specified original source')
        if len({scope_key(c.get('scope')) for c in claims}) != 1:
            raise ValueError('A source event must use one trusted project scope')
        payload = {'claims': claims, 'sources': sources, 'occurred_at': occurred, '_capture':processed.provenance()}
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self.lock:
            # Reject projection directories redirected outside the authoritative vault.
            for folder in ('engineering','engineering/memory-facts','engineering/observations'):
                target=self.vault/folder
                if target.exists() and not canonical_path(target).is_relative_to(self.vault):
                    raise ValueError('Memory source destination escapes the configured vault')
            self._reconcile_events()
            # Identity covers the supplied payload; default timestamp is reconciled on exact retry.
            row = self.db.execute('SELECT payload FROM memory_events WHERE id=?', (event_id,)).fetchone()
            if row and occurred_at is None:
                payload['occurred_at'] = json.loads(row[0])['occurred_at']
                serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if row and row[0] != serialized:
                raise ConflictError('Event identity already binds a different payload')
            target=self._event_path(event_id)
            if target.is_file() and parse_note(target,self.vault)['meta'].get('memory_event')!=event_id:
                raise ConflictError('Managed event destination contains a human-authored note')
            raw = markdown({'title': claims[0]['subject'] + '：' + claims[0]['predicate'],
                            'type': 'memory_fact', 'status': 'accepted' if all(c['source']=='user' and c.get('authority',True) for c in claims) else 'experimental',
                            'date': payload['occurred_at'], 'scope': claims[0].get('scope', 'global'),
                            'permalink': 'main/engineering/memory-facts/' + self._event_path(event_id).stem,
                            'memory_event': event_id, 'memory_payload': payload,
                            'source_event':event_id,
                            'derived_from':list(dict.fromkeys(ref for c in claims for ref in c.get('derived_from',[]) if isinstance(ref,str)))[:32],
                            'history_origin':'retained_fact'},
                           '\n\n'.join(c['value'] + '\n\n> ' + c['quote'] for c in claims))
            # Publish authoritative source before caching; retry rebuilds a missing cache.
            atomic_text(self._event_path(event_id), raw)
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO memory_events VALUES(?,?)', (event_id, serialized))
            self._publish_observations()
            return {'status': 'retained', 'event_id': event_id, 'duplicate': bool(row),
                    'source': str(self._event_path(event_id))}

    def _reconcile_events(self):
        present = {}
        folder=self._managed_path('engineering/memory-facts','.boundary').parent
        for path in folder.glob('*.md'):
            try:
                if not canonical_path(path).is_relative_to(self.vault):continue
                note = parse_note(path, self.vault)
                event = note['meta'].get('memory_event')
                payload = note['meta'].get('memory_payload')
                if event and isinstance(payload, dict):
                    claims=payload.get('claims');sources=payload.get('sources')
                    if not isinstance(claims,list) or not claims or not isinstance(sources,dict):continue
                    if any(not isinstance(c,dict) or any(not isinstance(c.get(k),str) or not c[k].strip() for k in ('subject','predicate','value','quote','source')) or c['source'] not in {'user','final'} or not isinstance(sources.get(c['source']),str) or c['quote'] not in sources[c['source']] for c in claims):continue
                    if not isinstance(payload.get('occurred_at'),str) or not date_value(payload['occurred_at']):continue
                    present[event] = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            except (OSError, yaml.YAMLError,TypeError,ValueError):
                continue
        with self.db:
            for row in self.db.execute('SELECT id FROM memory_events').fetchall():
                if row[0] not in present:
                    self.db.execute('DELETE FROM memory_events WHERE id=?', (row[0],))
            for event, payload in present.items():
                self.db.execute('INSERT INTO memory_events VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (event,payload))

    def observations(self, scope=None):
        with self.lock:
            self._reconcile_events()
            groups = {}
            for row in self.db.execute('SELECT id,payload FROM memory_events'):
                payload = json.loads(row['payload'])
                for claim in payload['claims']:
                    claim_scope = claim.get('scope', 'global')
                    if not scope_matches(claim_scope, scope):
                        continue
                    key = (scope_key(claim_scope), concept_key(claim['subject']), concept_key(claim['predicate']))
                    group = groups.setdefault(key, {'subject': claim['subject'], 'predicate': claim['predicate'], 'scope': claim_scope, 'history': []})
                    group['history'].append({'value': claim['value'], 'quote': claim['quote'], 'role': claim['source'],
                                             'authority': claim.get('authority',claim['source']=='user'),
                                             'event_id': row['id'], 'occurred_at': payload['occurred_at'],
                                             'source': 'main/engineering/memory-facts/' + self._event_path(row['id']).stem})
            result = []
            for group in groups.values():
                group['history'].sort(key=lambda h: (h['occurred_at'], h['event_id']))
                # Explicit user decisions prevail over unverified assistant-only claims.
                authoritative = [h for h in group['history'] if h['role']=='user' and h['authority']] or group['history']
                latest = authoritative[-1]
                group['current'] = latest['value']
                group['current_evidence'] = [h for h in authoritative if h['value']==latest['value']]
                group['proof_count'] = len({h['event_id'] for h in group['history']})
                group['status'] = 'accepted' if latest['role']=='user' and latest['authority'] else 'experimental'
                result.append(group)
            return result

    def _publish_observations(self):
        keep=set()
        for row in self.observations():
            key = json.dumps([scope_key(row['scope']), concept_key(row['subject']), concept_key(row['predicate'])], ensure_ascii=False)
            name = uuid5(NAMESPACE_URL, key).hex
            keep.add(name+'.md')
            body = f'# {row["subject"]}：{row["predicate"]}\n\n当前：{row["current"]}\n\n## 历史和出处\n\n'
            body += '\n'.join(f'- {h["occurred_at"]}：{h["value"]} [{h["role"]}] ({h["source"]})\n  > {h["quote"]}' for h in row['history'])
            target=self._managed_path('engineering/observations',name+'.md')
            if target.is_file() and parse_note(target,self.vault)['meta'].get('managed_by')!='engineering-memory':raise ConflictError('Observation destination contains a human-authored note')
            atomic_text(target, markdown({'title':row['subject']+'：'+row['predicate'], 'type':'observation', 'scope':row['scope'], 'status':row['status'], 'proof_count':row['proof_count'], 'managed_by':'engineering-memory',
                'source_event':'observation:'+name,'derived_from':list(dict.fromkeys(h['event_id'] for h in row['history']))[:32],
                'history_origin':'managed_memory_projection'}, body))
        for path in self._managed_path('engineering/observations','.boundary').parent.glob('*.md'):
            if path.name not in keep and canonical_path(path).is_relative_to(self.vault) and parse_note(path,self.vault)['meta'].get('managed_by')=='engineering-memory':path.unlink()

    def forget_event(self, event_id):
        with self.lock:
            self._event_path(event_id).unlink(missing_ok=True)
            with self.db:
                self.db.execute('DELETE FROM memory_events WHERE id=?', (event_id,))
            # Remove all owned projections and regenerate from remaining authoritative events.
            for path in (self.vault/'engineering/observations').glob('*.md'):
                if parse_note(path,self.vault)['meta'].get('managed_by')=='engineering-memory':
                    path.unlink()
            self._publish_observations()

    def reflect(self, query, scope=None, generator=None):
        with self.lock:
            self.db.execute('''CREATE TABLE IF NOT EXISTS memory_derivation_attempts(
                id TEXT PRIMARY KEY,query TEXT,scope TEXT,status TEXT,candidate TEXT,report TEXT,created TEXT)''')
            candidate={};attempt=uuid4().hex;persisted_query=sanitize_capture(query,'memory').value
            def capture(evidence):
                response=generator(evidence)
                candidate['response']=sanitize_capture(response,'memory').value
                return candidate['response']
            try:
                result=self._reflect_grounded(query,scope,capture if generator else None)
            except Exception as exc:
                report=getattr(exc,'report',{'accepted':False,'problems':[{'kind':type(exc).__name__}]})
                with self.db:self.db.execute('INSERT INTO memory_derivation_attempts VALUES(?,?,?,?,?,?,?)',
                    (attempt,persisted_query,scope or 'global','rejected',json.dumps(candidate,ensure_ascii=False),json.dumps(report,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
                exc.derivation_attempt=attempt
                raise
            with self.db:self.db.execute('INSERT INTO memory_derivation_attempts VALUES(?,?,?,?,?,?,?)',
                (attempt,persisted_query,scope or 'global','accepted',json.dumps(candidate,ensure_ascii=False),json.dumps(result.get('validation',{}),ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
            return {**result,'derivation_attempt':attempt}

    def _reflect_grounded(self, query, scope=None, generator=None):
        from fidelity import residue,validate
        retrieved = self.recall(query, scope=scope, limit=8)
        relevant = []
        query_terms = set(tokens(query))
        for row in self.observations(scope):
            if set(tokens(row['subject']+' '+row['predicate']+' '+row['current'])) & query_terms:
                relevant.append(row)
        citations = list(dict.fromkeys([h['source'] for row in relevant for h in row['current_evidence']] + [r['permalink'] for r in retrieved['results']]))
        if not citations:
            return {'status':'insufficient_evidence', 'answer':'没有足够的相关来源支持回答。', 'citations':[]}
        evidence = {'observations':relevant, 'sources':retrieved['results'], 'allowed_citations':citations, 'query':query}
        selected=[]
        for note in self.notes():
            if note['permalink'] in citations:
                selected.append({'source':note['permalink'],'text':sanitize_capture(note['content'],'memory').value})
        for row in relevant:
            for h in row['current_evidence']:
                selected.append({'source':h['source'],'text':h['quote']})
        evidence['residue']=residue(selected)
        evidence['residue']['source_basis']='processed_note_body_utf8_not_raw_markdown_file'
        if generator:
            response = generator(evidence)
            if response.get('status')=='insufficient_evidence':
                if response.get('citations') or response.get('support'):raise ValueError('Insufficient evidence response cannot claim supported citations')
                return {'status':'insufficient_evidence','answer':response.get('answer','没有足够证据。'),'citations':[]}
            if not isinstance(response.get('answer'), str) or not response.get('answer') or not response.get('citations'):
                raise ValueError('Reflection requires an answer with source citations')
            if any(c not in citations for c in response['citations']):
                raise ValueError('Reflection cites unavailable evidence')
            support = response.get('support',[])
            supported = set()
            for item in support:
                citation,quote = item.get('citation'),item.get('quote')
                source_text = '\n'.join(r['excerpt'] for r in retrieved['results'] if r['permalink']==citation)
                source_text += '\n'+'\n'.join(h['quote'] for row in relevant for h in row['current_evidence'] if h['source']==citation)
                source_text += '\n'+'\n'.join(s['text'] for s in selected if s['source']==citation)
                if citation not in response['citations'] or not isinstance(quote,str) or not quote.strip() or quote not in source_text:
                    raise ValueError('Reflection support is not an exact quote of cited available evidence')
                supported.add(citation)
            if supported != set(response['citations']):
                raise ValueError('Each reflection citation requires exact supporting evidence')
            cited=[s for s in selected if s['source'] in response['citations']]
            required=[r['current'] for r in relevant if any(h['role']=='user' and h.get('authority') for h in r['current_evidence'])]
            quality=validate(response['answer'],cited,required=required,require_coverage=False,claims=response.get('claims'))
            if not quality['accepted']:
                error=ValueError('Reflection rejected by literal fidelity gate');error.report=quality;raise error
            return {**response,'status':'synthesized','validation':quality}
        answer = '\n'.join(row['subject']+'：'+row['current'] for row in relevant)
        if not answer:
            answer = '\n\n'.join(r['excerpt'] for r in retrieved['results'][:3])
        return {'status':'evidence_only', 'answer':answer, 'citations':citations, 'evidence':evidence}

    def _generation(self):
        self._reconcile_events()
        # Revision tokens are cache invalidation metadata, never functional acceptance hashes.
        sources=[]
        for note in self.notes():
            stamp=(self.vault/note['path']).stat()
            sources.append((note['path'],stamp.st_mtime_ns,stamp.st_size,stamp.st_ctime_ns))
        return json.dumps(sorted(sources), ensure_ascii=False)

    def query_revision(self):
        """Existing catalog's revision for bound query reuse; never starts a model."""
        with self.lock:return {'revision':self._query_revision_value(),'scope':'main_catalog'}

    def _query_revision_value(self):
        return json.dumps({'catalog':self._generation(),'retrieval_policy':'main-query-v1',
            'embedding':getattr(self.models,'embedding_name',None),'reranker':getattr(self.models,'reranker_name',None)},ensure_ascii=False)

    def page(self, name, query, scope=None, refresh=False, generator=None, *, history=False, version=None):
        if not isinstance(name,str) or not name.strip() or any(c in name for c in '/\\:') or name in {'.','..'}:
            raise ValueError('Knowledge page name must be a simple nonempty label')
        if not isinstance(query,str) or not query.strip() or type(history) is not bool or type(refresh) is not bool:
            raise ValueError('Invalid knowledge page request')
        if version is not None and (not isinstance(version,str) or not version or len(version)>128):raise ValueError('Invalid page version')
        if sum((refresh,history,version is not None))>1:raise ValueError('Choose refresh, history, or exact version')
        identity=json.dumps([name,scope_key(scope)],ensure_ascii=False)
        from derived_pages import page
        with self.lock:return page(self,name,query,scope,identity,refresh=refresh,generator=generator,history=history,version=version)


def runtime_from_config(config, home):
    if config.get('resource_manager',{}).get('enabled') and os.environ.get('ENGINEERING_MEMORY_WORKER')!='1':
        from resource_client import RemoteRuntime
        return RemoteRuntime(home)
    result= MemoryRuntime(config['vault'], Path(home)/'state.sqlite',
                         use_models=config.get('memory_models', True),
                         cache=config.get('model_cache') or Path(home)/'models')
    if isinstance(result.models,LocalModels):
        result.models.embedding_name=config.get('memory_embedding_model',result.models.embedding_name)
        result.models.reranker_name=config.get('memory_reranker_model',result.models.reranker_name)
        result.models.reranker_path=config.get('memory_reranker_path')
        result.models.embedding_path=config.get('memory_embedding_path')
    return result
