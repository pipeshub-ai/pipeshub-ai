import * as fs from "fs";
import * as fsp from "fs/promises";
import * as crypto from "crypto";
import * as path from "path";
import { type FileEvent, type FileEventType, normalizeRelKey } from "./watcher_state";

/**
 * Buffers raw chokidar events and correlates `unlink`+`add` pairs into
 * RENAMED / MOVED events using inode tracking and content-hash fallback.
 *
 * Also deduplicates rapid `change` bursts on the same path.
 */

const QUICK_HASH_BYTES = 4096;

export type RawEventType = "add" | "addDir" | "unlink" | "unlinkDir" | "change";

export interface RawEvent {
  type: RawEventType;
  absPath: string;
  /** Relative posix-style key under sync root. */
  relKey: string;
  timestamp: number;
  /** Populated for add/addDir/change via lstat. */
  inode?: number;
  size?: number;
  mtimeMs?: number;
  isDirectory: boolean;
}

export interface EventCorrelatorOptions {
  syncRoot: string;
  /** Window in ms to wait for matching unlink↔add pairs. Default 250. */
  correlationWindowMs?: number;
  /** Debounce interval for rapid change events on the same path. Default 300. */
  changeDebounceMs?: number;
}

type PendingUnlink = RawEvent & { quickHash?: string };
type PendingAdd = RawEvent & { quickHash?: string };

async function quickHash(absPath: string): Promise<string | undefined> {
  try {
    const fh = await fsp.open(absPath, "r");
    try {
      const stat = await fh.stat();
      const buf = Buffer.allocUnsafe(Math.min(QUICK_HASH_BYTES, Math.max(0, stat.size)));
      let read = 0;
      if (buf.length > 0) {
        const { bytesRead } = await fh.read(buf, 0, buf.length, 0);
        read = bytesRead;
      }
      const h = crypto.createHash("sha256");
      h.update(buf.subarray(0, read));
      h.update(`|${stat.size}|${stat.mtimeMs}`);
      return h.digest("hex");
    } finally {
      await fh.close();
    }
  } catch {
    return undefined;
  }
}

function isValidInode(ino: number | undefined): boolean {
  return ino !== undefined && Number.isFinite(ino) && ino > 0;
}

function dirnamePosix(p: string): string {
  const i = p.lastIndexOf("/");
  return i <= 0 ? "" : p.slice(0, i);
}

export class EventCorrelator {
  private readonly syncRoot: string;
  private readonly correlationWindowMs: number;
  private readonly changeDebounceMs: number;

  /** Pending unlinks waiting for a matching add. */
  private pendingUnlinks = new Map<string, PendingUnlink>();
  /** Pending adds waiting for flush or matching unlink. */
  private pendingAdds = new Map<string, PendingAdd>();
  /** Per-path debounce timers for change events. */
  private changeTimers = new Map<string, NodeJS.Timeout>();
  /** Queued change events (deduplicated, latest wins). */
  private pendingChanges = new Map<string, RawEvent>();
  /** Timer for the correlation flush window. */
  private flushTimer: NodeJS.Timeout | null = null;
  /** Callback to receive correlated events. */
  private onEvents: ((events: FileEvent[]) => void) | null = null;
  /** Track inodes of recently unlinked files for rename detection. */
  private unlinkInodes = new Map<number, PendingUnlink>();

  constructor(opts: EventCorrelatorOptions) {
    this.syncRoot = path.resolve(opts.syncRoot);
    this.correlationWindowMs = opts.correlationWindowMs ?? 250;
    this.changeDebounceMs = opts.changeDebounceMs ?? 300;
  }

  setListener(fn: (events: FileEvent[]) => void): void {
    this.onEvents = fn;
  }

