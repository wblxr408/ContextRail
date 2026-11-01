// ContextRail adapter. Upstream source identity and local patches: provenance.json.
import { replaceOwnedFiles } from './owned-file-install.ts';
import { isQuotaFailure, isQuotaGuardPause, isAuthFailure, isSetupFailure,
  isObserverQuotaCooldownActive } from './observer-health.ts';
import { normalizeExplicitMemory } from './save-memory.ts';

const chunks = [];
let bytes = 0;
for await (const chunk of process.stdin) {
  chunks.push(chunk);
  bytes += chunk.length;
  if (bytes > 16 * 1024 * 1024) throw new Error('Request too large');
}
try {
  const request = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  let result;
  if (request.action === 'replace') {
    replaceOwnedFiles(request.root, request.files.map(file => ({name:file.name,
      contents:Buffer.from(file.contents, 'base64'),
      expected:file.expected === null ? null : Buffer.from(file.expected, 'base64')})));
    result = {committed:true};
  } else if (request.action === 'classify') {
    result = request.states.map(state => {
      const health = {lastErrorKind:state.reason, quotaCooldown:{until:state.until * 1000}};
      const quota = isQuotaFailure(health) || isQuotaGuardPause(health);
      return {code:quota ? 'quota_deferred' : isAuthFailure(health) ? 'credential_rejected' :
        isSetupFailure(health) ? 'setup_required' : state.reason === 'index_unavailable' ? 'index_unavailable' :
        state.reason === 'resource_unavailable' ? 'resource_deferred' : 'queue_deferred',
        waiting:isObserverQuotaCooldownActive(health, request.now * 1000)};
    });
  } else if (request.action === 'normalize-memory') {
    result = normalizeExplicitMemory(request.input);
  } else throw new Error('Unknown action');
  process.stdout.write(JSON.stringify({ok:true,result}));
} catch (error) {
  // Never echo provider messages, configuration contents, command arguments or stderr.
  const recovery = error instanceof AggregateError ? error.message.split('recovery files remain at ')[1] : null;
  process.stdout.write(JSON.stringify({ok:false, code:recovery ? 'rollback_incomplete' : 'operation_rejected',
    recovery_dir:recovery || null}));
  process.exitCode = 1;
}
