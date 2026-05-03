import * as fs from 'fs';
import * as fsp from 'fs/promises';
import * as path from 'path';
import * as crypto from 'crypto';
import type { WatchEvent } from '../watcher/replay-event-expander';

const WATCHER_STATE_VERSION = 1;
const QUICK_HASH_MAX_BYTES = 4096;
const SAVE_DEBOUNCE_MS = 5000;

export interface FileSnapshotEntry {
  inode?: number;
  size: number;
  mtimeMs: number;
  isDirectory: boolean;
  quickHash?: string;
}

export type FileSnapshotMap = Record<string, FileSnapshotEntry>;
export type ScanEntries = Map<string, FileSnapshotEntry> | Record<string, FileSnapshotEntry>;

export interface WatcherStateSnapshot {
  version: number;
  syncRoot: string;
  connectorInstanceId: string;
  lastScanTimestamp: number;
  files: FileSnapshotMap;
}

export interface ScanSyncRootOptions {
  includeSubfolders?: boolean;
  previousByRelPath?: Map<string, FileSnapshotEntry>;
  ignoredPatterns?: ReadonlyArray<RegExp | string>;
}

export interface WatcherStateStoreArgs {
  baseDir: string;
  syncRoot: string;
  connectorInstanceId: string;
  saveDebounceMs?: number;
}

export function connectorFileSegment(connectorInstanceId: unknown): string {
  const t = String(connectorInstanceId || '').trim();
  if (!t) return 'unknown';
  return t.replace(/[^a-zA-Z0-9._-]+/g, '_').slice(0, 200);
}

function watcherStateFilePath(baseDir: string, connectorInstanceId: string): string {
  return path.join(baseDir, `watcher_state.${connectorFileSegment(connectorInstanceId)}.json`);
}

function toPosixRelKey(rel: string): string {
  return rel.split(path.sep).join('/');
}

export function normalizeRelKey(absPath: string, syncRoot: string): string {
  const rel = path.relative(syncRoot, absPath);
  if (rel === '' || rel === '.') return '';
  // NFC so macOS HFS+/APFS NFD filenames hash identically to user-space
  // NFC paths on the Python side. Without this, a CREATED in NFC and a
  // RENAMED whose oldPath chokidar reports in NFD compute different
  // external_record_ids and the server treats them as unrelated files.
  return toPosixRelKey(rel).normalize('NFC');
}

function dirnamePosix(p: string): string {
  const i = p.lastIndexOf('/');
  return i <= 0 ? '' : p.slice(0, i);
}

function isValidInode(ino: unknown): boolean {
  if (ino === undefined || ino === null) return false;
  const n = typeof ino === 'bigint' ? Number(ino) : (ino as number);
  return Number.isFinite(n) && n > 0;
}

async function computeQuickHash(absFilePath: string, size: number): Promise<string | undefined> {
  try {
    const fh = await fsp.open(absFilePath, 'r');
    try {
      const buf = Buffer.allocUnsafe(Math.min(QUICK_HASH_MAX_BYTES, Math.max(0, size)));
      let read = 0;
      if (buf.length > 0) {
        const { bytesRead } = await fh.read(buf, 0, buf.length, 0);
        read = bytesRead;
      }
      const h = crypto.createHash('sha256');
      h.update(buf.subarray(0, read));
      h.update(`|${size}|`);
      return h.digest('hex');
    } finally {
      await fh.close();
    }
  } catch {
    return undefined;
  }
}

export async function contentQuickHash(absPath: string): Promise<string | undefined> {
  try {
    const st = await fsp.lstat(absPath);
    if (!st.isFile()) return undefined;
    return computeQuickHash(absPath, st.size);
  } catch {
    return undefined;
  }
}

function matchesAnyPattern(
  patterns: ReadonlyArray<RegExp | string>,
  relPath: string,
  absPath: string,
): boolean {
  for (const p of patterns) {
    if (p instanceof RegExp) {
      if (p.test(absPath) || p.test(relPath)) return true;
    } else if (typeof p === 'string') {
      if (relPath === p || absPath === p) return true;
    }
  }
  return false;
}

