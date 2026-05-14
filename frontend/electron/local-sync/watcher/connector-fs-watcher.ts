import * as fs from 'fs';
import * as path from 'path';
import * as chokidar from 'chokidar';
import { EventCorrelator, type ChokidarEventName } from './event-correlator';
import { BatchDispatcher, type BatchMeta } from '../transport/batch-dispatcher';
import { expandWatchEventsForReplay, type WatchEvent } from './replay-event-expander';
import {
  WatcherStateStore,
  scanSyncRoot,
  contentQuickHash,
  normalizeRelKey,
  type FileSnapshotEntry,
  type FileSnapshotMap,
} from '../persistence/watcher-state-store';
import { IGNORED_PATTERNS } from './ignored-patterns';

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
 * (handling rename/move via inode + quickHash), deduplicates within batches, and
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
  private syntheticSuppressionWindowMs: number;
  private suppressedEventKeys: Map<string, number>;

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
    this.syntheticSuppressionWindowMs = 1500;
    this.suppressedEventKeys = new Map();

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
        if (ev.isDirectory) return false;
        const prev = this.stateStore.getSnapshot().files[ev.relKey];
        if (!prev || prev.isDirectory || !prev.quickHash) return false;
        const cur = await contentQuickHash(ev.absPath);
        return cur !== undefined && cur === prev.quickHash;
      },
      getPreviousFileEntry: (relKey: string) => this.stateStore.getSnapshot().files[relKey],
    });

    this.correlator.setListener((events: WatchEvent[]) => {
      const filtered = this.dropSuppressedEvents(events);
      const dispatchable = this.expandForDispatch(filtered);
      if (dispatchable.length > 0) {
        this.noteSyntheticFollowupSuppressions(dispatchable);
        this.applyEventsToState(dispatchable).catch(() => { /* ignore */ });
        this.dispatcher.push(dispatchable);
      }
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

  private expandForDispatch(events: WatchEvent[], filesSnapshot?: FileSnapshotMap): WatchEvent[] {
    if (!events || events.length === 0) return [];
    if (!events.some((event) => event.isDirectory)) return events;
    return expandWatchEventsForReplay(
      events,
      filesSnapshot || this.stateStore.getSnapshot().files,
    );
  }

  private dropSuppressedEvents(events: WatchEvent[]): WatchEvent[] {
    if (!events || events.length === 0) return [];
    const now = Date.now();
    for (const [key, expiresAt] of this.suppressedEventKeys) {
      if (expiresAt <= now) this.suppressedEventKeys.delete(key);
    }
    return events.filter((event) => {
      const key = `${event.type}:${event.path}`;
      const expiresAt = this.suppressedEventKeys.get(key);
      if (!expiresAt || expiresAt <= now) return true;
      return false;
    });
  }

  private noteSyntheticFollowupSuppressions(events: WatchEvent[]): void {
    if (!events || events.length === 0) return;
    const expiresAt = Date.now() + this.syntheticSuppressionWindowMs;
    for (const event of events) {
      if (!event || (event.type !== 'RENAMED' && event.type !== 'MOVED')) continue;
      if (event.oldPath) this.suppressedEventKeys.set(`DELETED:${event.oldPath}`, expiresAt);
      if (event.path) this.suppressedEventKeys.set(`CREATED:${event.path}`, expiresAt);
    }
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
    const quickHash = isDirectory ? undefined : await contentQuickHash(absPath);

    this.stateStore.getSnapshot().files[relKey] = {
      inode,
      size,
      mtimeMs,
      isDirectory,
      quickHash,
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
        const quickHash = await contentQuickHash(absPath);
        const entry: FileSnapshotEntry = {
          inode,
          size: stats.size,
          mtimeMs: stats.mtimeMs,
          isDirectory: false,
          quickHash,
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
      const dispatchable = this.expandForDispatch(offlineEvents, replayFiles);
      if (dispatchable.length > 0) {
        this.log(`Found ${dispatchable.length} change(s) since last run.`);
        this.dispatcher.push(dispatchable, { source: 'reconcile' });
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
      awaitWriteFinish: { stabilityThreshold: 1500, pollInterval: 200 },
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

    this.watcher.on('ready', async () => {
      this.ready = true;
      if (!hasPreviousState) {
        this.log('Initial scan complete. Capturing baseline state...');
        try {
          const scan = await scanSyncRoot(this.rootPath, this.scanOptions());
          this.stateStore.applyScan(scan);
          this.stateStore.flushSave();
          this.log(`Baseline captured: ${scan.size} entries tracked.`);
        } catch (err) {
          this.log(`Baseline scan error: ${err instanceof Error ? err.message : String(err)}`);
        }
      } else {
        this.log('Watcher ready.');
      }
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
    await this.correlator.drain();
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
    const dispatchable = this.expandForDispatch(events, replayFiles);
    if (dispatchable.length > 0) this.dispatcher.push(dispatchable, { source: 'reconcile' });
    // Drain the dispatcher synchronously so callers (e.g. runScheduledTick)
    // can rely on every reconcile event having reached onBatch/the journal
    // before this returns. Without this, the 1s scheduleFlush timer would
    // delay rescan deltas under SCHEDULED until the next tick.
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
   */
  async drainLiveEvents(): Promise<void> {
    await this.correlator.drain();
    await this.dispatcher.flush();
  }
}
