// SPDX-License-Identifier: Apache-2.0
// Pure source slices; see provenance.json. No health ledger or probes.
export interface ObserverHealthState {
  /** Failures since the last successful store. */
  consecutiveFailures: number;
  /** Epoch ms of the first failure in the current streak. */
  failingSinceAt: number | null;
  /** Epoch ms of the most recent failure. */
  lastErrorAt: number | null;
  /** Scrubbed + truncated message of the most recent failure. */
  lastErrorMessage: string | null;
  /** Provider whose generator failed most recently. */
  lastErrorProvider: string | null;
  /** Epoch ms of the most recent successful observation/summary store. */
  lastSuccessAt: number | null;
  /**
   * Structured detail from a classified provider error (the gateway's taxonomy
   * envelope `{ code, message, action, url, request_id }`), carried so the
   * session-start warning shows the same words as the worker's `Observer failed`
   * log line. All null for unclassified failures and for ledgers written by
   * older builds.
   */
  lastErrorCode?: string | null;
  /** Scrubbed remedy text ("It resets on …", "Add credits at …"). */
  lastErrorAction?: string | null;
  /** Link the remedy points at (stored as-is; URLs are not credentials). */
  lastErrorUrl?: string | null;
  /** Gateway/upstream request id for support. */
  lastErrorRequestId?: string | null;
  /**
   * Classified failure kind (`quota_exhausted`, `auth`, …). The warning reads
   * this to pick its remedy: a spent allowance is the one outage a restart
   * cannot clear, so it must not be offered one.
   */
  lastErrorKind?: string | null;
  /**
   * Intentional pause while the provider quota breaker is withholding
   * generator starts. Distinct from lastError*: a cooldown is not a failure,
   * and must not flip the ledger to unhealthy. Null when no breaker is armed
   * (or after it was cleared on a successful store).
   */
  quotaCooldown: ObserverQuotaCooldown | null;
}

/**
 * Persisted half of the quota breaker, mirrored into the health ledger so a
 * cooldown is visible without tailing the worker log. `until` is authoritative:
 * a stale `active: true` after expiry must not keep the banner up.
 */
export interface ObserverQuotaCooldown {
  active: boolean;
  provider: string;
  /**
   * 'claude' only: the config-dir profile (Claude account) the breaker pauses.
   * A reader shows the pause only while that account is selected
   * (quota-cooldown's cooldownAppliesToCurrentAccount).
   */
  profile?: string;
  /** Epoch ms when the breaker was armed. */
  armedAt: number;
  /** Epoch ms when the next probe is allowed. */
  until: number;
  /** Window the provider named, when it named one (e.g. 'weekly'). */
  window?: string;
  /** Provider-reported reason, already free of any user prompt text. */
  message?: string;
  /**
   * Where memory capture runs while this provider is held: the configured
   * quota fallback, or the recovered primary when the fallback's own breaker
   * is the one mirrored. Absent when no fallback is configured or nothing
   * else can serve; the notice then says capture is paused.
   */
  servingProvider?: string;
  /** 'claude' only: the credential that is held ('api-key' | 'subscription'), when one is. */
  auth?: string;
}



export const OBSERVER_UNHEALTHY_FAILURE_THRESHOLD = 3;
export const OBSERVER_QUOTA_FAILURE_STALE_AFTER_MS = 30 * 60_000;

export function isObserverUnhealthy(state: ObserverHealthState | null): state is ObserverHealthState {
  // The threshold lets a blip self-heal before it warns. A refused credential
  // is not a blip — the provider said so — and the cooldown armed with it
  // allows no second attempt for a while, so waiting for the threshold would
  // hide the one remedy (a new key) for over an hour. A setup failure is the
  // same: it fails every request until the user acts, and its gate re-checks
  // only every few minutes.
  return state !== null
    && (state.consecutiveFailures >= OBSERVER_UNHEALTHY_FAILURE_THRESHOLD || isAuthFailure(state) || isSetupFailure(state))
    && (state.lastErrorAt ?? 0) > (state.lastSuccessAt ?? 0);
}

export function describeDuration(ms: number): string {
  const minutes = Math.max(1, Math.round(ms / 60_000));
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'}`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `about ${hours} hour${hours === 1 ? '' : 's'}`;
  return `about ${Math.round(hours / 24)} days`;
}

export function isQuotaFailure(state: ObserverHealthState): boolean {
  return state.lastErrorKind === 'quota_exhausted';
}

export function isQuotaGuardPause(state: ObserverHealthState): boolean {
  return state.lastErrorKind === 'quota_guard';
}

export function isAuthFailure(state: ObserverHealthState): boolean {
  return state.lastErrorKind === 'auth_invalid';
}

export function isSetupFailure(state: ObserverHealthState): boolean {
  return state.lastErrorKind === 'setup_required';
}

function hasOutlivedRecheckWindow(state: ObserverHealthState, nowMs: number): boolean {
  const lastErrorAt = state.lastErrorAt;
  // `>=`, not `>`: `isObserverQuotaCooldownActive` calls the cooldown expired
  // at `until > nowMs`, so at exactly the recheck interval the breaker has
  // already released. A strict `>` left one instant where the breaker was
  // open and the banner still called the outage current.
  return lastErrorAt !== null && nowMs - lastErrorAt >= OBSERVER_QUOTA_FAILURE_STALE_AFTER_MS;
}

export function isQuotaFailureStale(
  state: ObserverHealthState,
  nowMs: number = Date.now(),
): boolean {
  return (isQuotaFailure(state) || isQuotaGuardPause(state)) && hasOutlivedRecheckWindow(state, nowMs);
}

export function isDeadlineFailureStale(
  state: ObserverHealthState,
  nowMs: number = Date.now(),
): boolean {
  return state.lastErrorCode === 'deadline_exceeded' && hasOutlivedRecheckWindow(state, nowMs);
}

export function isObserverQuotaCooldownActive(
  state: ObserverHealthState | null,
  nowMs: number = Date.now(),
): state is ObserverHealthState {
  const cooldown = state?.quotaCooldown;
  return cooldown != null
    && typeof cooldown.until === 'number'
    && cooldown.until > nowMs;
}