export async function scanSyncRoot(
  syncRootAbs: string,
  options: ScanSyncRootOptions = {},
): Promise<Map<string, FileSnapshotEntry>> {
  const includeSubfolders = options.includeSubfolders !== false;
  const previousByRelPath = options.previousByRelPath;
  const ignoredPatterns = options.ignoredPatterns || [];
  const root = path.resolve(syncRootAbs);
  const out = new Map<string, FileSnapshotEntry>();

  async function visit(dirAbs: string): Promise<void> {
    let entries: fs.Dirent[];
    try {
      entries = await fsp.readdir(dirAbs, { withFileTypes: true });
    } catch {
      return;
    }
    for (const ent of entries) {
      if (ent.name === '.' || ent.name === '..') continue;
      const abs = path.join(dirAbs, ent.name);
      const relKey = normalizeRelKey(abs, root);
      if (matchesAnyPattern(ignoredPatterns, relKey, abs)) continue;
      let st: fs.Stats;
      try {
        st = await fsp.lstat(abs);
      } catch {
        continue;
      }
      const isDirectory = st.isDirectory();
      const inode = typeof st.ino === 'bigint' ? Number(st.ino) : st.ino;
      const size = st.isFile() ? st.size : 0;
      const mtimeMs = st.mtimeMs;
      let quickHash: string | undefined;
      if (st.isFile()) {
        const old = previousByRelPath && previousByRelPath.get(relKey);
        if (old && !old.isDirectory && old.size === size && old.mtimeMs === mtimeMs && old.quickHash) {
          quickHash = old.quickHash;
        } else {
          quickHash = await computeQuickHash(abs, size);
        }
      }
      out.set(relKey, { inode, size, mtimeMs, isDirectory, quickHash });
      if (isDirectory && includeSubfolders) {
        await visit(abs);
      }
    }
  }

  await visit(root);
  return out;
}

function emptyState(syncRoot: string, connectorInstanceId: string): WatcherStateSnapshot {
  return {
    version: WATCHER_STATE_VERSION,
    syncRoot,
    connectorInstanceId,
    lastScanTimestamp: 0,
    files: {},
  };
}

function parseFileEntry(raw: unknown): FileSnapshotEntry | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const r = raw as Record<string, unknown>;
  const inode = Number(r.inode);
  const size = Number(r.size);
  const mtimeMs = Number(r.mtimeMs);
  if (!Number.isFinite(inode) || !Number.isFinite(size) || !Number.isFinite(mtimeMs)) return null;
  const isDirectory = Boolean(r.isDirectory);
  const quickHash = typeof r.quickHash === 'string' && r.quickHash.length > 0 ? r.quickHash : undefined;
  return { inode, size, mtimeMs, isDirectory, quickHash };
}

export class WatcherStateStore {
  private baseDir: string;
  private debounceMs: number;
  private syncRoot: string;
  private connectorInstanceId: string;
  private state: WatcherStateSnapshot;
  private saveTimer: NodeJS.Timeout | null;
  private dirty: boolean;

  constructor({ baseDir, syncRoot, connectorInstanceId, saveDebounceMs }: WatcherStateStoreArgs) {
    this.baseDir = path.resolve(baseDir);
    this.debounceMs = saveDebounceMs != null ? saveDebounceMs : SAVE_DEBOUNCE_MS;
    this.syncRoot = path.resolve(syncRoot);
    this.connectorInstanceId = String(connectorInstanceId).trim();
    this.state = emptyState(this.syncRoot, this.connectorInstanceId);
    this.saveTimer = null;
    this.dirty = false;
  }

  statePath(): string {
    return watcherStateFilePath(this.baseDir, this.connectorInstanceId);
  }

  getSnapshot(): WatcherStateSnapshot {
    return this.state;
  }

