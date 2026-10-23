"""Source-scoped dimensions, never a claim of complete transcript recovery."""


def report(source,unit,*,total=None,selected=None,truncated=0,unsupported=0,unknown=None,reasons=None,recover=None,scope=None):
    return {'schema':'contextrail.loss.v1','source':source,'unit':unit,'scope':scope,
            'total':total,'selected':selected,'omitted':total-selected if total is not None and selected is not None else None,
            'truncated':truncated,'unsupported':unsupported,'unknown':unknown,
            'dimensions':'selected+omitted=total_when_known; truncated/unsupported_overlap_omitted; unknown_is_uninspected_or_unobserved',
            'reasons':reasons or {},'recover':recover or {}}


def packet_loss(context):
    if 'loss' in context:return context['loss']
    selected=context.get('stable_evidence',[])+context.get('evidence',[])
    omitted=context.get('available_total',len(context.get('available',[])))
    return {'task_evidence':report('task_evidence','refs',total=len(selected)+omitted,selected=len(selected),unknown=omitted,
             reasons={'cold_reason_not_classified_by_legacy_compiler':omitted},scope='declared_snapshot',
             recover={'tool':'context.list','arguments':{'snapshot':context['snapshot']},'read_tool':'context.get'})}
