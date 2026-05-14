import * as fsp from 'fs/promises';
import * as path from 'path';
import * as crypto from 'crypto';
import * as fs from 'fs';
import { normalizeRelKey, type FileSnapshotEntry } from '../persistence/watcher-state-store';
import type { WatchEvent } from './replay-event-expander';

const QUICK_HASH_BYTES = 4096;
const MAX_PENDING_UNLINK_ENTRIES = 10000;

export type ChokidarEventName = 'add' | 'addDir' | 'unlink' | 'unlinkDir' | 'change';

interface RawEvent {
  type: ChokidarEventName;
  absPath: string;
  relKey: string;
  timestamp: number;
  inode?: number;
  size?: number;
  mtimeMs?: number;
  isDirectory: boolean;
  quickHash?: string;
}

export interface EventCorrelatorOptions {
  syncRoot: string;
  correlationWindowMs?: number;
  unlinkCorrelationWindowMs?: number;
  changeDebounceMs?: number;
  shouldSuppressModifiedChange?: (event: RawEvent) => Promise<boolean> | boolean;
  getPreviousFileEntry?: (relKey: string) => FileSnapshotEntry | undefined;
}

export type EventListener = (events: WatchEvent[]) => void;

async function quickHash(absPath: string): Promise<string | undefined> {
  try {
    const fh = await fsp.open(absPath, 'r');
    try {
      const stat = await fh.stat();
      const buf = Buffer.allocUnsafe(Math.min(QUICK_HASH_BYTES, Math.max(0, stat.size)));
      let read = 0;
      if (buf.length > 0) {
        const { bytesRead } = await fh.read(buf, 0, buf.length, 0);
        read = bytesRead;
      }
      const h = crypto.createHash('sha256');
      h.update(buf.subarray(0, read));
      h.update(`|${stat.size}|`);
      return h.digest('hex');
    } finally {
      await fh.close();
    }
  } catch {
    return undefined;
  }
}

function isValidInode(ino: unknown): boolean {
  return ino !== undefined && Number.isFinite(ino as number) && (ino as number) > 0;
}

function dirnamePosix(p: string): string {
  const i = p.lastIndexOf('/');
  return i <= 0 ? '' : p.slice(0, i);
}

export class EventCorrelator {
  private syncRoot: string;
  private correlationWindowMs: number;
  private unlinkCorrelationWindowMs: number;
  private changeDebounceMs: number;
  private shouldSuppressModifiedChange?: EventCorrelatorOptions['shouldSuppressModifiedChange'];
  private getPreviousFileEntry: (relKey: string) => FileSnapshotEntry | undefined;
  private pendingUnlinks: Map<string, RawEvent>;
  private pendingAdds: Map<string, RawEvent>;
  private changeTimers: Map<string, NodeJS.Timeout>;
  private pendingChanges: Map<string, RawEvent>;
  private flushTimer: NodeJS.Timeout | null;
  private flushTimerDueAt: number | null;
  private onEvents: EventListener | null;
  private unlinkInodes: Map<number, RawEvent>;

  constructor(opts: EventCorrelatorOptions) {
    this.syncRoot = path.resolve(opts.syncRoot);
    this.correlationWindowMs = opts.correlationWindowMs != null ? opts.correlationWindowMs : 250;
    // Unlinks often arrive immediately while the matching add is delayed by
    // chokidar awaitWriteFinish. Keep deletes pending longer, but continue
    // flushing unrelated creates/changes on the shorter correlation window.
    this.unlinkCorrelationWindowMs = opts.unlinkCorrelationWindowMs != null
      ? opts.unlinkCorrelationWindowMs
      : Math.max(this.correlationWindowMs, 2000);
    this.changeDebounceMs = opts.changeDebounceMs != null ? opts.changeDebounceMs : 300;
    this.shouldSuppressModifiedChange = opts.shouldSuppressModifiedChange;
    this.getPreviousFileEntry = opts.getPreviousFileEntry || (() => undefined);
    this.pendingUnlinks = new Map();
    this.pendingAdds = new Map();
    this.changeTimers = new Map();
    this.pendingChanges = new Map();
    this.flushTimer = null;
    this.flushTimerDueAt = null;
    this.onEvents = null;
    this.unlinkInodes = new Map();
  }

  setListener(fn: EventListener): void {
    this.onEvents = fn;
  }

