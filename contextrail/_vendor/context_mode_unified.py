"""MODIFIED Python adaptation of context-mode src/search/unified.ts (ELv2).

Copyright (c) mksglu and context-mode contributors.
Pinned source: 90af14e34bf35120c3310f7a75f54fa99d2cabba.
See context_mode_LICENSE.txt. Changes: both modes merge all sources; no fake
session-start time; source failures remain in caller envelope; use rank fusion
instead of comparing unrelated raw scores. No auto-memory filesystem scanner.
"""
from datetime import datetime


def merge_sources(groups,view):
    results=[]
    source_order={source:index for index,source in enumerate(groups)}
    for source,hits in groups.items():
        for rank,hit in enumerate(hits,1):
            results.append({**hit,'source_rank':rank,'fusion_score':1/(60+rank)})
    if view=='timeline':
        def order(hit):
            value=hit['time'].get('occurred_at')
            try:stamp=datetime.fromisoformat(value).timestamp() if value else None
            except (ValueError,TypeError):stamp=None
            return stamp is None,stamp or 0,hit['origin'],hit['source_rank']
        results.sort(key=order)
    else:results.sort(key=lambda h:(-h['fusion_score'],source_order[h['origin']],h['source_rank']))
    return results
