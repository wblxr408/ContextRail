import { existsSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import path from 'node:path';
const MAX_FILE_PATHS=10;
export function expandHomeDirectory(candidate: string, homeDirectory: string = homedir()): string {
  if (candidate === '~') return homeDirectory;
  if (candidate.startsWith('~/')) return path.join(homeDirectory, candidate.slice(2));
  return candidate;
}

function isExistingFile(candidate: string, cwd: string): boolean {
  const absolutePath = path.isAbsolute(candidate) ? candidate : path.resolve(cwd, candidate);
  try {
    if (!existsSync(absolutePath)) return false;
    return statSync(absolutePath).isFile();
  } catch {
    // [ANTI-PATTERN IGNORED]: this probes arbitrary tokens parsed from shell commands against the
    // filesystem, so stat failures (delete race after existsSync, EACCES, junk tokens) are expected
    // per-token noise; recovery is treating the candidate as not a file, same as existsSync failing.
    return false;
  }
}

function dedupeAndCap(paths: string[]): string[] {
  const seen = new Set<string>();
  const deduped: string[] = [];

  for (const filePath of paths) {
    if (seen.has(filePath)) continue;
    seen.add(filePath);
    deduped.push(filePath);
    if (deduped.length >= MAX_FILE_PATHS) break;
  }

  return deduped;
}

function extractFromMcp(toolName: string, toolInput: unknown, cwd: string, homeDirectory: string): string[] {
  if (!/^mcp__.+__(read|view|cat)(?:_file|_files)?$/.test(toolName)) return [];

  const input = (toolInput ?? {}) as { path?: unknown; paths?: unknown };
  const candidates: string[] = [];

  if (typeof input.path === 'string') candidates.push(input.path);
  if (Array.isArray(input.paths)) {
    for (const item of input.paths) {
      if (typeof item === 'string') candidates.push(item);
    }
  }

  return dedupeAndCap(candidates.map(candidate => expandHomeDirectory(candidate, homeDirectory)).filter(candidate => isExistingFile(candidate, cwd)));
}

export function extractFilePaths(toolName: string, toolInput: unknown, cwd: string, homeDirectory: string = homedir()): string[] {
  if (toolName.startsWith('mcp__')) return extractFromMcp(toolName, toolInput, cwd, homeDirectory);
  return [];
}

