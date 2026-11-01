import { chmodSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, rmdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve, relative, isAbsolute, sep } from 'node:path';
import { logger } from './logger.mjs';
import { renameWithWindowsRetry as renameSync } from './atomic-json.ts';

export interface OwnedFile { name: string; contents: Buffer; expected?: Buffer | null; }

export function readProjectAttribution(packageRoot: string): OwnedFile[] {
  // Read both inputs before a caller creates or changes its destination.
  return ['LICENSE', 'NOTICE'].map(name => ({ name, contents: readFileSync(join(packageRoot, name)) }));
}

function statIfPresent(path: string): ReturnType<typeof lstatSync> | undefined {
  try { return lstatSync(path); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return undefined;
    throw error;
  }
}


// ContextRail compatibility additions: contained relative paths, compare-before-write,
// and upstream bounded Windows rename retries. Original staging/rollback body remains below.
function checkTarget(destination: string, file: OwnedFile): void {
  const root = resolve(destination);
  const target = resolve(root, file.name);
  const rel = relative(root, target);
  if (!rel || rel.startsWith('..' + sep) || isAbsolute(rel)) throw new Error('Owned target escapes root');
  let current = root;
  for (const part of rel.split(sep)) {
    current = join(current, part);
    const entry = statIfPresent(current);
    if (entry?.isSymbolicLink()) throw new Error('Owned path crosses a link');
    if (current !== target && entry && !entry.isDirectory()) throw new Error('Owned parent is not a directory');
  }
  if (file.expected !== undefined) {
    const entry = statIfPresent(target);
    if (file.expected === null ? Boolean(entry) : !entry?.isFile() || !readFileSync(target).equals(file.expected)) {
      throw new Error('Owned target changed while preparing update');
    }
  }
}
/** Replace only an explicit owned set; retain the old files until every rename succeeds. */
export function replaceOwnedFiles(destination: string, files: OwnedFile[]): void {
  const names = new Set<string>();
  const destinationStat = statIfPresent(destination);
  if (destinationStat && !destinationStat.isDirectory()) throw new Error('Installation directory is not a real directory: ' + destination);
  for (const file of files) {
    if (!file.name || isAbsolute(file.name) || file.name.split(/[\\/]/).some(part => !part || part === '.' || part === '..') || file.name.includes(':') || names.has(file.name)) {
      throw new Error('Invalid or repeated owned filename: ' + file.name);
    }
    names.add(file.name);
    checkTarget(destination, file);
    const current = statIfPresent(join(destination, file.name));
    if (current && !current.isFile()) throw new Error('Owned destination is not a regular file: ' + join(destination, file.name));
  }

  // Node on Windows can reject recursive mkdir of an existing drive root.
  if (!statIfPresent(dirname(destination))) mkdirSync(dirname(destination), { recursive: true });
  const stage = mkdtempSync(join(dirname(destination), '.claude-mem-files-'));
  const backup = join(stage, 'previous');
  const applied: { name: string; backedUp: boolean; installed: boolean }[] = [];
  let createdDestination = false;
  let committed = false;
  let rollbackComplete = true;
  try {
    chmodSync(stage, 0o700);
    mkdirSync(backup, { mode: 0o700 });
    for (const file of files) {
      mkdirSync(dirname(join(stage, file.name)), { recursive: true });
      writeFileSync(join(stage, file.name), file.contents, { flag: 'wx', mode: 0o644 });
    }
    if (!destinationStat) {
      mkdirSync(destination);
      createdDestination = true;
    }
    for (const file of files) {
      const change = { name: file.name, backedUp: false, installed: false };
      applied.push(change);
      const target = join(destination, file.name);
      checkTarget(destination, file);
      mkdirSync(dirname(target), { recursive: true });
      mkdirSync(dirname(join(backup, file.name)), { recursive: true });
      if (statIfPresent(target)) {
        renameSync(target, join(backup, file.name));
        change.backedUp = true;
      }
      renameSync(join(stage, file.name), target);
      change.installed = true;
    }
    committed = true;
  } catch (failure) {
    const rollbackErrors: unknown[] = [];
    for (const change of applied.reverse()) {
      try {
        const target = join(destination, change.name);
        if (change.installed) rmSync(target, { force: true });
        if (change.backedUp) renameSync(join(backup, change.name), target);
      } catch (error) { rollbackErrors.push(error); }
    }
    if (createdDestination) {
      try { if (readdirSync(destination).length === 0) rmdirSync(destination); }
      catch (error) { rollbackErrors.push(error); }
    }
    rollbackComplete = rollbackErrors.length === 0;
    if (!rollbackComplete) {
      throw new AggregateError([failure, ...rollbackErrors], 'Could not replace owned files: ' + String(failure) + '; recovery files remain at ' + stage, { cause: failure });
    }
    throw failure;
  } finally {
    if (committed || rollbackComplete) {
      // A cleanup refusal after commit does not turn an installed set into a reported failure.
      try { rmSync(stage, { recursive: true, force: true }); }
      catch (error) { logger.warn('SYSTEM', 'Could not remove installation staging directory ' + stage + ': ' + String(error)); }
    }
  }
}