  async push(type: ChokidarEventName, absPath: string, stats?: fs.Stats): Promise<void> {
    const relKey = normalizeRelKey(absPath, this.syncRoot);
    if (!relKey) return;
    const isDirectory = type === 'addDir' || type === 'unlinkDir';
    const raw: RawEvent = {
      type, absPath, relKey,
      timestamp: Date.now(),
      inode: stats ? (typeof stats.ino === 'bigint' ? Number(stats.ino) : stats.ino) : undefined,
      size: stats && typeof stats.isFile === 'function' && stats.isFile() ? stats.size : undefined,
      mtimeMs: stats && stats.mtimeMs,
      isDirectory,
    };
    switch (type) {
      case 'unlink':
      case 'unlinkDir': await this.handleUnlink(raw); break;
      case 'add':
      case 'addDir': await this.handleAdd(raw); break;
      case 'change': this.handleChange(raw); break;
    }
  }

  private async handleUnlink(raw: RawEvent): Promise<void> {
    if (this.pendingAdds.has(raw.relKey)) {
      const add = this.pendingAdds.get(raw.relKey)!;
      this.pendingAdds.delete(raw.relKey);
      this.emit([{ type: 'MODIFIED', path: raw.relKey, timestamp: add.timestamp, size: add.size, isDirectory: raw.isDirectory }]);
      return;
    }
    // Chokidar's unlink event typically lacks stats (file is already gone),
    // so recover inode/size/quickHash from the persisted watcher state. Without
    // this, rename detection in flush() can never match by inode or by hash.
    const enriched: RawEvent = { ...raw };
    if (!isValidInode(enriched.inode) || enriched.quickHash === undefined) {
      const prev = this.getPreviousFileEntry(raw.relKey);
      if (prev) {
        if (!isValidInode(enriched.inode) && isValidInode(prev.inode)) enriched.inode = prev.inode;
        if (enriched.size === undefined && !prev.isDirectory) enriched.size = prev.size;
        if (!raw.isDirectory && prev.quickHash) enriched.quickHash = prev.quickHash;
      }
    }
    this.pendingUnlinks.set(raw.relKey, enriched);
    if (isValidInode(enriched.inode)) this.unlinkInodes.set(enriched.inode!, enriched);
    this.scheduleFlush(this.unlinkCorrelationWindowMs);
    if (this.pendingUnlinks.size > MAX_PENDING_UNLINK_ENTRIES) this.flush(true);
  }

  private async handleAdd(raw: RawEvent): Promise<void> {
    let hash: string | undefined;
    if (!raw.isDirectory) hash = await quickHash(raw.absPath);
    const pending: RawEvent = { ...raw, quickHash: hash };

    if (this.pendingUnlinks.has(raw.relKey)) {
      const unlink = this.pendingUnlinks.get(raw.relKey)!;
      this.pendingUnlinks.delete(raw.relKey);
      if (isValidInode(unlink.inode)) this.unlinkInodes.delete(unlink.inode!);
      this.emit([{ type: 'MODIFIED', path: raw.relKey, timestamp: raw.timestamp, size: raw.size, isDirectory: raw.isDirectory }]);
      return;
    }

    if (isValidInode(raw.inode) && this.unlinkInodes.has(raw.inode!)) {
      const unlink = this.unlinkInodes.get(raw.inode!)!;
      if (unlink.isDirectory === raw.isDirectory) {
        this.unlinkInodes.delete(raw.inode!);
        this.pendingUnlinks.delete(unlink.relKey);
        const sameDir = dirnamePosix(unlink.relKey) === dirnamePosix(raw.relKey);
        const evtType = raw.isDirectory
          ? (sameDir ? 'DIR_RENAMED' : 'DIR_MOVED')
          : (sameDir ? 'RENAMED' : 'MOVED');
        this.emit([{ type: evtType, path: raw.relKey, oldPath: unlink.relKey, timestamp: raw.timestamp, size: raw.size, isDirectory: raw.isDirectory }]);
        return;
      }
    }

    this.pendingAdds.set(raw.relKey, pending);
    this.scheduleFlush();
  }

  private handleChange(raw: RawEvent): void {
    const existing = this.changeTimers.get(raw.relKey);
    if (existing) clearTimeout(existing);
    this.pendingChanges.set(raw.relKey, raw);
    const relKey = raw.relKey;
    const timer = setTimeout(() => {
      this.changeTimers.delete(relKey);
      this.flushDebouncedChange(relKey).catch(() => { /* ignore */ });
    }, this.changeDebounceMs);
    this.changeTimers.set(raw.relKey, timer);
  }

  private async flushDebouncedChange(relKey: string): Promise<void> {
    const ev = this.pendingChanges.get(relKey);
    if (!ev) return;
    this.pendingChanges.delete(relKey);
    await this.emitModifiedIfNeeded(ev);
  }

