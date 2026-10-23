"""MODIFIED Python port of context-mode adapters/codex/usage.ts (ELv2).

Copyright (c) mksglu and context-mode contributors.
Source 90af14e34bf35120c3310f7a75f54fa99d2cabba; context_mode_LICENSE.txt.
Keep last_token_usage and cumulative separately, preserve absent/invalid counts
as None, do NOT add reasoning to output (it is an included subset), no price
catalog, line cursor or new file reader. Existing source admission owns replay.
"""
FIELDS=('input_tokens','cached_input_tokens','output_tokens','reasoning_output_tokens','total_tokens')


def _counts(value):
    if not isinstance(value,dict):return None
    result={k:value[k] if type(value.get(k)) is int and 0<=value[k]<=2**63-1 else None for k in FIELDS}
    return result


def parse_codex_usage(payload,model=None):
    if not isinstance(payload,dict) or payload.get('type')!='token_count':return None
    info=payload.get('info');info=info if isinstance(info,dict) else {}
    last=_counts(info.get('last_token_usage'));total=_counts(info.get('total_token_usage'))
    return {'source':'codex_rollout.token_count','model':model if isinstance(model,str) and model else None,
            'counts':last,'cumulative':total,'cache_write_tokens':None,
            'counts_state':'unknown' if last is None else 'partial' if any(v is None for v in last.values()) else 'reported',
            'counting':'cached_is_subset_of_input; reasoning_is_subset_of_output',
            'granularity':'native_report_not_a_rail_provider_request','native_cost':None}