  /**
   * Feed a raw chokidar event into the correlator.
   * The correlator will buffer, correlate, and emit high-level FileEvent[]
   * to the listener after the correlation window closes.
   */
  async push(type: RawEventType, absPath: string, stats?: fs.Stats): Promise<void> {
    const relKey = normalizeRelKey(absPath, this.syncRoot);
    if (!relKey) return; // skip sync root itself

    const isDirectory = type === "addDir" || type === "unlinkDir";
    const now = Date.now();

    const raw: RawEvent = {
      type,
      absPath,
      relKey,
      timestamp: now,
      inode: stats ? (typeof stats.ino === "bigint" ? Number(stats.ino) : stats.ino) : undefined,
      size: stats?.isFile() ? stats.size : undefined,
      mtimeMs: stats?.mtimeMs,
      isDirectory,
    };

    switch (type) {
      case "unlink":
      case "unlinkDir":
        await this.handleUnlink(raw);
        break;
      case "add":
      case "addDir":
        await this.handleAdd(raw);
        break;
      case "change":
        this.handleChange(raw);
        break;
    }
  }

  private async handleUnlink(raw: RawEvent): Promise<void> {
    // Check if this is an unlink+add on the same path (atomic save).
    // If an add is already pending for this exact path, collapse to MODIFIED.
    if (this.pendingAdds.has(raw.relKey)) {
      const add = this.pendingAdds.get(raw.relKey)!;
      this.pendingAdds.delete(raw.relKey);
      this.emitImmediate([{
        type: "MODIFIED",
        path: raw.relKey,
        timestamp: add.timestamp,
        size: add.size,
        isDirectory: raw.isDirectory,
      }]);
      return;
    }

    // Try to grab inode from the cached state (file is already gone)
    // We stored it from a previous scan or add event
    this.pendingUnlinks.set(raw.relKey, { ...raw });
    if (isValidInode(raw.inode)) {
      this.unlinkInodes.set(raw.inode!, { ...raw });
    }
    this.scheduleFlush();
  }

  private async handleAdd(raw: RawEvent): Promise<void> {
    // Compute quick hash for content-based matching (files only)
    let hash: string | undefined;
    if (!raw.isDirectory) {
      hash = await quickHash(raw.absPath);
    }
    const pending: PendingAdd = { ...raw, quickHash: hash };

    // Check if this add matches a pending unlink on the same path (atomic save)
    if (this.pendingUnlinks.has(raw.relKey)) {
      const unlink = this.pendingUnlinks.get(raw.relKey)!;
      this.pendingUnlinks.delete(raw.relKey);
      if (isValidInode(unlink.inode)) {
        this.unlinkInodes.delete(unlink.inode!);
      }
      // Same path unlink→add = MODIFIED (atomic save pattern)
      this.emitImmediate([{
        type: "MODIFIED",
        path: raw.relKey,
        timestamp: raw.timestamp,
        size: raw.size,
        isDirectory: raw.isDirectory,
      }]);
      return;
    }

    // Try inode-based rename detection
    if (isValidInode(raw.inode) && this.unlinkInodes.has(raw.inode!)) {
      const unlink = this.unlinkInodes.get(raw.inode!)!;
      // Must be same type (both file or both dir)
      if (unlink.isDirectory === raw.isDirectory) {
        this.unlinkInodes.delete(raw.inode!);
        this.pendingUnlinks.delete(unlink.relKey);
        const sameDir = dirnamePosix(unlink.relKey) === dirnamePosix(raw.relKey);
        const evtType: FileEventType = raw.isDirectory
          ? (sameDir ? "DIR_RENAMED" : "DIR_MOVED")
          : (sameDir ? "RENAMED" : "MOVED");
        this.emitImmediate([{
          type: evtType,
          path: raw.relKey,
          oldPath: unlink.relKey,
          timestamp: raw.timestamp,
          size: raw.size,
          isDirectory: raw.isDirectory,
        }]);
        return;
      }
    }

    this.pendingAdds.set(raw.relKey, pending);
    this.scheduleFlush();
  }

  private handleChange(raw: RawEvent): void {
    // Debounce rapid changes on the same path
    const existing = this.changeTimers.get(raw.relKey);
    if (existing) {
      clearTimeout(existing);
    }
    this.pendingChanges.set(raw.relKey, raw);
    const timer = setTimeout(() => {
      this.changeTimers.delete(raw.relKey);
      const ev = this.pendingChanges.get(raw.relKey);
      if (ev) {
        this.pendingChanges.delete(raw.relKey);
        this.emitImmediate([{
          type: "MODIFIED",
          path: ev.relKey,
          timestamp: ev.timestamp,
          size: ev.size,
          isDirectory: ev.isDirectory,
        }]);
      }
    }, this.changeDebounceMs);
    this.changeTimers.set(raw.relKey, timer);
  }