  private async emitModifiedIfNeeded(ev: RawEvent): Promise<void> {
    if (!ev.isDirectory && this.shouldSuppressModifiedChange) {
      try {
        if (await this.shouldSuppressModifiedChange(ev)) return;
      } catch { /* emit below */ }
    }
    this.emit([{ type: 'MODIFIED', path: ev.relKey, timestamp: ev.timestamp, size: ev.size, isDirectory: ev.isDirectory }]);
  }

  private scheduleFlush(delayMs = this.correlationWindowMs): void {
    const delay = Math.max(0, delayMs);
    const dueAt = Date.now() + delay;
    if (this.flushTimer && this.flushTimerDueAt !== null && this.flushTimerDueAt <= dueAt) return;
    if (this.flushTimer) clearTimeout(this.flushTimer);
    this.flushTimerDueAt = dueAt;
    this.flushTimer = setTimeout(() => {
      this.flushTimer = null;
      this.flushTimerDueAt = null;
      this.flush();
    }, delay);
  }

  flush(force = false): void {
    const events: WatchEvent[] = [];
    if (this.pendingUnlinks.size > 0 && this.pendingAdds.size > 0) {
      const unlinksByHash = new Map<string, RawEvent[]>();
      for (const [, u] of this.pendingUnlinks) {
        if (u.quickHash) {
          const arr = unlinksByHash.get(u.quickHash) || [];
          arr.push(u);
          unlinksByHash.set(u.quickHash, arr);
        }
      }
      for (const [relKey, add] of this.pendingAdds) {
        if (!add.quickHash) continue;
        const matches = unlinksByHash.get(add.quickHash);
        if (!matches || matches.length === 0) continue;
        const idx = matches.findIndex((u) => u.isDirectory === add.isDirectory && this.pendingUnlinks.has(u.relKey));
        if (idx === -1) continue;
        const unlink = matches[idx];
        matches.splice(idx, 1);
        this.pendingUnlinks.delete(unlink.relKey);
        this.pendingAdds.delete(relKey);
        if (isValidInode(unlink.inode)) this.unlinkInodes.delete(unlink.inode!);
        const sameDir = dirnamePosix(unlink.relKey) === dirnamePosix(add.relKey);
        const evtType = add.isDirectory
          ? (sameDir ? 'DIR_RENAMED' : 'DIR_MOVED')
          : (sameDir ? 'RENAMED' : 'MOVED');
        events.push({ type: evtType, path: add.relKey, oldPath: unlink.relKey, timestamp: add.timestamp, size: add.size, isDirectory: add.isDirectory });
      }
    }
    const now = Date.now();
    let nextUnlinkFlushInMs: number | null = null;
    for (const [, u] of this.pendingUnlinks) {
      const ageMs = now - u.timestamp;
      if (!force && ageMs < this.unlinkCorrelationWindowMs) {
        const remainingMs = this.unlinkCorrelationWindowMs - ageMs;
        nextUnlinkFlushInMs = nextUnlinkFlushInMs === null
          ? remainingMs
          : Math.min(nextUnlinkFlushInMs, remainingMs);
        continue;
      }
      events.push({ type: u.isDirectory ? 'DIR_DELETED' : 'DELETED', path: u.relKey, timestamp: u.timestamp, isDirectory: u.isDirectory });
      this.pendingUnlinks.delete(u.relKey);
      if (isValidInode(u.inode)) this.unlinkInodes.delete(u.inode!);
    }
    for (const [, a] of this.pendingAdds) {
      events.push({ type: a.isDirectory ? 'DIR_CREATED' : 'CREATED', path: a.relKey, timestamp: a.timestamp, size: a.size, isDirectory: a.isDirectory });
    }
    this.pendingAdds.clear();
    if (nextUnlinkFlushInMs !== null) this.scheduleFlush(nextUnlinkFlushInMs);
    if (events.length > 0) this.emit(events);
  }

  private emit(events: WatchEvent[]): void {
    if (this.onEvents && events.length > 0) this.onEvents(events);
  }

  async drain(): Promise<void> {
    if (this.flushTimer) { clearTimeout(this.flushTimer); this.flushTimer = null; this.flushTimerDueAt = null; }
    for (const t of this.changeTimers.values()) clearTimeout(t);
    this.changeTimers.clear();
    const pendingList = [...this.pendingChanges.values()];
    this.pendingChanges.clear();
    for (const ev of pendingList) await this.emitModifiedIfNeeded(ev);
    this.flush(true);
  }
}
