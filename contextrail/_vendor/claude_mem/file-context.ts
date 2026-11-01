// Selected original file-context ranking/format/handler flow; see provenance.json.
import path from 'node:path';
import { homedir } from 'node:os';
import { logger } from './logger.mjs';
import { parseJsonArray, formatTime, formatDate, formatHeaderDateTime } from './timeline-formatting.ts';
import { lookupFileObservationHistory, claimFileContextInjection } from './file-context-host.mjs';
type EventHandler = { execute(input: any): Promise<any> };
type NormalizedHookInput = any;
type HookResult = any;
const MAX_FILE_CONTEXT_PATHS=10;
const TYPE_ICONS: Record<string, string> = {
  decision: '\u2696\uFE0F',
  bugfix: '\uD83D\uDD34',
  feature: '\uD83D\uDFE3',
  refactor: '\uD83D\uDD04',
  discovery: '\uD83D\uDD35',
  change: '\u2705',
};

function compactTime(timeStr: string): string {
  return timeStr.toLowerCase().replace(' am', 'a').replace(' pm', 'p');
}

interface ObservationRow {
  id: string | number;
  memory_session_id: string;
  title: string | null;
  type: string;
  created_at_epoch: number | null;
  files_read: string | null;
  files_modified: string | null;
}

export function deduplicateObservations(
  observations: ObservationRow[],
  targetPath: string,
  displayLimit: number
): ObservationRow[] {
  const seenSessions = new Set<string>();
  const dedupedBySession: ObservationRow[] = [];
  for (const obs of observations) {
    const sessionKey = obs.memory_session_id ?? `no-session-${obs.id}`;
    if (!seenSessions.has(sessionKey)) {
      seenSessions.add(sessionKey);
      dedupedBySession.push(obs);
    }
  }

  const scored = dedupedBySession.map(obs => {
    const filesRead = parseJsonArray(obs.files_read);
    const filesModified = parseJsonArray(obs.files_modified);
    const totalFiles = filesRead.length + filesModified.length;
    const normalizedTarget = targetPath.replace(/\\/g, '/');
    const inModified = filesModified.some(f => f.replace(/\\/g, '/') === normalizedTarget);

    let specificityScore = 0;
    if (inModified) specificityScore += 2;
    if (totalFiles <= 3) specificityScore += 2;
    else if (totalFiles <= 8) specificityScore += 1;

    return { obs, specificityScore };
  });

  scored.sort((a, b) => b.specificityScore - a.specificityScore);

  return scored.slice(0, displayLimit).map(s => s.obs);
}

interface FileObservationHistory {
  /** The path as the tool input spelled it; the routing hints quote it back. */
  filePath: string;
  /** Canonical absolute path: the dedupe key and the gate's subject. */
  absolutePath: string;
  relativePath: string;
  newestObservationMs: number;
  /** Deduped and ranked down to DISPLAY_LIMIT, then ordered oldest first for display. */
  displayedObservations: ObservationRow[];
  /**
   * The lookup stat'ed the file, so its size and mtime checks ran. False when
   * stat failed with anything but ENOENT: the timeline is still context, but
   * the gate never denies a Read it could not check.
   */
  fileStatVerified: boolean;
  /** The size the lookup stat'ed; 0 when the stat failed. */
  fileSizeBytes: number;
}

/** Escapes a path for the quoted tool-call hints (backslashes, quotes, newlines). */
function escapePathForHint(filePath: string): string {
  return filePath.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n');
}

/**
 * The day-grouped history both outputs share: `### <day>`, then one
 * `<id> <time> <icon> <title>` line per observation. Expects oldest-first rows.
 */
function formatObservationTimeline(observationsOldestFirst: ObservationRow[]): string[] {
  const lines: string[] = [];
  let currentDay: string | null = null;
  for (const obs of observationsOldestFirst) {
    const day = obs.created_at_epoch == null ? 'Time unknown' : formatDate(obs.created_at_epoch);
    if (day !== currentDay) {
      lines.push(`### ${day}`);
      currentDay = day;
    }
    const title = (obs.title || 'Untitled').replace(/[\r\n\t]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 160);
    const icon = TYPE_ICONS[obs.type] || '\u2753';
    const time = obs.created_at_epoch == null ? '?' : compactTime(formatTime(obs.created_at_epoch));
    lines.push(`${obs.id} ${time} ${icon} ${title}`);
  }
  return lines;
}