  private scheduleFlush(): void {
    if (this.flushTimer) return;
    this.flushTimer = setTimeout(() => {
      this.flushTimer = null;
      this.flush();
    }, this.correlationWindowMs);
  }

  /**
   * Flush all pending unlinks and adds that were not correlated.
   * Attempts content-hash-based matching as a last resort for rename detection.
   */
  private flush(): void {
    const events: FileEvent[] = [];

    // Try content-hash matching between remaining unlinks and adds
    if (this.pendingUnlinks.size > 0 && this.pendingAdds.size > 0) {
      const unlinksByHash = new Map<string, PendingUnlink[]>();
      for (const [, u] of this.pendingUnlinks) {
        if (u.quickHash) {
          const arr = unlinksByHash.get(u.quickHash) ?? [];
          arr.push(u);
          unlinksByHash.set(u.quickHash, arr);
        }
      }

      for (const [relKey, add] of this.pendingAdds) {
        if (!add.quickHash) continue;
        const matches = unlinksByHash.get(add.quickHash);
        if (!matches || matches.length === 0) continue;

        // Pick the first unmatched unlink with same hash and same type
        const idx = matches.findIndex(
          (u) => u.isDirectory === add.isDirectory && this.pendingUnlinks.has(u.relKey)
        );
        if (idx === -1) continue;

        const unlink = matches[idx]!;
        matches.splice(idx, 1);
        this.pendingUnlinks.delete(unlink.relKey);
        this.pendingAdds.delete(relKey);
        if (isValidInode(unlink.inode)) {
          this.unlinkInodes.delete(unlink.inode!);
        }

        const sameDir = dirnamePosix(unlink.relKey) === dirnamePosix(add.relKey);
        const evtType: FileEventType = add.isDirectory
          ? (sameDir ? "DIR_RENAMED" : "DIR_MOVED")
          : (sameDir ? "RENAMED" : "MOVED");
        events.push({
          type: evtType,
          path: add.relKey,
          oldPath: unlink.relKey,
          timestamp: add.timestamp,
          size: add.size,
          isDirectory: add.isDirectory,
        });
      }
    }

    // Remaining unlinks → DELETED
    for (const [, u] of this.pendingUnlinks) {
      events.push({
        type: u.isDirectory ? "DIR_DELETED" : "DELETED",
        path: u.relKey,
        timestamp: u.timestamp,
        isDirectory: u.isDirectory,
      });
    }
    this.pendingUnlinks.clear();
    this.unlinkInodes.clear();

    // Remaining adds → CREATED
    for (const [, a] of this.pendingAdds) {
      events.push({
        type: a.isDirectory ? "DIR_CREATED" : "CREATED",
        path: a.relKey,
        timestamp: a.timestamp,
        size: a.size,
        isDirectory: a.isDirectory,
      });
    }
    this.pendingAdds.clear();

    if (events.length > 0) {
      this.emitImmediate(events);
    }
  }

  private emitImmediate(events: FileEvent[]): void {
    if (this.onEvents && events.length > 0) {
      this.onEvents(events);
    }
  }

  /** Force-flush all pending state (call on shutdown). */
  drain(): void {
    if (this.flushTimer) {
      clearTimeout(this.flushTimer);
      this.flushTimer = null;
    }
    // Flush pending change debounces
    for (const [relKey, timer] of this.changeTimers) {
      clearTimeout(timer);
      const ev = this.pendingChanges.get(relKey);
      if (ev) {
        this.emitImmediate([{
          type: "MODIFIED",
          path: ev.relKey,
          timestamp: ev.timestamp,
          size: ev.size,
          isDirectory: ev.isDirectory,
        }]);
      }
    }
    this.changeTimers.clear();
    this.pendingChanges.clear();
    this.flush();
  }
}