  load(): void {
    const p = this.statePath();
    if (!fs.existsSync(p)) {
      this.state = emptyState(this.syncRoot, this.connectorInstanceId);
      return;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(fs.readFileSync(p, 'utf8'));
    } catch {
      this.state = emptyState(this.syncRoot, this.connectorInstanceId);
      return;
    }
    if (typeof parsed !== 'object' || parsed === null) {
      this.state = emptyState(this.syncRoot, this.connectorInstanceId);
      return;
    }
    const raw = parsed as Record<string, unknown>;
    const version = Number(raw.version);
    if (version !== 1 && version !== 2) {
      this.state = emptyState(this.syncRoot, this.connectorInstanceId);
      return;
    }
    const fileSyncRoot = typeof raw.syncRoot === 'string' ? path.resolve(raw.syncRoot) : '';
    const fileConnectorId = typeof raw.connectorInstanceId === 'string' ? raw.connectorInstanceId.trim() : '';
    if (fileSyncRoot !== this.syncRoot || fileConnectorId !== this.connectorInstanceId) {
      this.state = emptyState(this.syncRoot, this.connectorInstanceId);
      return;
    }
    const files: FileSnapshotMap = {};
    if (raw.files && typeof raw.files === 'object') {
      for (const [k, v] of Object.entries(raw.files as Record<string, unknown>)) {
        const norm = k.split('\\').join('/');
        const entry = parseFileEntry(v);
        if (entry) files[norm] = entry;
      }
    }
    this.state = {
      version: WATCHER_STATE_VERSION,
      syncRoot: this.syncRoot,
      connectorInstanceId: this.connectorInstanceId,
      lastScanTimestamp: Number.isFinite(Number(raw.lastScanTimestamp)) ? Number(raw.lastScanTimestamp) : 0,
      files,
    };
  }

  applyScan(entries: ScanEntries): void {
    const next: FileSnapshotMap = {};
    if (entries instanceof Map) {
      for (const [k, v] of entries) next[k.split('\\').join('/')] = { ...v };
    } else {
      for (const [k, v] of Object.entries(entries)) next[k.split('\\').join('/')] = { ...v };
    }
    this.state.files = next;
    this.state.syncRoot = this.syncRoot;
    this.state.connectorInstanceId = this.connectorInstanceId;
    this.state.lastScanTimestamp = Date.now();
    this.scheduleSave();
  }

