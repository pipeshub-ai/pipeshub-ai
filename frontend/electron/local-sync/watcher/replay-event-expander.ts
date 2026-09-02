import * as path from 'path';
import { contentFileHash } from '../persistence/watcher-state-store';

/**
 * Per-file expansion of directory-level events. Needed for replay because the
 * backend expects file granularity, and by the time we replay a failed batch
 * the directory may already be gone from disk — so we expand against the
 * persisted watcher state snapshot we captured at the time the event fired.
 */
export interface FileStateEntry {
  inode?: number;
  size?: number;
  mtimeMs?: number;
  isDirectory: boolean;
  sha256?: string;
}

export type FileStateMap = Record<string, FileStateEntry>;

export interface WatchEvent {
  type: string;
  path: string;
  oldPath?: string;
  timestamp: number;
  size?: number;
  isDirectory: boolean;
  sha256?: string;
}

function listFilesUnderPrefix(files: FileStateMap, prefix: string): string[] {
  const normalizedPrefix = String(prefix || '').replace(/\/+$/, '');
  const childPrefix = normalizedPrefix ? `${normalizedPrefix}/` : '';
  return Object.keys(files)
    .filter((relPath) => {
      const entry = files[relPath];
      return entry && !entry.isDirectory && (relPath === normalizedPrefix || relPath.startsWith(childPrefix));
    })
    .sort((a, b) => a.localeCompare(b));
}

/**
 * Best-effort fresh hash for a file expanded out of a directory move: right
 * after a live `DIR_RENAMED`/`DIR_MOVED` the file genuinely exists at its new
 * path, but a stale batch replayed later may find it already gone — in which
 * case the last known hash captured in the state snapshot is the best
 * available answer.
 */
async function resolveExpandedFileHash(
  rootPath: string | undefined,
  newRelPath: string,
  cachedSha256: string | undefined,
): Promise<string | undefined> {
  if (rootPath) {
    const fresh = await contentFileHash(path.resolve(rootPath, newRelPath));
    if (fresh !== undefined) return fresh;
  }
  return cachedSha256;
}

function eventIdentity(event: WatchEvent): string {
  return JSON.stringify([event.type, event.path, event.oldPath || '']);
}

export async function expandWatchEventsForReplay(
  events: WatchEvent[],
  files: FileStateMap,
  rootPath?: string,
): Promise<WatchEvent[]> {
  const expanded: WatchEvent[] = [];
  for (const event of events) {
    if (!event.isDirectory) { expanded.push(event); continue; }
    expanded.push(event);
    if (event.type === 'DIR_DELETED') {
      for (const relPath of listFilesUnderPrefix(files, event.path)) {
        expanded.push({ type: 'DELETED', path: relPath, timestamp: event.timestamp, isDirectory: false });
      }
      continue;
    }
    if (event.type === 'DIR_MOVED' || event.type === 'DIR_RENAMED') {
      const oldPrefix = String(event.oldPath || '').replace(/\/+$/, '');
      if (!oldPrefix) continue;
      for (const oldRelPath of listFilesUnderPrefix(files, oldPrefix)) {
        const suffix = oldRelPath.slice(oldPrefix.length).replace(/^\/+/, '');
        const newRelPath = [event.path, suffix].filter(Boolean).join('/');
        const sha256 = await resolveExpandedFileHash(rootPath, newRelPath, files[oldRelPath]?.sha256);
        expanded.push({
          type: event.type === 'DIR_MOVED' ? 'MOVED' : 'RENAMED',
          path: newRelPath, oldPath: oldRelPath,
          timestamp: event.timestamp, isDirectory: false,
          sha256,
        });
      }
    }
  }
  // A directory event that survives expansion alongside its own children would
  // expand again if this ran twice over the same array. Collapsing identical
  // events keeps that idempotent whatever the caller does.
  const seen = new Set<string>();
  return expanded.filter((event) => {
    const identity = eventIdentity(event);
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}
