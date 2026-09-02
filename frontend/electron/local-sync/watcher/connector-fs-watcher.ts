import * as fs from 'fs';
import * as path from 'path';
import * as chokidar from 'chokidar';
import { EventCorrelator, type ChokidarEventName } from './event-correlator';
import { BatchDispatcher, type BatchMeta } from '../transport/batch-dispatcher';
import { expandWatchEventsForReplay, type WatchEvent } from './replay-event-expander';
import {
  WatcherStateStore,
  scanSyncRoot,
  contentFileHash,
  normalizeRelKey,
  type FileSnapshotEntry,
  type FileSnapshotMap,
} from '../persistence/watcher-state-store';
import { IGNORED_PATTERNS } from './ignored-patterns';

/**
 * chokidar holds a file's `add` back until its size has been stable this long,
 * so any window that has to outlive a freshly-moved file's `add` must exceed it.
 */
const AWAIT_WRITE_FINISH_STABILITY_MS = 1500;
const AWAIT_WRITE_FINISH_POLL_MS = 200;
/** Slack on top of the derived move-ledger lifetime. */
const MOVE_LEDGER_MARGIN_MS = 1000;
/** A move is one entry, so this only bounds pathological churn. */
const MAX_MOVE_LEDGER_ENTRIES = 64;

/**
 * One directory move we have already reported per-file. `suffixes` holds the
 * children we synthesized events for, so the raw chokidar events for those same
 * children can be recognised and dropped without swallowing a genuinely new
 * file that appears inside the moved directory afterwards.
 */
interface MoveLedgerEntry {
  oldPrefix: string;
  newPrefix: string;
  suffixes: Set<string>;
  expiresAt: number;
}

/** '' when path *is* prefix, the child suffix when under it, null otherwise. */
function suffixUnder(prefix: string, candidate: string): string | null {
  if (!prefix) return null;
  if (candidate === prefix) return '';
  return candidate.startsWith(`${prefix}/`) ? candidate.slice(prefix.length + 1) : null;
}

export interface ConnectorFsWatcherStatus {
  running: boolean;
  rootPath: string;
  connectorId: string;
  trackedFiles: number;
  pendingEvents: number;
}

export interface BatchPayload {
  connectorId: string;
  batchId: string;
  timestamp: number;
  source: string;
  events: WatchEvent[];
}

export type BatchHandler = (payload: BatchPayload) => Promise<unknown> | unknown;

export interface ConnectorFsWatcherArgs {
  connectorId: string;
  rootPath: string;
  baseDir: string;
  onBatch?: BatchHandler;
  flushMs?: number;
  maxBatchSize?: number;
  includeSubfolders?: boolean;
  correlationWindowMs?: number;
  changeDebounceMs?: number;
  stateSyncDebounceMs?: number;
  usePolling?: boolean;
  pollInterval?: number;
  log?: (msg: string) => void;
  /**
   * Surface chokidar errors to the caller. Without this, `this.watcher.on('error', ...)`
   * just logs and the user has no signal that the watcher has died (ENOSPC, EMFILE,
   * EACCES, network-share unmount). The manager attaches a callback that updates
   * runtime.lastError so the UI can show it.
   */
  onWatcherError?: (err: Error) => void;
}

/**
 * Watches `rootPath` with chokidar, correlates raw events into high-level FileEvents
 * (handling rename/move via inode + full-content sha256), deduplicates within batches, and
 * emits batches via onBatch. Performs startup reconciliation against a persisted
 * per-connector watcher_state.*.json so offline changes are captured.
 */
export class ConnectorFsWatcher {
  connectorId: string;
  rootPath: string;
  baseDir: string;
  onBatch?: BatchHandler;
  onWatcherError?: (err: Error) => void;
  includeSubfolders: boolean;
  log: (msg: string) => void;
  usePolling: boolean;
  pollInterval: number;
  stateSyncDebounceMs: number;

