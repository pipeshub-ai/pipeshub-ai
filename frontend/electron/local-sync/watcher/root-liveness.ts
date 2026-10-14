import * as fs from 'fs';
import { isValidInode } from '../persistence/watcher-state-store';

export type RootLiveness = 'alive' | 'missing' | 'replaced' | 'unreadable';

/** Identity of the directory the watcher bound to at start. */
export interface RootFingerprint {
  ino: number;
  dev: number;
}

/**
 * null when the filesystem reports no usable inode (FAT32, some SMB shares),
 * in which case liveness falls back to existence alone.
 */
export function fingerprintRoot(stats: fs.Stats): RootFingerprint | null {
  const ino = typeof stats.ino === 'bigint' ? Number(stats.ino) : stats.ino;
  if (!isValidInode(ino)) return null;
  return { ino: Number(ino), dev: Number(stats.dev) };
}

/**
 * Is `rootPath` still the directory we started watching?
 *
 * Deliberately a path lookup rather than anything asked of the watcher:
 * chokidar holds an open handle, a handle follows the directory through a
 * rename, so it stays valid — and silent — after the user moves the sync root
 * away. Re-resolving the configured path from its parent is the only thing
 * that observes that.
 */
export async function checkRootLiveness(
  rootPath: string,
  fingerprint: RootFingerprint | null,
): Promise<RootLiveness> {
  let stats: fs.Stats;
  try {
    stats = await fs.promises.lstat(rootPath);
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    // EPERM/EACCES also means gone on Windows: a directory deleted while we
    // still hold a handle keeps its name until that handle closes, and opening
    // it fails rather than reporting ENOENT.
    return code === 'ENOENT' || code === 'ENOTDIR' || code === 'EPERM' || code === 'EACCES'
      ? 'missing'
      : 'unreadable';
  }
  if (!stats.isDirectory()) return 'missing';
  if (!fingerprint) return 'alive';
  const current = fingerprintRoot(stats);
  if (!current) return 'alive';
  // Same name, different directory: the folder we indexed was moved away and
  // another put in its place, which existence alone cannot tell apart.
  return current.ino === fingerprint.ino && current.dev === fingerprint.dev ? 'alive' : 'replaced';
}
