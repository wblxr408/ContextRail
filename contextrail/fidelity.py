"""Bounded literal provenance, adapted from ai-memory keep_tokens.rs (MIT).

Upstream 1bd866375b01031d63db54862a133376598d3701; see notices.
No language entailment claim: gates check declared requirements and literal facts.
"""
import re

POLICY='contextrail.literal-fidelity.v1'
PATTERNS=(
    ('url',r'https?://[^\s<>()\[\]"\'`，。；]+'),
    ('path',r'[A-Za-z]:[\\/][^\s<>"\'`，。；]+|(?:[\w.\-]{1,128}/){1,16}[\w.\-]{1,128}\.[A-Za-z0-9]{1,16}'),
    ('command',r'`([^`\n]{1,1024})`'),
    ('error',r'(?<![\w])(?:E\d{2,6}|ERR_[A-Z0-9_]+|HTTP\s?\d{3}|0x[0-9a-fA-F]{4,16})(?![\w])'),
    ('version',r'(?<![\w.])v?\d+\.\d+(?:\.\d+){0,2}(?:[-+][0-9A-Za-z.\-]+)?(?![\w.])'),
    ('quantity',r'(?<![\w.])\d+(?:\.\d+)?\s*(?:ms|s|sec|min|MiB|GiB|KiB|MB|GB|KB|bytes|tokens|%|毫秒|秒|分钟|小时|字节|次|条)(?![A-Za-z])'),
    ('negation',r'(?:不要|禁止|不得|不能|不允许|勿|\bdo not\b|\bmust not\b|\bnever\b|\bnot allowed\b)[^\n。；;!?！？]{1,512}'),
)
COMPILED=[(k,re.compile(p,re.I if k=='negation' else 0)) for k,p in PATTERNS]


def residue(sources,*,max_source_bytes=262144,max_entries=256):
    entries=[];scanned=total=omitted=0;seen=set();source_rows=[]
    for source in sources:
        text=source['text'];raw=text.encode('utf-8');total+=len(raw)
        prefix=raw[:max_source_bytes].decode('utf-8','ignore');used=len(prefix.encode());scanned+=used
        source_rows.append({'source':source['source'],'total_bytes':len(raw),'scanned_bytes':used})
        for kind,pattern in COMPILED:
            for m in pattern.finditer(prefix):
                a,b=m.span(1 if kind=='command' else 0);literal=prefix[a:b].rstrip('.,;:，。；：')
                b=a+len(literal)
                if not literal:continue
                key=(str(source['source']),kind,a,b)
                if key in seen:continue
                seen.add(key)
                if len(entries)>=max_entries or len(literal)>1024:omitted+=1;continue
                lo=max(prefix.rfind('\n',0,a),prefix.rfind('。',0,a),prefix.rfind('；',0,a))+1
                ends=[p for p in (prefix.find('\n',b),prefix.find('。',b),prefix.find('；',b)) if p>=0]
                hi=min(ends) if ends else len(prefix)
                lo=max(lo,a-192);hi=min(hi,b+192)
                entries.append({'kind':kind,'text':literal,'source':source['source'],
                    'start':source.get('start',0)+len(prefix[:a].encode()),'end':source.get('start',0)+len(prefix[:b].encode()),
                    'context':prefix[lo:hi],'context_start':source.get('start',0)+len(prefix[:lo].encode()),
                    'context_end':source.get('start',0)+len(prefix[:hi].encode()),'range_unit':'source_utf8_bytes'})
    return {'schema':POLICY,'entries':entries,'sources':source_rows,'coverage':'bounded_literal_rules_not_semantic_entailment',
            'loss':{'total_source_bytes':total,'scanned_bytes':scanned,'unscanned_bytes':total-scanned,'omitted_entries':omitted,
                    'selected_entries':len(entries),'complete_within_rules':scanned==total and not omitted}}


def validate(text,sources,*,required=(),require_coverage=True,claims=None):
    if not isinstance(text,str) or not text.strip():raise ValueError('A nonempty derivation is required')
    mined=residue(sources);out=residue([{'source':'draft','text':text}]);problems=[]
    for item in required:
        if item.strip() and item.strip() not in text:problems.append({'kind':'missing_required','text':item})
    critical={'version','quantity'}
    values={(x['kind'],x['text']) for x in mined['entries'] if x['kind'] in critical}
    actual={(x['kind'],x['text']) for x in out['entries'] if x['kind'] in critical}
    if require_coverage:
        for k,value in sorted(values-actual):problems.append({'kind':'missing_literal','literal_kind':k,'text':value})
    for k,value in sorted(actual-values):problems.append({'kind':'unsupported_literal','literal_kind':k,'text':value})
    for entry in mined['entries']:
        if entry['kind']=='negation' and entry['text'] not in text:
            problems.append({'kind':'missing_negation','text':entry['text'],'source':entry['source']})
    if not mined['loss']['complete_within_rules'] or not out['loss']['complete_within_rules']:
        problems.append({'kind':'coverage_incomplete'})
    if claims is not None:
        if not isinstance(claims,list) or len(claims)>128:raise ValueError('Invalid claims')
        for claim in claims:
            if not isinstance(claim,dict) or claim.get('kind') not in {'source','inference','unknown'} or not isinstance(claim.get('text'),str):
                raise ValueError('Claims require source/inference/unknown kind and text')
            if claim['kind']=='source':
                quote=claim.get('quote');citation=claim.get('citation')
                if not isinstance(quote,str) or not quote or claim['text'] not in quote or not any(s['source']==citation and quote in s['text'] for s in sources):
                    problems.append({'kind':'unsupported_source_claim','text':claim['text']})
            elif claim.get('citation') is not None or claim.get('quote') is not None:
                problems.append({'kind':'non_source_claim_with_proof','text':claim['text']})
    return {'policy':POLICY,'accepted':not problems,'problems':problems,'residue':mined,
            'claim_scope':'declared_claim_kinds_and_literal_coverage; unrestricted_semantic_inference_unproven'}