  reconcile(currentScan: Map<string, FileSnapshotEntry>): WatchEvent[] {
    const now = Date.now();
    const oldFiles = this.state.files;
    const oldPaths = new Set(Object.keys(oldFiles));
    const newPaths = new Set(currentScan.keys());
    const events: WatchEvent[] = [];
    const oldByInode = new Map<number, { paths: string[] }>();
    const newByInode = new Map<number, { paths: string[] }>();

    for (const p of oldPaths) {
      const e = oldFiles[p];
      if (!e || !isValidInode(e.inode)) continue;
      let g = oldByInode.get(e.inode!);
      if (!g) { g = { paths: [] }; oldByInode.set(e.inode!, g); }
      g.paths.push(p);
    }
    for (const p of newPaths) {
      const e = currentScan.get(p);
      if (!e || !isValidInode(e.inode)) continue;
      let g = newByInode.get(e.inode!);
      if (!g) { g = { paths: [] }; newByInode.set(e.inode!, g); }
      g.paths.push(p);
    }

    const handledOld = new Set<string>();
    const handledNew = new Set<string>();

    for (const [ino, oldG] of oldByInode) {
      const newG = newByInode.get(ino);
      if (!newG) continue;
      if (oldG.paths.length !== 1 || newG.paths.length !== 1) continue;
      const oldPath = oldG.paths[0];
      const newPath = newG.paths[0];
      if (oldPath === newPath) continue;
      const oldEnt = oldFiles[oldPath];
      const newEnt = currentScan.get(newPath)!;
      if (oldEnt.isDirectory !== newEnt.isDirectory) continue;
      const sameDir = dirnamePosix(oldPath) === dirnamePosix(newPath);
      const type = newEnt.isDirectory
        ? (sameDir ? 'DIR_RENAMED' : 'DIR_MOVED')
        : (sameDir ? 'RENAMED' : 'MOVED');
      events.push({
        type, path: newPath, oldPath, timestamp: now,
        size: newEnt.isDirectory ? undefined : newEnt.size,
        isDirectory: newEnt.isDirectory,
      });
      handledOld.add(oldPath);
      handledNew.add(newPath);
    }

    for (const p of oldPaths) {
      if (handledOld.has(p)) continue;
      if (!newPaths.has(p)) continue;
      const oldEnt = oldFiles[p];
      const newEnt = currentScan.get(p)!;
      if (oldEnt.isDirectory !== newEnt.isDirectory) {
        events.push({ type: oldEnt.isDirectory ? 'DIR_DELETED' : 'DELETED', path: p, timestamp: now, isDirectory: oldEnt.isDirectory });
        events.push({ type: newEnt.isDirectory ? 'DIR_CREATED' : 'CREATED', path: p, timestamp: now, size: newEnt.isDirectory ? undefined : newEnt.size, isDirectory: newEnt.isDirectory });
        handledOld.add(p); handledNew.add(p);
        continue;
      }
      const inodeSame = oldEnt.inode === newEnt.inode || (!isValidInode(oldEnt.inode) && !isValidInode(newEnt.inode));
      let metaSame: boolean;
      if (newEnt.isDirectory) {
        metaSame = oldEnt.mtimeMs === newEnt.mtimeMs;
      } else if (oldEnt.quickHash && newEnt.quickHash) {
        metaSame = oldEnt.size === newEnt.size && oldEnt.quickHash === newEnt.quickHash;
      } else if (!oldEnt.quickHash && newEnt.quickHash) {
        metaSame = oldEnt.size === newEnt.size && oldEnt.mtimeMs === newEnt.mtimeMs;
      } else {
        metaSame = oldEnt.size === newEnt.size && oldEnt.mtimeMs === newEnt.mtimeMs && oldEnt.quickHash === newEnt.quickHash;
      }
      if (!inodeSame || !metaSame) {
        events.push({ type: 'MODIFIED', path: p, timestamp: now, size: newEnt.isDirectory ? undefined : newEnt.size, isDirectory: newEnt.isDirectory });
      }
      handledOld.add(p); handledNew.add(p);
    }

    for (const p of oldPaths) {
      if (handledOld.has(p)) continue;
      const e = oldFiles[p];
      events.push({ type: e.isDirectory ? 'DIR_DELETED' : 'DELETED', path: p, timestamp: now, isDirectory: e.isDirectory });
    }
    for (const p of newPaths) {
      if (handledNew.has(p)) continue;
      const e = currentScan.get(p)!;
      events.push({ type: e.isDirectory ? 'DIR_CREATED' : 'CREATED', path: p, timestamp: now, size: e.isDirectory ? undefined : e.size, isDirectory: e.isDirectory });
    }
    return events;
  }

  commitReconcile(currentScan: Map<string, FileSnapshotEntry>): WatchEvent[] {
    const ev = this.reconcile(currentScan);
    this.applyScan(currentScan);
    this.flushSave();
    return ev;
  }

  scheduleSave(): void {
    this.dirty = true;
    if (this.saveTimer) return;
    this.saveTimer = setTimeout(() => {
      this.saveTimer = null;
      this.flushSave();
    }, this.debounceMs);
  }

  flushSave(): void {
    if (this.saveTimer) { clearTimeout(this.saveTimer); this.saveTimer = null; }
    if (!this.dirty) return;
    this.dirty = false;
    const p = this.statePath();
    const tmp = `${p}.tmp`;
    try {
      fs.mkdirSync(this.baseDir, { recursive: true });
      fs.writeFileSync(tmp, JSON.stringify(this.state, null, 2), 'utf8');
      fs.renameSync(tmp, p);
    } catch {
      try { if (fs.existsSync(tmp)) fs.unlinkSync(tmp); } catch { /* ignore */ }
    }
  }
}
