// ContextRail adapter. Upstream source identity and local patches: provenance.json.
import { replaceOwnedFiles } from './owned-file-install.ts';
import { isQuotaFailure, isQuotaGuardPause, isAuthFailure, isSetupFailure,
  isObserverQuotaCooldownActive } from './observer-health.ts';
import { normalizeExplicitMemory } from './save-memory.ts';
import { postCompactHandler } from './post-compact.ts';
import { markTimelinePending, hasTimelinePending, takeTimelinePending } from './codex-compaction-gate.ts';
import { fileContextHandler, fileHistoryQueryPaths, deduplicateObservations } from './file-context.ts';
import { bindFileContext } from './file-context-host.mjs';
import { GATE_TABLE_DDL, CLAIM_GATE_SQL } from './file-context-dedupe.ts';
import { extractFilePaths } from './codex-file-context.ts';
import { productsFromFields, mergeProducts } from './work-product.mjs';

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
  if (request.action === 'work-products') {
    result = mergeProducts(request.groups.map(group=>productsFromFields(group.fields,{atEpoch:group.atEpoch,sourceRef:group.sourceRef})),request.limit ?? 60);
  } else if (request.action === 'file-history') {
    for (const history of request.histories) {
      history.displayedObservations = deduplicateObservations(history.displayedObservations, history.relativePath, 15)
        .sort((a,b)=>(a.created_at_epoch ?? 0)-(b.created_at_epoch ?? 0));
    }
    bindFileContext(request.histories, request.allowed);
    result = await fileContextHandler.execute({sessionId:request.session,cwd:request.root,toolInput:{filePaths:request.allowed}});
    result.gate = {ddl:GATE_TABLE_DDL,claim:CLAIM_GATE_SQL};
  } else if (request.action === 'file-paths') {
    result = {paths:extractFilePaths(request.tool,request.input,request.cwd),
      query_paths:request.absolute ? fileHistoryQueryPaths(request.absolute,request.relative) : []};
  } else if (request.action === 'compact-marker') {
    process.env.CONTEXTRAIL_MARKER_DIR = request.root;
    if (request.operation === 'mark') {
      await postCompactHandler.execute({sessionId:request.session});
      result = {pending:hasTimelinePending(request.session)};
    } else if (request.operation === 'take') {
      result = {taken:takeTimelinePending(request.session),pending:hasTimelinePending(request.session)};
    } else result = {pending:hasTimelinePending(request.session)};
  } else if (request.action === 'replace') {
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