function formatFileContextTimeline(history: FileObservationHistory): string {
  const safePath = escapePathForHint(history.filePath);
  return [
    `Current: ${formatHeaderDateTime()}`,
    `Historical observations for this file; tool permissions and current file contents remain native.`,
    `Use context.history_details with the exact source refs in this envelope.`,
    ...formatObservationTimeline(history.displayedObservations),
  ].join('\n');
}

// The `Full-file Read blocked by claude-mem:` line is the read-gate eval's trace
// anchor (evals/read-gate) — keep it verbatim.
export const fileContextHandler: EventHandler = {
  async execute(input: NormalizedHookInput): Promise<HookResult> {
    if (input.agentId) {
      logger.debug('HOOK', 'Skipping file context: subagent context detected', {
        sessionId: input.sessionId,
        agentId: input.agentId,
        agentType: input.agentType
      });
      return { continue: true, suppressOutput: true };
    }

    const toolInput = input.toolInput as Record<string, unknown> | undefined;
    const filePaths = Array.isArray(toolInput?.filePaths)
      ? (toolInput.filePaths as unknown[]).filter((p): p is string => typeof p === 'string').slice(0, MAX_FILE_CONTEXT_PATHS)
      : [];
    const filePath = toolInput?.file_path as string | undefined;
    const candidatePaths = filePaths.length > 0 ? filePaths : (filePath ? [filePath] : []);

    if (candidatePaths.length === 0) {
      return { continue: true, suppressOutput: true };
    }

    const lookupResults = await Promise.allSettled(
      candidatePaths.map(candidatePath => lookupFileObservationHistory(input, candidatePath))
    );
    const histories: FileObservationHistory[] = [];

    lookupResults.forEach((result, index) => {
      if (result.status === 'fulfilled') {
        if (result.value) histories.push(result.value);
        return;
      }
      logger.debug('HOOK', 'File context timeline lookup failed, skipping path', {
        filePath: candidatePaths[index],
        error: result.reason instanceof Error ? result.reason.message : String(result.reason),
      });
    });

    const timelines: string[] = [];
    for (const history of histories) {
      // #3480 — skip re-injecting the same still-valid timeline for a file already
      // surfaced this session; re-inject only once a newer observation has landed.
      // Claimed last, after every other reason to bail out, so a suppressed
      // injection never burns the claim — and claiming IS recording, so two
      // concurrent Reads of this file cannot both inject.
      if (!claimFileContextInjection(input.sessionId, history.absolutePath, history.newestObservationMs)) {
        logger.debug('HOOK', 'File context already surfaced this session, skipping re-injection', {
          filePath: history.relativePath,
          sessionId: input.sessionId,
          newestObservationMs: history.newestObservationMs,
        });
        continue;
      }
      timelines.push(formatFileContextTimeline(history));
    }

    if (timelines.length === 0) {
      return { continue: true, suppressOutput: true };
    }

    // Context only, no permissionDecision: on a synchronous hook 'allow' would
    // skip the user's permission prompt for this Read.
    return {
      hookSpecificOutput: {
        hookEventName: 'PreToolUse',
        additionalContext: timelines.join('\n\n---\n\n'),
      },
    };
  },
};

export function fileHistoryQueryPaths(
  absolutePath: string,
  relativePath: string,
  homeDirectory: string = homedir(),
): string[] {
  const absoluteForward = absolutePath.split(path.sep).join('/');
  const homeForward = homeDirectory.split(path.sep).join('/');
  const homeRelative = absoluteForward.startsWith(homeForward + '/')
    ? '~/' + absoluteForward.slice(homeForward.length + 1)
    : '';
  return Array.from(new Set([absoluteForward, relativePath, homeRelative].filter(Boolean)));
}