  private watcher: chokidar.FSWatcher | null;
  private running: boolean;
  private ready: boolean;
  private stateSyncTimer: NodeJS.Timeout | null;
  private moveLedgerTtlMs: number;
  private moveLedger: MoveLedgerEntry[];
  /** Serialises listener bodies so two emits cannot interleave their awaits
   * and observe each other's half-applied state. */
  private listenerChain: Promise<void>;

  stateStore: WatcherStateStore;
  dispatcher: BatchDispatcher;
  correlator: EventCorrelator;

  constructor({
    connectorId,
    rootPath,
    baseDir,
    onBatch,
    onWatcherError,
    flushMs,
    maxBatchSize,
    includeSubfolders,
    correlationWindowMs,
    changeDebounceMs,
    stateSyncDebounceMs,
    usePolling,
    pollInterval,
    log,
  }: ConnectorFsWatcherArgs) {
    if (!connectorId) throw new Error('connectorId is required');
    if (!rootPath) throw new Error('rootPath is required');
    if (!baseDir) throw new Error('baseDir is required');
    this.connectorId = connectorId;
    this.rootPath = path.resolve(rootPath);
    this.baseDir = baseDir;
    this.onBatch = onBatch;
    this.onWatcherError = onWatcherError;
    this.includeSubfolders = includeSubfolders !== false;
    this.log = log || ((msg: string) => console.log(`[local-sync:${connectorId}]`, msg));
    this.usePolling = usePolling === true;
    this.pollInterval = typeof pollInterval === 'number' && pollInterval > 0 ? pollInterval : 1000;
    // This is only a fallback snapshot refresh. Event handling updates state
    // first; running the rescan too soon can erase the "previous" hash before
    // chokidar's awaitWriteFinish-delayed change/add events reach us.
    this.stateSyncDebounceMs = typeof stateSyncDebounceMs === 'number' && stateSyncDebounceMs >= 0 ? stateSyncDebounceMs : 5000;

    this.watcher = null;
    this.running = false;
    this.ready = false;
    this.stateSyncTimer = null;
    this.moveLedger = [];
    this.listenerChain = Promise.resolve();

    this.stateStore = new WatcherStateStore({
      baseDir: this.baseDir,
      syncRoot: this.rootPath,
      connectorInstanceId: this.connectorId,
    });

    this.dispatcher = new BatchDispatcher(
      async (events: WatchEvent[], meta: BatchMeta) => {
        if (!this.onBatch) return;
        await this.onBatch({
          connectorId: this.connectorId,
          batchId: meta.batchId,
          timestamp: Date.now(),
          source: meta.source,
          events,
        });
      },
      {
        maxBatchSize: maxBatchSize != null ? maxBatchSize : 50,
        flushIntervalMs: flushMs != null ? flushMs : 1000,
        onError: (err: unknown) => this.log(
          `Batch dispatch error: ${err instanceof Error ? err.message : String(err)}`,
        ),
      }
    );

    this.correlator = new EventCorrelator({
      syncRoot: this.rootPath,
      correlationWindowMs,
      changeDebounceMs,
      shouldSuppressModifiedChange: async (ev) => {
        if (ev.isDirectory) return { suppress: false };
        // Hashed once here and handed back so the caller can stamp the
        // emitted MODIFIED event with it instead of re-reading the file.
        const cur = await contentFileHash(ev.absPath);
        const prev = this.stateStore.getSnapshot().files[ev.relKey];
        const suppress = Boolean(
          prev && !prev.isDirectory && prev.sha256 && cur !== undefined && cur === prev.sha256,
        );
        return { suppress, sha256: cur };
      },
      getPreviousFileEntry: (relKey: string) => this.stateStore.getSnapshot().files[relKey],
    });

    // Derived rather than fixed: every one of these delays sits between a
    // directory move and the raw child events it provokes, so a lifetime
    // shorter than their sum can never see the events it exists to drop.
    this.moveLedgerTtlMs = AWAIT_WRITE_FINISH_STABILITY_MS
      + AWAIT_WRITE_FINISH_POLL_MS
      + this.correlator.correlationWindowMs
      + this.correlator.unlinkCorrelationWindowMs
      + MOVE_LEDGER_MARGIN_MS;

    this.correlator.setListener((events: WatchEvent[]) => {
      this.listenerChain = this.listenerChain.then(async () => {
        // Expansion first: the children a DIR_DELETED expands into are created
        // here, so a filter running before it could never see them.
        const dispatchable = this.filterAndNote(await this.expandForDispatch(events));
        if (dispatchable.length === 0) return;
        // Awaited: the correlator reads this state back to recover a pending
        // unlink's inode and hash, and racing that read is what made the
        // duplicate-on-move bug intermittent rather than constant.
        await this.applyEventsToState(dispatchable);
        this.dispatcher.push(dispatchable);
      }).catch(() => { /* a failed emit must not wedge the chain */ });
    });
  }

