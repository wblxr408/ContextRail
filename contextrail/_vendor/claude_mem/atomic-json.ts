// SPDX-License-Identifier: Apache-2.0
// Literal Windows retry slice from src/shared/atomic-json.ts; see provenance.json.
import { renameSync } from 'node:fs';
export const IS_WINDOWS_PLATFORM = process.platform === 'win32';

const WINDOWS_RENAME_RETRY_CODES = new Set(['EPERM', 'EBUSY', 'EACCES']);
/** About 0.6 s in total, the same order as graceful-fs's Windows rename retry. */
const WINDOWS_RENAME_RETRY_DELAYS_MS = [10, 20, 40, 80, 160, 320];

export function sleepSync(ms: number): void {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

/**
 * Rename `from` over `to`. On Windows, a rename over a file that antivirus,
 * the search indexer or a sync client holds open for a moment fails with
 * EPERM, EBUSY or EACCES, so retry those with a short bounded backoff.
 */
export function renameWithWindowsRetry(
  from: string,
  to: string,
  rename: (from: string, to: string) => void = renameSync,
  onWindows: boolean = IS_WINDOWS_PLATFORM
): void {
  if (!onWindows) return rename(from, to);
  for (let attempt = 0; ; attempt++) {
    try {
      rename(from, to);
      return;
    } catch (err) {
      const code = (err as NodeJS.ErrnoException).code ?? '';
      if (attempt >= WINDOWS_RENAME_RETRY_DELAYS_MS.length || !WINDOWS_RENAME_RETRY_CODES.has(code)) {
        throw err;
      }
      sleepSync(WINDOWS_RENAME_RETRY_DELAYS_MS[attempt]);
    }
  }
}


