"""Interpret original queue/control records; never probe or restart a service."""
import json
import sqlite3
import time

from .claude_mem import invoke
from .errors import RailError

REASONS = {'quota_exhausted','quota_guard','auth_invalid','setup_required','index_unavailable','resource_unavailable'}
ACTIONS = {
    'quota_deferred':'Wait for the recorded allowance window or fix the configured quota; a restart does not restore quota.',
    'credential_rejected':'Review credentials in the existing memory provider configuration; do not put secrets in this status output.',
    'setup_required':'Complete the existing provider setup, then let its original retry queue continue.',
    'index_unavailable':'Inspect the existing index queue; raw capture or a saved source does not prove indexing completed.',
    'resource_deferred':'Allow the original resource backoff to expire; inspect shared resource limits if it repeats.',
    'queue_deferred':'Inspect the original queue control record; no specific cause can be confirmed from this view.'}


def explain(db, workspace, states, *, now=None):
    now = time.time() if now is None else now
    diagnostics = []
    try:
        rows = db.execute("""SELECT c.reason,c.next_retry,c.stage,j.state,count(*) AS jobs,max(c.updated) AS observed_at
            FROM jobs j JOIN job_control c ON c.job_id=j.id
            WHERE lower(replace(json_extract(j.payload,'$.cwd'),'\\','/'))=?
            AND j.state NOT IN ('saved','cancelled','discarded')
            GROUP BY c.reason,c.next_retry,c.stage,j.state ORDER BY c.next_retry LIMIT 12""", (workspace,)).fetchall()
        observations = [{'reason':r['reason'] if r['reason'] in REASONS else 'unknown',
            'until':r['next_retry'] if type(r['next_retry']) in (int,float) else 0,
            'jobs':r['jobs'],'observed_at':r['observed_at'],'source':'existing_memory.job_control'} for r in rows]
        for row in db.execute("SELECT key,value FROM meta WHERE key IN ('capture_backpressure:judge','capture_backpressure:index')"):
            try:
                value = json.loads(row['value'])
                until = value.get('until')
                if type(until) not in (int,float) or until <= now: continue
                reason = value.get('reason')
                observations.append({'reason':reason if reason in REASONS else 'resource_unavailable',
                    'until':until,'source':'existing_memory.meta','scope':'shared_runtime_not_task'})
            except (ValueError, AttributeError, TypeError):
                diagnostics.append({'code':'backpressure_record_invalid','source':'existing_memory.meta','restart_recommended':False})
        if observations:
            classified = invoke('classify', states=observations, now=now)
            for value, observed in zip(classified, observations):
                code = value['code']
                diagnostics.append({'code':code,'source':observed['source'],
                    'scope':observed.get('scope','workspace_jobs_not_task'),
                    'next_retry':observed['until'] or None,
                    'observed_at':observed.get('observed_at'),
                    'state':'waiting_until_recorded_retry' if value['waiting'] else 'last_observed_wait_retry_due',
                    'recovery':'not_observed','restart_recommended':False,'action':ACTIONS[code]})
    except sqlite3.Error:
        diagnostics.append({'code':'queue_control_unavailable','source':'existing_memory.job_control',
            'restart_recommended':False,'action':'This memory schema has no readable control detail; queue counts remain separate evidence.'})
    except RailError:
        diagnostics.append({'code':'classifier_unavailable','source':'bundled_claude_mem',
            'restart_recommended':False,'action':'Use Node 22.6+ with native type stripping; original queue counts remain available.'})
    if states.get('awaiting_index'):
        diagnostics.append({'code':'awaiting_index','jobs':states['awaiting_index'],'source':'existing_memory.jobs',
            'restart_recommended':False,'action':'Source processing is awaiting the existing index; learned/indexed completion is not confirmed.'})
    if states.get('failed'):
        diagnostics.append({'code':'failed_jobs','jobs':states['failed'],'source':'existing_memory.jobs',
            'restart_recommended':False,'action':'Inspect the failed records in the existing queue; status does not automatically replay or discard them.'})
    return diagnostics
