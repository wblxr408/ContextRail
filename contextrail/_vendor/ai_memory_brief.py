"""MODIFIED Python adaptation of ai-memory excerpt.rs and brief_tiers.rs.

Copyright (c) Fabio Akita. MIT; see ai_memory_LICENSE.txt.
Source 1bd866375b01031d63db54862a133376598d3701: ai-memory-core/excerpt.rs,
profile.rs metadata labels, ai-memory-hooks/brief_tiers.rs.
Differences: a directory tier always retains Rail refs; caller measures complete
JSON for each promotion, rather than adding byte costs. Atomic group promotion
and mandatory pages are caller-owned. No recent-section share or pinned slots.
"""
import re

LABELS={'status','date','deciders','decision-makers','authors','author','owner','owners',
        'supersedes','superseded by','consulted','informed','tags','last updated','reviewers'}


def clip(text,maximum):
    raw=text.encode('utf-8')
    if len(raw)<=maximum:return text
    return raw[:maximum-3].decode('utf-8',errors='ignore')+'…'


def leading_prose(body):
    body=body.encode('utf-8')[:16384].decode('utf-8',errors='ignore')
    result=[];size=0
    for block in re.split(r'\r?\n\s*\r?\n',body):
        if block.strip().startswith('_Extractively compacted from a cold episodic page'):continue
        for line in block.splitlines():
            line=line.strip()
            if not line or line.startswith('#') or line=='---':continue
            head=line[:256].lstrip('-*_ ')
            metadata=False
            for close in (':**','**:',':__','__:'):
                if close in head:
                    label=head.split(close,1)[0].strip().casefold()
                    if label in LABELS:metadata=True;break
            if metadata:continue
            for word in line.split():
                # Need only enough to decide clipping; huge words stay bounded.
                piece=word.encode('utf-8')[:504].decode('utf-8',errors='ignore')
                result.append(piece);size+=len(piece.encode())+1
                if size>500:return ' '.join(result)
    return ' '.join(result) or None


def excerpt_from_page(frontmatter,body):
    def field(name):
        value=frontmatter.get(name)
        return value.strip() if isinstance(value,str) and value.strip() else None
    abstract=field('abstract');summary=field('summary');prose=None
    if not abstract and not summary:prose=leading_prose(body)
    return tuple(clip(' '.join(text.split()),maximum) if text else ''
                 for text,maximum in ((abstract or summary or prose,256),(summary or abstract or prose,500)))


def assign_tiers(pages,promote):
    """Port coverage → L1 → short L2 → cheapest L2. Caller's render is truth.

    pages: (identity, costs); promote(identity,tier) atomically attempts the
    rendered change. Costs only order attempts; they never prove the budget.
    """
    for identity,costs in pages:promote(identity,'L0')
    at_l1={identity:promote(identity,'L1') or costs['L1']<=costs['L0'] for identity,costs in pages}
    for identity,costs in pages:
        if costs['L2']-costs['L1']<=128:
            if promote(identity,'L2'):at_l1[identity]=True
    # Cheapest full bodies first after all affordable summary promotions.
    if all(at_l1.values()):
        for identity,costs in sorted(pages,key=lambda p:(p[1]['L2']-p[1]['L1'],str(p[0]))):
            promote(identity,'L2')