  private scanOptions() {
    return {
      includeSubfolders: this.includeSubfolders,
      previousByRelPath: new Map(Object.entries(this.stateStore.getSnapshot().files)),
      ignoredPatterns: IGNORED_PATTERNS,
    };
  }

  private scheduleStateSyncFromDisk(): void {
    if (this.stateSyncTimer) clearTimeout(this.stateSyncTimer);
    this.stateSyncTimer = setTimeout(() => {
      this.stateSyncTimer = null;
      this.syncStateFromDisk().catch(() => { /* ignore */ });
    }, this.stateSyncDebounceMs);
  }

  private async syncStateFromDisk(): Promise<void> {
    try {
      const scan = await scanSyncRoot(this.rootPath, this.scanOptions());
      this.stateStore.applyScan(scan);
      this.stateStore.flushSave();
    } catch (err) {
      this.log(`State rescan error: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  private async expandForDispatch(events: WatchEvent[], filesSnapshot?: FileSnapshotMap): Promise<WatchEvent[]> {
    if (!events || events.length === 0) return [];
    if (!events.some((event) => event.isDirectory)) return events;
    return expandWatchEventsForReplay(
      events,
      filesSnapshot || this.stateStore.getSnapshot().files,
      this.rootPath,
    );
  }

  /**
   * Drop the events a directory move provokes twice, then record the move so
   * the *next* wave can be recognised.
   *
   * Moving a directory makes us report every child once from the expansion of
   * the directory event, and makes chokidar report each child's own unlink/add
   * a second or two later. Without this the backend sees both descriptions of
   * the same move and ends up with two records per file.
   *
   * Order matters: filtering runs before recording, so the first wave (which
   * establishes the entry) passes through and only repeats are dropped.
   */
  private filterAndNote(events: WatchEvent[]): WatchEvent[] {
    if (!events || events.length === 0) return [];
    const kept = this.dropSuppressedEvents(events);
    this.noteDirectoryMoves(kept);
    return kept;
  }

  private pruneMoveLedger(now: number): void {
    this.moveLedger = this.moveLedger.filter((entry) => entry.expiresAt > now);
    if (this.moveLedger.length > MAX_MOVE_LEDGER_ENTRIES) {
      this.moveLedger.sort((a, b) => a.expiresAt - b.expiresAt);
      this.moveLedger = this.moveLedger.slice(-MAX_MOVE_LEDGER_ENTRIES);
    }
  }

  /**
   * True when this event merely restates a move already reported. Sliding the
   * entry's expiry on each hit matters for big directories, where chokidar's
   * per-file stability wait spreads the raw events over far longer than any
   * fixed lifetime.
   */
  private isAlreadyReported(event: WatchEvent, now: number): boolean {
    for (const entry of this.moveLedger) {
      let matched = false;
      switch (event.type) {
        case 'MOVED':
        case 'RENAMED':
        case 'DIR_MOVED':
        case 'DIR_RENAMED': {
          if (!event.oldPath) break;
          const oldSuffix = suffixUnder(entry.oldPrefix, event.oldPath);
          // A pair whose halves map through this move by the same suffix *is*
          // this move -- no other file can produce it -- so there is no need to
          // consult the recorded children.
          matched = oldSuffix !== null && suffixUnder(entry.newPrefix, event.path) === oldSuffix;
          break;
        }
        case 'CREATED':
        case 'DIR_CREATED': {
          const suffix = suffixUnder(entry.newPrefix, event.path);
          // Only children we actually reported: a genuinely new file dropped
          // into the moved directory must still reach the backend.
          matched = suffix !== null && entry.suffixes.has(suffix);
          break;
        }
        case 'DELETED':
        case 'DIR_DELETED': {
          const suffix = suffixUnder(entry.oldPrefix, event.path);
          matched = suffix !== null && entry.suffixes.has(suffix);
          break;
        }
        default:
          // MODIFIED is deliberately absent: suppressing it would discard a
          // real edit made moments after a move, and letting a redundant one
          // through costs nothing -- the backend skips same-revision updates.
          break;
      }
      if (matched) {
        entry.expiresAt = now + this.moveLedgerTtlMs;
        return true;
      }
    }
    return false;
  }

  private dropSuppressedEvents(events: WatchEvent[]): WatchEvent[] {
    if (!events || events.length === 0) return [];
    const now = Date.now();
    this.pruneMoveLedger(now);
    if (this.moveLedger.length === 0) return events;
    return events.filter((event) => !this.isAlreadyReported(event, now));
  }

  private noteDirectoryMoves(events: WatchEvent[]): void {
    if (!events || events.length === 0) return;
    const now = Date.now();
    const touched: MoveLedgerEntry[] = [];

    for (const event of events) {
      if (event.type !== 'DIR_MOVED' && event.type !== 'DIR_RENAMED') continue;
      if (!event.oldPath || !event.path) continue;
      const oldPrefix = event.oldPath;
      let entry = this.moveLedger.find(
        (e) => e.oldPrefix === oldPrefix && e.newPrefix === event.path,
      );
      if (!entry) {
        entry = { oldPrefix, newPrefix: event.path, suffixes: new Set<string>(), expiresAt: 0 };
        this.moveLedger.push(entry);
      }
      entry.expiresAt = now + this.moveLedgerTtlMs;
      touched.push(entry);
    }
    if (touched.length === 0) return;

    for (const event of events) {
      if (event.type !== 'MOVED' && event.type !== 'RENAMED') continue;
      if (!event.oldPath) continue;
      for (const entry of touched) {
        const suffix = suffixUnder(entry.oldPrefix, event.oldPath);
        if (suffix !== null && suffixUnder(entry.newPrefix, event.path) === suffix) {
          entry.suffixes.add(suffix);
          break;
        }
      }
    }
    this.pruneMoveLedger(now);
  }

  private async captureRawState(
    eventName: string,
    absPath: string,
    stats?: fs.Stats,
  ): Promise<void> {
    if (!stats) return;
    if (!['add', 'addDir'].includes(eventName)) return;
    const relKey = normalizeRelKey(absPath, this.rootPath);
    if (!relKey) return;

    const isDirectory = typeof stats.isDirectory === 'function' ? stats.isDirectory() : eventName === 'addDir';
    const inode = typeof stats.ino === 'bigint' ? Number(stats.ino) : stats.ino;
    const size = !isDirectory && typeof stats.size === 'number' ? stats.size : 0;
    const mtimeMs = typeof stats.mtimeMs === 'number' ? stats.mtimeMs : Date.now();
    const sha256 = isDirectory ? undefined : await contentFileHash(absPath);

    this.stateStore.getSnapshot().files[relKey] = {
      inode,
      size,
      mtimeMs,
      isDirectory,
      sha256,
    };
    this.stateStore.scheduleSave();
  }

  private async applyEventsToState(events: WatchEvent[]): Promise<void> {
    if (!events || events.length === 0) return;
    let touched = false;

    for (const event of events) {
      if (!event || event.isDirectory) continue;
      const nextPath = String(event.path || '').trim();
      if (!nextPath) continue;
      if (event.oldPath) delete this.stateStore.getSnapshot().files[String(event.oldPath)];

      if (event.type === 'DELETED') {
        delete this.stateStore.getSnapshot().files[nextPath];
        touched = true;
        continue;
      }

      const absPath = path.resolve(this.rootPath, nextPath);
      try {
        const stats = await fs.promises.lstat(absPath);
        if (!stats.isFile()) continue;
        const inode = typeof stats.ino === 'bigint' ? Number(stats.ino) : stats.ino;
        // The event already carries a freshly computed hash from the
        // correlator — reuse it rather than reading the file a second time.
        const sha256 = event.sha256 !== undefined ? event.sha256 : await contentFileHash(absPath);
        const entry: FileSnapshotEntry = {
          inode,
          size: stats.size,
          mtimeMs: stats.mtimeMs,
          isDirectory: false,
          sha256,
        };
        this.stateStore.getSnapshot().files[nextPath] = entry;
        touched = true;
      } catch {
        delete this.stateStore.getSnapshot().files[nextPath];
        touched = true;
      }
    }

    if (touched) this.stateStore.scheduleSave();
  }

  async start(): Promise<void> {
    if (this.running) return;
    if (!fs.existsSync(this.rootPath)) {
      throw new Error(`Local sync root folder does not exist: ${this.rootPath}`);
    }
    const st = fs.statSync(this.rootPath);
    if (!st.isDirectory()) {
      throw new Error(`Local sync root must be a directory: ${this.rootPath}`);
    }

    this.running = true;
    this.log(`Starting file watcher on: ${this.rootPath}`);

    this.stateStore.load();
    const prevState = this.stateStore.getSnapshot();
    const hasPreviousState = Object.keys(prevState.files).length > 0;

    if (hasPreviousState) {
      this.log('Performing startup reconciliation...');
      const replayFiles = { ...prevState.files };
      const currentScan = await scanSyncRoot(this.rootPath, this.scanOptions());
      const offlineEvents = this.stateStore.commitReconcile(currentScan);
      const dispatchable = this.filterAndNote(
        await this.expandForDispatch(offlineEvents, replayFiles),
      );
      if (dispatchable.length > 0) {
        this.log(`Found ${dispatchable.length} change(s) since last run.`);
        this.dispatcher.push(dispatchable, { source: 'reconcile' });
        // Drain synchronously so the caller (LocalSyncManager.start) can rely
        // on every reconcile event having reached onBatch/the journal before
        // this returns — it replays right after start() resolves instead of
        // waiting for the 1s scheduleFlush timer or the next scheduled tick.
        await this.dispatcher.flush();
      }
    } else {
      // First-ever start for this connector (or watcher_state was lost):
      // seed the backend from the current disk snapshot as plain CREATED
      // upserts instead of relying on a destructive REPLACE. The backend
      // upserts idempotently by deterministic external_record_id, so this is
      // safe even if some of these paths already exist there.
      this.log('No previous state found. Seeding from current disk contents...');
      const currentScan = await scanSyncRoot(this.rootPath, this.scanOptions());
      const seedEvents: WatchEvent[] = [];
      for (const [relPath, entry] of currentScan) {
        if (entry.isDirectory) continue;
        seedEvents.push({
          type: 'CREATED',
          path: relPath,
          timestamp: Date.now(),
          size: entry.size,
          isDirectory: false,
          sha256: entry.sha256,
        });
      }
      this.stateStore.applyScan(currentScan);
      this.stateStore.flushSave();
      if (seedEvents.length > 0) {
        this.log(`Seeding ${seedEvents.length} file(s) from disk.`);
        this.dispatcher.push(seedEvents, { source: 'reconcile' });
        await this.dispatcher.flush();
      }
    }

    const depth = this.includeSubfolders ? undefined : 0;
    this.watcher = chokidar.watch(this.rootPath, {
      ignored: IGNORED_PATTERNS as unknown as RegExp,
      persistent: true,
      ignoreInitial: true,
      followSymlinks: false,
      depth,
      alwaysStat: true,
      usePolling: this.usePolling,
      interval: this.pollInterval,
      // 200 ms was too aggressive for cp/rsync of multi-GB files over a
      // network drive — the writer is still flushing when the watcher
      // fires CREATED, the dispatcher reads a half-written file, and the
      // stat-time size disagrees with the bytes we end up uploading.
      // 1500 ms quiet window catches cold-cached writes without making
      // small-file edits feel sluggish.
      awaitWriteFinish: {
        stabilityThreshold: AWAIT_WRITE_FINISH_STABILITY_MS,
        pollInterval: AWAIT_WRITE_FINISH_POLL_MS,
      },
      ignorePermissionErrors: true,
      atomic: 200,
    });

    this.watcher.on('all', async (eventName, filePath, stats) => {
      if (!this.ready) return;
      if (!['add', 'addDir', 'unlink', 'unlinkDir', 'change'].includes(eventName)) return;
      await this.captureRawState(eventName, filePath, stats).catch(() => { /* ignore */ });
      this.scheduleStateSyncFromDisk();
      this.correlator.push(eventName as ChokidarEventName, filePath, stats).catch(() => { /* ignore */ });
    });

    this.watcher.on('ready', () => {
      this.ready = true;
      this.log('Watcher ready.');
    });

    this.watcher.on('error', (err) => {
      const e = err instanceof Error ? err : new Error(String(err));
      this.log(`Watcher error: ${e.message}`);
      try {
        if (this.onWatcherError) this.onWatcherError(e);
      } catch { /* listener errors must not crash the watcher loop */ }
    });
  }

  async stop(): Promise<void> {
    if (!this.running) return;
    this.log('Stopping file watcher...');
    if (this.stateSyncTimer) { clearTimeout(this.stateSyncTimer); this.stateSyncTimer = null; }
    // Forced: syncStateFromDisk() below rewrites state to match disk, so an
    // unlink left pending here becomes a deletion no event ever reports.
    await this.correlator.drain(true);
    await this.dispatcher.flush();
    if (this.watcher) {
      try { await this.watcher.close(); } catch { /* ignore */ }
      this.watcher = null;
    }
    await this.syncStateFromDisk();
    this.stateStore.flushSave();
    this.running = false;
    this.ready = false;
    this.log('File watcher stopped.');
  }

  getStatus(): ConnectorFsWatcherStatus {
    const state = this.stateStore.getSnapshot();
    return {
      running: this.running,
      rootPath: this.rootPath,
      connectorId: this.connectorId,
      trackedFiles: Object.keys(state.files).length,
      pendingEvents: this.dispatcher.pending,
    };
  }

  async rescan(): Promise<WatchEvent[]> {
    const replayFiles = { ...this.stateStore.getSnapshot().files };
    const scan = await scanSyncRoot(this.rootPath, this.scanOptions());
    const events = this.stateStore.commitReconcile(scan);
    this.stateStore.flushSave();
    const dispatchable = this.filterAndNote(
      await this.expandForDispatch(events, replayFiles),
    );
    if (dispatchable.length > 0) this.dispatcher.push(dispatchable, { source: 'reconcile' });
    // Drain synchronously so callers can rely on every reconcile event having
    // reached onBatch/the journal before this returns; the 1s scheduleFlush
    // timer would otherwise hold them back until after the current pull page.
    await this.dispatcher.flush();
    return dispatchable;
  }

  /**
   * Push any live events buffered in the correlator and dispatcher all the
   * way through to onBatch (and therefore the journal). Called at the start
   * of a scheduled tick so a change made shortly before the tick isn't lost
   * in the correlator's 250ms or the dispatcher's 1000ms window — without
   * this drain, rescan() finds no diff (live events already updated state)
   * and replay() finds nothing in the journal yet, so the change defers to
   * the *next* tick.
   *
   * Unlinks still inside the correlation window are deliberately left pending:
   * forcing them would turn a rename that happens to straddle a pull into
   * DELETE + CREATE, destroying the record's vertex and its permissions and
   * indexing history. They correlate normally and land on the next page, which
   * the pull protocol already tolerates — the server asking for the next page
   * is what acks this one.
   */
  async drainLiveEvents(): Promise<void> {
    await this.correlator.drain(false);
    await this.dispatcher.flush();
  }
}
