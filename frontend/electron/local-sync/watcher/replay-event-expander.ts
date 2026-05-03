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
  quickHash?: string;
}

export type FileStateMap = Record<string, FileStateEntry>;

export interface WatchEvent {
  type: string;
  path: string;
  oldPath?: string;
  timestamp: number;
  size?: number;
  isDirectory: boolean;
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

export function expandWatchEventsForReplay(
  events: WatchEvent[],
  files: FileStateMap,
): WatchEvent[] {
  const expanded: WatchEvent[] = [];
  for (const event of events) {
    if (!event.isDirectory) { expanded.push(event); continue; }
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
        expanded.push({
          type: event.type === 'DIR_MOVED' ? 'MOVED' : 'RENAMED',
          path: newRelPath, oldPath: oldRelPath,
          timestamp: event.timestamp, isDirectory: false,
        });
      }
    }
  }
  return expanded;
}
