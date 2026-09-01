import * as path from 'path';
import * as fs from 'fs';
import { normalizeRelKey, contentFileHash, type FileSnapshotEntry } from '../persistence/watcher-state-store';
import type { WatchEvent } from './replay-event-expander';

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
  sha256?: string;
}

/** Result of a suppress check that also had to read the file, so the caller
 * can reuse that hash on the emitted event instead of re-reading it. */
export interface ModifiedSuppressionResult {
  suppress: boolean;
  sha256?: string;
}

export interface EventCorrelatorOptions {
  syncRoot: string;
  correlationWindowMs?: number;
  unlinkCorrelationWindowMs?: number;
  changeDebounceMs?: number;
  shouldSuppressModifiedChange?: (
    event: RawEvent
  ) => Promise<ModifiedSuppressionResult> | ModifiedSuppressionResult;
  getPreviousFileEntry?: (relKey: string) => FileSnapshotEntry | undefined;
}

export type EventListener = (events: WatchEvent[]) => void;

function isValidInode(ino: unknown): boolean {
  return ino !== undefined && Number.isFinite(ino as number) && (ino as number) > 0;
}

function dirnamePosix(p: string): string {
  const i = p.lastIndexOf('/');
  return i <= 0 ? '' : p.slice(0, i);
}

export class EventCorrelator {
  private syncRoot: string;
  /** Exposed so callers can size their own windows off the effective values
   * rather than re-deriving these defaults. */
  readonly correlationWindowMs: number;
  readonly unlinkCorrelationWindowMs: number;
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
      this.emit([{ type: 'MODIFIED', path: raw.relKey, timestamp: add.timestamp, size: add.size, isDirectory: raw.isDirectory, sha256: add.sha256 }]);
      return;
    }
    // Chokidar's unlink event typically lacks stats (file is already gone),
    // so recover inode/size/sha256 from the persisted watcher state. Without
    // this, rename detection in flush() can never match by inode or by hash.
    const enriched: RawEvent = { ...raw };
    if (!isValidInode(enriched.inode) || enriched.sha256 === undefined) {
      const prev = this.getPreviousFileEntry(raw.relKey);
      if (prev) {
        if (!isValidInode(enriched.inode) && isValidInode(prev.inode)) enriched.inode = prev.inode;
        if (enriched.size === undefined && !prev.isDirectory) enriched.size = prev.size;
        if (!raw.isDirectory && prev.sha256) enriched.sha256 = prev.sha256;
      }
    }
    this.pendingUnlinks.set(raw.relKey, enriched);
    if (isValidInode(enriched.inode)) this.unlinkInodes.set(enriched.inode!, enriched);
    this.scheduleFlush(this.unlinkCorrelationWindowMs);
    if (this.pendingUnlinks.size > MAX_PENDING_UNLINK_ENTRIES) this.flush(true);
  }

  private async handleAdd(raw: RawEvent): Promise<void> {
    // Computed once up front and reused by every emit path below (rename
    // match, atomic-write match, or the CREATED fallthrough in flush()) —
    // never hashed twice for the same add.
    let hash: string | undefined;
    if (!raw.isDirectory) hash = await contentFileHash(raw.absPath);
    const pending: RawEvent = { ...raw, sha256: hash };

    if (this.pendingUnlinks.has(raw.relKey)) {
      const unlink = this.pendingUnlinks.get(raw.relKey)!;
      this.pendingUnlinks.delete(raw.relKey);
      if (isValidInode(unlink.inode)) this.unlinkInodes.delete(unlink.inode!);
      this.emit([{ type: 'MODIFIED', path: raw.relKey, timestamp: raw.timestamp, size: raw.size, isDirectory: raw.isDirectory, sha256: hash }]);
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
        this.emit([{ type: evtType, path: raw.relKey, oldPath: unlink.relKey, timestamp: raw.timestamp, size: raw.size, isDirectory: raw.isDirectory, sha256: hash }]);
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
    let sha256: string | undefined;
    if (!ev.isDirectory) {
      if (this.shouldSuppressModifiedChange) {
        try {
          const result = await this.shouldSuppressModifiedChange(ev);
          if (result.suppress) return;
          sha256 = result.sha256;
        } catch { /* fall through and hash directly below */ }
      }
      if (sha256 === undefined) sha256 = await contentFileHash(ev.absPath);
    }
    this.emit([{ type: 'MODIFIED', path: ev.relKey, timestamp: ev.timestamp, size: ev.size, isDirectory: ev.isDirectory, sha256 }]);
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
        if (u.sha256) {
          const arr = unlinksByHash.get(u.sha256) || [];
          arr.push(u);
          unlinksByHash.set(u.sha256, arr);
        }
      }
      for (const [relKey, add] of this.pendingAdds) {
        if (!add.sha256) continue;
        const matches = unlinksByHash.get(add.sha256);
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
        events.push({ type: evtType, path: add.relKey, oldPath: unlink.relKey, timestamp: add.timestamp, size: add.size, isDirectory: add.isDirectory, sha256: add.sha256 });
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
      events.push({ type: a.isDirectory ? 'DIR_CREATED' : 'CREATED', path: a.relKey, timestamp: a.timestamp, size: a.size, isDirectory: a.isDirectory, sha256: a.sha256 });
    }
    this.pendingAdds.clear();
    if (nextUnlinkFlushInMs !== null) this.scheduleFlush(nextUnlinkFlushInMs);
    if (events.length > 0) this.emit(events);
  }

  private emit(events: WatchEvent[]): void {
    if (this.onEvents && events.length > 0) this.onEvents(events);
  }

  /**
   * Emit everything buffered. `forceUnlinks` decides the fate of deletes that
   * are still inside the correlation window: forcing them turns a rename whose
   * `add` has not landed yet into DELETE + CREATE, which costs the record its
   * identity server-side. Only a drain that is about to discard the correlator
   * (stop()) may force; a drain that merely wants the journal current before a
   * pull must not, and lets those unlinks correlate into the next page.
   */
  async drain(forceUnlinks = true): Promise<void> {
    if (this.flushTimer) { clearTimeout(this.flushTimer); this.flushTimer = null; this.flushTimerDueAt = null; }
    for (const t of this.changeTimers.values()) clearTimeout(t);
    this.changeTimers.clear();
    const pendingList = [...this.pendingChanges.values()];
    this.pendingChanges.clear();
    for (const ev of pendingList) await this.emitModifiedIfNeeded(ev);
    this.flush(forceUnlinks);
  }
}
