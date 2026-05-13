import * as fs from 'fs';
import * as path from 'path';
import { ConnectorFsWatcher, type BatchPayload } from './watcher/connector-fs-watcher';
import {
  LocalSyncJournal,
  type ConnectorMeta,
  type ScheduledConfig,
} from './persistence/journal';
import {
  dispatchFileEventBatch as defaultDispatchFileEventBatch,
  type DispatchFileEventBatchArgs,
} from './transport/file-event-dispatcher';
import { expandWatchEventsForReplay, type WatchEvent } from './watcher/replay-event-expander';
import {
  connectorFileSegment,
  scanSyncRoot,
  type FileSnapshotMap,
} from './persistence/watcher-state-store';
import { scheduleCrawlingManagerJob } from './transport/crawling-manager-client';

const RETRY_BASE_MS = 5_000;
const RETRY_MAX_MS = 5 * 60_000;
const FULL_SYNC_MODE_REPLACE = 'replace' as const;
const RECOVERY_MODE_REPLAY_ONLY = 'replay-only' as const;
// Cap per-batch retries so a permanent 4xx (auth/permission/schema rejection)
// doesn't wedge the journal forever. After this many attempts the batch is
// quarantined; replay skips it and proceeds to drain the rest of the journal.
const MAX_BATCH_ATTEMPTS = 8;

type RecoveryMode = typeof FULL_SYNC_MODE_REPLACE | typeof RECOVERY_MODE_REPLAY_ONLY;

export interface ReplayResult {
  replayedBatches: number;
  replayedEvents: number;
  skippedBatches: number;
}

export type DispatchFn = (args: DispatchFileEventBatchArgs) => Promise<unknown>;

export interface LocalSyncManagerOptions {
  app: Pick<Electron.App, 'getPath'>;
  onStatusChange?: (status: ConnectorStatus) => void;
  dispatchFileEventBatch?: DispatchFn;
}

export interface StartArgs {
  connectorId: string;
  connectorName?: string;
  rootPath: string;
  apiBaseUrl: string;
  accessToken: string;
  includeSubfolders?: boolean;
  connectorDisplayType?: string;
  syncStrategy?: 'MANUAL' | 'SCHEDULED';
  scheduledConfig?: ScheduledConfig;
}

interface RuntimeState {
  connectorId: string;
  connectorName?: string;
  rootPath: string;
  normalizedRootPath: string;
  apiBaseUrl: string;
  accessToken: string;
  connectorDisplayType?: string;
  syncStrategy: 'MANUAL' | 'SCHEDULED';
  scheduledConfig: ScheduledConfig | null;
  watcher: ConnectorFsWatcher | null;
  watcherState: 'starting' | 'watching' | 'stopped';
  lastError: string | null;
  scheduleTimer: NodeJS.Timeout | null;
  startSignature: string;
  // Set true at the top of stop() so processBatch (called from the watcher's
  // drain on stop) journals the events but skips network dispatch. Without
  // this, app quit can block for up to 30s per pending batch waiting on a
  // dispatch that the next session will replay anyway.
  shuttingDown: boolean;
}

export interface ConnectorStatus {
  connectorId: string;
  watcherState: 'starting' | 'watching' | 'stopped';
  rootPath: string | null;
  lastError: string | null;
  trackedFiles: number | null;
  pendingEvents: number | null;
  lastAckBatchId: string | null;
  lastRecordedBatchId: string | null;
  syncStrategy: 'MANUAL' | 'SCHEDULED';
  scheduledConfig: ScheduledConfig | null;
  pendingCount: number;
  failedCount: number;
  syncedCount: number;
  quarantinedCount: number;
  lastBatchId: string | null;
  lastAckAt: number | null;
}

interface RootPathLock {
  connectorId: string;
  connectorName?: string;
}

function loadWatcherStateFiles(baseDir: string, connectorId: string): FileSnapshotMap {
  const p = path.join(baseDir, `watcher_state.${connectorFileSegment(connectorId)}.json`);
  if (!fs.existsSync(p)) return {};
  try {
    const raw = JSON.parse(fs.readFileSync(p, 'utf8'));
    return (raw && raw.files) || {};
  } catch {
    return {};
  }
}

/** Matches ConnectorFsWatcher.applyFilters for files. */
function buildFullSyncSignature(meta: ConnectorMeta | null): string {
  if (!meta) return '';
  return JSON.stringify({
    rootPath: path.resolve(String(meta.rootPath || '')),
    apiBaseUrl: String(meta.apiBaseUrl || ''),
    includeSubfolders: meta.includeSubfolders !== false,
  });
}

function normalizeSyncRootPath(rootPath: string): string {
  const resolved = path.resolve(String(rootPath || ''));
  try {
    return fs.realpathSync.native(resolved);
  } catch {
    return resolved;
  }
}

interface RuntimeSignatureArgs {
  rootPath: string;
  apiBaseUrl: string;
  includeSubfolders?: boolean;
  connectorDisplayType?: string;
  syncStrategy?: 'MANUAL' | 'SCHEDULED';
  scheduledConfig?: ScheduledConfig;
}

/**
 * Identity for an in-flight runtime. Includes everything that requires
 * recreating the watcher or scheduled timer; **excludes** accessToken so
 * routine token refreshes don't tear down sync. Renderer effects often refire
 * (Zustand connector list updates change array refs), so start() being called
 * with an unchanged config must be a no-op — otherwise the scheduled timer is
 * reset on every refire and never fires.
 */
function buildRuntimeSignature(args: RuntimeSignatureArgs): string {
  const sched = args.syncStrategy === 'SCHEDULED' && args.scheduledConfig
    ? {
        intervalMinutes: Number(args.scheduledConfig.intervalMinutes) || 0,
        timezone: args.scheduledConfig.timezone || null,
      }
    : null;
  return JSON.stringify({
    rootPath: path.resolve(String(args.rootPath || '')),
    apiBaseUrl: String(args.apiBaseUrl || ''),
    includeSubfolders: args.includeSubfolders !== false,
    connectorDisplayType: String(args.connectorDisplayType || ''),
    syncStrategy: args.syncStrategy === 'SCHEDULED' ? 'SCHEDULED' : 'MANUAL',
    scheduledConfig: sched,
  });
}

export class LocalSyncManager {
  app: Pick<Electron.App, 'getPath'>;
  onStatusChange?: (status: ConnectorStatus) => void;
  dispatchFileEventBatch: DispatchFn;
  baseDir: string;
  journal: LocalSyncJournal;
  private runtimes: Map<string, RuntimeState>;
  private retryTimers: Map<string, NodeJS.Timeout>;
  private retryAttempts: Map<string, number>;
  private retryModes: Map<string, RecoveryMode>;
  private lastReplaceSyncSignature: Map<string, string>;
  private fullSyncInFlight: Map<string, Promise<void>>;
  private rootPathStartLocks: Map<string, RootPathLock>;
  private replayInFlight: Map<string, Promise<ReplayResult>>;
  // Per-connector serial chain: any replay or full-sync queues behind the
  // previous op so the two never run concurrently. Without this, a scheduled
  // replay could complete just before a REPLACE full-sync resets the backend
  // and wiped its work — duplicate uploads + double-fired downstream side
  // effects (chunking, embedding). Same-kind calls still coalesce via the
  // *InFlight maps; cross-kind calls serialize through opChain.
  private opChain: Map<string, Promise<unknown>>;
  private tickInFlight: Map<string, Promise<void>>;

  constructor({ app, onStatusChange, dispatchFileEventBatch }: LocalSyncManagerOptions) {
    this.app = app;
    this.onStatusChange = onStatusChange;
    this.dispatchFileEventBatch = dispatchFileEventBatch || defaultDispatchFileEventBatch;
    this.baseDir = path.join(this.app.getPath('userData'), 'local-sync-journal');
    this.journal = new LocalSyncJournal(this.baseDir);
    this.runtimes = new Map();
    // Retry scheduling keyed on connectorId so it survives even when the
    // renderer hasn't (yet) called start() — i.e. during offline recovery on
    // app launch before the window mounts the connector UI.
    this.retryTimers = new Map();
    this.retryAttempts = new Map();
    this.retryModes = new Map();
    this.lastReplaceSyncSignature = new Map();
    this.fullSyncInFlight = new Map();
    this.rootPathStartLocks = new Map();
    // Per-connector replay serialization. Without this, an armed retry timer
    // and a live-event-driven replay() can fire concurrently — both iterate
    // the journal in order, both POST the first failed batch, the backend
    // sees duplicates, and the second to finish clobbers the other's
    // status update. Coalesce them into one in-flight replay per connector.
    this.replayInFlight = new Map();
    this.opChain = new Map();
    this.tickInFlight = new Map();
  }

  async init(): Promise<void> {
    const connectorIds = this.journal.listConnectorIds();
    for (const connectorId of connectorIds) {
      // Recovery order on app restart:
      //  1. Attempt replay, which is a no-op until a live runtime provides an
      //     access token via start().
      //  2. Watcher rescan+reconcile and backend full-sync run from start(),
      //     once the renderer has supplied that token.
      //
      // Intentional non-behavior: we do NOT bootstrap the watcher or scheduled
      // tick here, and we do NOT persist access tokens in journal metadata.
      // While the app is closed nothing should be syncing; on reopen we wait
      // for the renderer to mount the connector page, which calls start() and
      // brings the live watcher / scheduled timer up.
      try {
        await this.replay(connectorId);
      } catch (error) {
        console.warn(`[local-sync] startup replay failed for ${connectorId}:`, error);
        this.armRetry(connectorId, FULL_SYNC_MODE_REPLACE);
      }
    }
  }

  private emitStatus(connectorId: string): void {
    if (this.onStatusChange) this.onStatusChange(this.getStatus(connectorId) as ConnectorStatus);
  }

  private getRuntime(connectorId: string): RuntimeState | undefined {
    return this.runtimes.get(connectorId);
  }

  private findRuntimeByRootPath(
    normalizedRootPath: string,
    excludeConnectorId: string,
  ): RuntimeState | null {
    for (const [runtimeConnectorId, runtime] of this.runtimes) {
      if (runtimeConnectorId === excludeConnectorId) continue;
      const runtimeRootPath = runtime.normalizedRootPath || normalizeSyncRootPath(runtime.rootPath);
      if (runtimeRootPath === normalizedRootPath) return runtime;
    }
    return null;
  }

  private assertRootPathAvailable(normalizedRootPath: string, connectorId: string): void {
    const existingRuntime = this.findRuntimeByRootPath(normalizedRootPath, connectorId);
    if (existingRuntime) {
      const owner = existingRuntime.connectorName || existingRuntime.connectorId;
      throw new Error(`Local sync root is already watched by connector "${owner}": ${normalizedRootPath}`);
    }
    const lock = this.rootPathStartLocks.get(normalizedRootPath);
    if (lock && lock.connectorId !== connectorId) {
      const owner = lock.connectorName || lock.connectorId;
      throw new Error(`Local sync root is already watched by connector "${owner}": ${normalizedRootPath}`);
    }
  }

  async start({
    connectorId, connectorName, rootPath, apiBaseUrl, accessToken,
    includeSubfolders,
    connectorDisplayType,
    syncStrategy,
    scheduledConfig,
  }: StartArgs): Promise<ConnectorStatus> {
    if (!connectorId) throw new Error('connectorId is required');
    if (!rootPath) throw new Error('rootPath is required');
    if (!apiBaseUrl) throw new Error('apiBaseUrl is required');
    if (!accessToken) throw new Error('accessToken is required');

    const startSignature = buildRuntimeSignature({
      rootPath, apiBaseUrl, includeSubfolders,
      connectorDisplayType, syncStrategy, scheduledConfig,
    });
    const existing = this.runtimes.get(connectorId);
    if (
      existing
      && existing.startSignature === startSignature
      && (existing.watcherState === 'watching' || existing.watcherState === 'starting')
    ) {
      // Same configuration — refresh access token in place (renderer may pass
      // a fresh one) and keep the watcher + scheduled timer running. Without
      // this the scheduled tick never fires: renderer effects refire faster
      // than intervalMinutes (≥ 60s), and stop() clears the timer each time.
      existing.accessToken = accessToken;
      this.emitStatus(connectorId);
      return this.getStatus(connectorId) as ConnectorStatus;
    }

    const normalizedRootPath = normalizeSyncRootPath(rootPath);
    this.assertRootPathAvailable(normalizedRootPath, connectorId);
    this.rootPathStartLocks.set(normalizedRootPath, { connectorId, connectorName });

    await this.stop(connectorId);

    const strategy: 'MANUAL' | 'SCHEDULED' = syncStrategy || 'MANUAL';
    const activeScheduledConfig = strategy === 'SCHEDULED' ? scheduledConfig ?? null : null;
    const interval = activeScheduledConfig && Math.max(1, Number(activeScheduledConfig.intervalMinutes || 0));

    this.journal.setMeta(connectorId, {
      connectorName, rootPath, apiBaseUrl,
      includeSubfolders,
      connectorDisplayType, syncStrategy: strategy,
      scheduledConfig: activeScheduledConfig,
    });

    // Register the BullMQ repeat job on the backend (same as CLI). Best-effort:
    // failure here doesn't break local watching — the local interval below
    // still runs resyncs on the desktop side.
    if (strategy === 'SCHEDULED' && connectorDisplayType && interval) {
      try {
        await scheduleCrawlingManagerJob({
          apiBaseUrl, accessToken, connectorDisplayType,
          connectorInstanceId: connectorId,
          scheduledConfig: activeScheduledConfig,
        });
      } catch (err) {
        console.warn(
          `[local-sync:${connectorId}] schedule registration failed:`,
          err instanceof Error ? err.message : err,
        );
      }
    }

    const runtime: RuntimeState = {
      connectorId, connectorName, rootPath, normalizedRootPath, apiBaseUrl, accessToken,
      connectorDisplayType,
      syncStrategy: strategy,
      scheduledConfig: activeScheduledConfig,
      watcher: null, watcherState: 'starting', lastError: null,
      scheduleTimer: null,
      startSignature,
      shuttingDown: false,
    };
    this.runtimes.set(connectorId, runtime);
    this.rootPathStartLocks.delete(normalizedRootPath);
    const currentMeta = this.journal.getMeta(connectorId);
    const currentFullSyncSignature = buildFullSyncSignature(currentMeta);
    const shouldRunReplaceFullSync = this.lastReplaceSyncSignature.get(connectorId) !== currentFullSyncSignature;

    const processBatch = async ({ batchId, timestamp, events, source }: BatchPayload): Promise<void> => {
      const backlogBeforeAppend = this.journal.getPendingOrFailedBatches(connectorId);
      // Pre-compute replayEvents for batches containing directory-level events,
      // using the watcher state as it looks right now. Mirrors CLI behavior:
      // if the directory is deleted before a replay happens, we still know
      // which children to re-send to the backend.
      let replayEvents: WatchEvent[] | undefined;
      if ((events || []).some((e) => e.isDirectory)) {
        const stateFiles = loadWatcherStateFiles(this.baseDir, connectorId);
        replayEvents = expandWatchEventsForReplay(events, stateFiles);
      }
      this.journal.appendBatch(connectorId, { batchId, timestamp, events, source, replayEvents });
      this.emitStatus(connectorId);
      if (!events || events.length === 0) {
        this.journal.updateBatchStatus(connectorId, batchId, 'synced', { lastError: null });
        runtime.lastError = null;
        this.emitStatus(connectorId);
        return;
      }
      // SCHEDULED strategy: hold the batch in the journal as 'pending' and let
      // the scheduled tick (or the backend's localfs:resync trigger) drain it
      // via replay(). Otherwise the user-visible "Every N Minutes" cadence is
      // a lie — edits would propagate to the backend the moment the watcher
      // fires.
      //
      // ALSO: if we're shutting down (manager.stop drains the watcher on app
      // quit), keep the same journal-only behavior so app exit isn't blocked
      // by an in-flight 30s dispatch. The next session's init() replays.
      if (runtime.syncStrategy === 'SCHEDULED' || runtime.shuttingDown) {
        const totalPending = this.journal.getPendingOrFailedBatches(connectorId).length;
        const tag = runtime.shuttingDown ? 'SHUTDOWN' : 'SCHEDULED';
        console.log(
          `[local-sync:${connectorId}] ${tag}: queued batch ${batchId} with ${events.length} event(s) ` +
          `(${totalPending} pending; next tick/launch will drain)`,
        );
        runtime.lastError = null;
        this.emitStatus(connectorId);
        return;
      }
      try {
        if (backlogBeforeAppend.length > 0) {
          await this.replay(connectorId);
        } else {
          await this.dispatchFileEventBatch({
            apiBaseUrl: runtime.apiBaseUrl,
            accessToken: runtime.accessToken,
            connectorId,
            batchId, timestamp, events,
            rootPath: runtime.rootPath,
            refreshAccessToken: () => this.refreshAccessTokenForConnector(connectorId),
          });
          this.journal.updateBatchStatus(connectorId, batchId, 'synced', { lastError: null });
        }
        runtime.lastError = null;
        // Only cancel retry if no other failed/pending batches remain — a live
        // success here doesn't mean the journal is drained. Cancelling
        // unconditionally would strand prior failed batches until next failure
        // or app restart.
        if (this.journal.getPendingOrFailedBatches(connectorId).length === 0) {
          this.cancelRetry(connectorId);
        }
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        if (backlogBeforeAppend.length === 0) {
          this.journal.updateBatchStatus(connectorId, batchId, 'failed', { lastError: msg });
        }
        runtime.lastError = msg;
        if (backlogBeforeAppend.length === 0) {
          this.armRetry(connectorId, RECOVERY_MODE_REPLAY_ONLY);
        }
      }
      this.emitStatus(connectorId);
    };

    runtime.watcher = new ConnectorFsWatcher({
      connectorId,
      rootPath,
      baseDir: this.baseDir,
      onBatch: processBatch,
      includeSubfolders,
      log: (msg: string) => console.log(`[local-sync:${connectorId}]`, msg),
      onWatcherError: (err: Error) => {
        const rt = this.runtimes.get(connectorId);
        if (rt) rt.lastError = `watcher error: ${err.message}`;
        this.emitStatus(connectorId);
      },
    });

    // MANUAL: drain any backlog before live events flow so prior-session
    // pendings dispatch in order. SCHEDULED: skip — edits must be held until
    // the next tick (rule: "SCHEDULED — edits held until the tick"); the
    // backend full-sync below + tick replay catch up any backlog.
    if (strategy === 'MANUAL') {
      try {
        await this.replay(connectorId);
      } catch (error) {
        console.warn(`[local-sync] replay during start for ${connectorId}:`, error);
      }
    }
    await runtime.watcher.start();
    runtime.watcherState = 'watching';

    // Trigger a backend full-sync so it reconciles against actual disk state.
    // This covers the "app restart → full sync" edge case: the backend crawls
    // every file in the sync root and upserts/deletes as needed, independent
    // of journal history. Re-check the signature here — `await this.replay`
    // above can take long enough for an init()-launched full-sync to complete
    // and update lastReplaceSyncSignature; without this re-check we'd run a
    // redundant REPLACE for the same disk snapshot.
    if (shouldRunReplaceFullSync && this.lastReplaceSyncSignature.get(connectorId) !== currentFullSyncSignature) {
      this.triggerBackendFullSync(connectorId).then(() => {
        this.lastReplaceSyncSignature.set(connectorId, currentFullSyncSignature);
      }).catch((err: unknown) => {
        console.warn(
          `[local-sync:${connectorId}] backend full-sync trigger failed:`,
          err instanceof Error ? err.message : err,
        );
        const rt = this.runtimes.get(connectorId);
        if (rt) rt.lastError = err instanceof Error ? err.message : String(err);
        this.armRetry(connectorId, FULL_SYNC_MODE_REPLACE);
      });
    }

    // Scheduled-sync tick (desktop-side mirror of the backend cron job).
    // Fires every `intervalMinutes`, runs replay + rescan — same work the
    // CLI's `localfs:resync` socket listener triggers on each server cron tick.
    if (strategy === 'SCHEDULED' && interval) {
      const periodMs = Math.max(60_000, interval * 60_000);
      this.armScheduledTick(runtime, periodMs);
    }

    this.emitStatus(connectorId);
    return this.getStatus(connectorId) as ConnectorStatus;
  }

  // Scan every file in the root folder and send CREATED events to the
  // backend. The backend upserts idempotently (external_record_id is a
  // deterministic hash of connector + relative path), so this is safe to
  // call on every restart — it brings the backend in sync with actual disk
  // state without relying on replaying historical journal events.
  /**
   * Coalesces concurrent full-syncs (e.g. init + start) into one in-flight run per connector,
   * AND serializes against any in-flight replay so a REPLACE doesn't reset the backend
   * mid-replay and clobber its work.
   */
  triggerBackendFullSync(connectorId: string): Promise<void> {
    if (!connectorId) return Promise.resolve();
    const inflight = this.fullSyncInFlight.get(connectorId);
    if (inflight) return inflight;
    const prev = this.opChain.get(connectorId) || Promise.resolve();
    const p = prev
      .catch(() => { /* prior op's error must not block this one */ })
      .then(() => this._triggerBackendFullSyncBody(connectorId))
      .finally(() => {
        if (this.fullSyncInFlight.get(connectorId) === p) {
          this.fullSyncInFlight.delete(connectorId);
        }
      });
    this.fullSyncInFlight.set(connectorId, p);
    this.opChain.set(connectorId, p.catch(() => {}));
    return p;
  }

  private async _triggerBackendFullSyncBody(connectorId: string): Promise<void> {
    const meta = this.journal.getMeta(connectorId);
    if (!meta || !meta.rootPath || !meta.apiBaseUrl) return;
    const token = this.getRuntimeAccessToken(connectorId);
    if (!token) return;

    const rootPath = path.resolve(meta.rootPath);
    if (!fs.existsSync(rootPath)) return;

    const includeSubfolders = meta.includeSubfolders !== false;
    const scan = await scanSyncRoot(rootPath, { includeSubfolders });
    const events: WatchEvent[] = [];
    for (const [relPath, entry] of scan) {
      if (entry.isDirectory) continue;
      events.push({
        type: 'CREATED',
        path: relPath,
        timestamp: Date.now(),
        size: entry.size,
        isDirectory: false,
      });
    }

    const pending = this.journal.getPendingOrFailedBatches(connectorId);
    const batchSize = 50;
    try {
      // REPLACE-mode contract: first batch carries `resetBeforeApply: true` so
      // the backend wipes its prior snapshot before applying the new file set.
      // An empty disk still needs one no-op batch to clear backend state.
      const batches: Array<{ events: WatchEvent[]; resetBeforeApply: boolean }> = [];
      if (events.length === 0) {
        batches.push({ events: [], resetBeforeApply: true });
      } else {
        for (let i = 0; i < events.length; i += batchSize) {
          batches.push({
            events: events.slice(i, i + batchSize),
            resetBeforeApply: i === 0,
          });
        }
      }

      for (const batch of batches) {
        const batchId = `fullsync-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        await this.dispatchFileEventBatch({
          apiBaseUrl: meta.apiBaseUrl,
          accessToken: token,
          connectorId,
          batchId,
          timestamp: Date.now(),
          events: batch.events,
          resetBeforeApply: batch.resetBeforeApply,
          rootPath: meta.rootPath,
          refreshAccessToken: () => this.refreshAccessTokenForConnector(connectorId),
        });
      }
    } catch (err) {
      const rt = this.runtimes.get(connectorId);
      if (rt) rt.lastError = err instanceof Error ? err.message : String(err);
      throw err;
    }

    // Only after all chunks succeed: mark stale incremental batches superseded.
    for (const batch of pending) {
      this.journal.updateBatchStatus(connectorId, batch.batchId, 'synced', { lastError: null });
    }
    if (pending.length > 0) {
      console.log(`[local-sync:${connectorId}] full-sync: marked ${pending.length} stale journal batch(es) as synced`);
    }

    this.cancelRetry(connectorId);
    this.lastReplaceSyncSignature.set(connectorId, buildFullSyncSignature(meta));
    console.log(`[local-sync:${connectorId}] backend full-sync: sent ${events.length} file(s)`);
  }

  /**
   * Replay pending/failed journal batches from the journal. Only startup/restart
   * recovery escalates to a replace full-sync; reconnect recovery stays journal-driven.
   */
  async runRecoveryTick(connectorId: string): Promise<ReplayResult> {
    const mode = this.retryModes.get(connectorId) || RECOVERY_MODE_REPLAY_ONLY;
    const replayResult = await this.replay(connectorId);
    if (mode === FULL_SYNC_MODE_REPLACE) {
      await this.triggerBackendFullSync(connectorId);
    }
    return replayResult;
  }

  // Self-scheduling retry: when dispatch fails, re-run journal replay; when the
  // startup replace-sync fails, replay first and then rebuild from disk.
  // Keyed on connectorId so it works both while the watcher is running and
  // during pre-start() startup recovery.
  private armRetry(connectorId: string, mode: RecoveryMode): void {
    if (!connectorId) return;
    const nextMode: RecoveryMode = mode === FULL_SYNC_MODE_REPLACE
      ? FULL_SYNC_MODE_REPLACE
      : RECOVERY_MODE_REPLAY_ONLY;
    const existingMode = this.retryModes.get(connectorId);
    if (existingMode !== FULL_SYNC_MODE_REPLACE || nextMode === FULL_SYNC_MODE_REPLACE) {
      this.retryModes.set(connectorId, nextMode);
    }
    if (this.retryTimers.has(connectorId)) return;
    const attempt = this.retryAttempts.get(connectorId) || 0;
    const delay = Math.min(RETRY_BASE_MS * 2 ** attempt, RETRY_MAX_MS);
    this.retryAttempts.set(connectorId, attempt + 1);
    const timer = setTimeout(() => {
      this.retryTimers.delete(connectorId);
      this.runRecoveryTick(connectorId)
        .then(() => { this.retryAttempts.delete(connectorId); })
        .catch(() => { this.armRetry(connectorId, this.retryModes.get(connectorId) ?? RECOVERY_MODE_REPLAY_ONLY); });
    }, delay);
    if (timer.unref) timer.unref();
    this.retryTimers.set(connectorId, timer);
  }

  private cancelRetry(connectorId: string): void {
    const timer = this.retryTimers.get(connectorId);
    if (timer) { clearTimeout(timer); this.retryTimers.delete(connectorId); }
    this.retryAttempts.delete(connectorId);
    this.retryModes.delete(connectorId);
  }

  /**
   * Self-rescheduling tick. We use setTimeout instead of setInterval so a slow
   * tick never overlaps the next one; the timer is re-armed only after the
   * current replay/rescan attempt finishes.
   */
  private armScheduledTick(runtime: RuntimeState, periodMs: number): void {
    const connectorId = runtime.connectorId;
    const delay = Math.max(1_000, periodMs);
    runtime.scheduleTimer = setTimeout(() => {
      runtime.scheduleTimer = null;
      this.runScheduledTick(connectorId)
        .catch((err: unknown) => {
          console.warn(`[local-sync:${connectorId}] scheduled tick error:`, err);
        })
        .finally(() => {
          // Re-arm only if the runtime is still alive — stop() deletes the
          // runtime, and we must not resurrect the timer after teardown.
          if (this.runtimes.get(connectorId) === runtime) {
            this.armScheduledTick(runtime, periodMs);
          }
        });
    }, delay);
    if (runtime.scheduleTimer.unref) runtime.scheduleTimer.unref();
  }

  /**
   * Single-flight per connector: a slow tick (large rescan, slow network,
   * REPLACE in flight) can run longer than `intervalMinutes`, and the next
   * timer fire would otherwise race the prior tick — `rescan` is not
   * coalesced anywhere else and two concurrent rescans both call
   * `commitReconcile`, mutating snapshot state and producing duplicate or
   * missing offline events. If a tick is already running, we skip the new
   * one (the in-flight tick will catch any deltas added since it started
   * via its own rescan/replay).
   */
  runScheduledTick(connectorId: string): Promise<void> {
    const existing = this.tickInFlight.get(connectorId);
    if (existing) return existing;
    const promise = this._runScheduledTickInner(connectorId).finally(() => {
      if (this.tickInFlight.get(connectorId) === promise) {
        this.tickInFlight.delete(connectorId);
      }
    });
    this.tickInFlight.set(connectorId, promise);
    return promise;
  }

  private async _runScheduledTickInner(connectorId: string): Promise<void> {
    const runtime = this.runtimes.get(connectorId);
    if (!runtime) return;
    const tickStartedAt = Date.now();
    const pendingBefore = this.journal.getPendingOrFailedBatches(connectorId).length;
    console.log(
      `[local-sync:${connectorId}] scheduled tick: starting ` +
      `(${pendingBefore} batch(es) pending before rescan)`,
    );
    // Reset the per-tick error so a successful tick doesn't carry forward a
    // stale message from a prior step (e.g. a transient rescan ENOENT that
    // resolved by the time replay ran). Per-step failures below set lastError
    // again — the *last* error of this tick is what surfaces.
    runtime.lastError = null;
    let stepError: string | null = null;
    // Drain live events first: anything sitting in the correlator's 250ms
    // window or the dispatcher's 1s buffer needs to reach the journal before
    // we replay. Otherwise a change made just before the tick is invisible to
    // both rescan (state already updated by applyEventsToState) and replay
    // (journal hasn't received the batch yet) and slips to the next tick.
    try { if (runtime.watcher) await runtime.watcher.drainLiveEvents(); } catch (err) {
      stepError = err instanceof Error ? err.message : String(err);
    }
    // Rescan so any offline deltas land in the journal as 'pending' batches;
    // then replay drains everything (including those new batches and anything
    // held back by the SCHEDULED gate in processBatch) in one tick.
    try { if (runtime.watcher) await runtime.watcher.rescan(); } catch (err) {
      stepError = err instanceof Error ? err.message : String(err);
    }
    let replayResult: ReplayResult = { replayedBatches: 0, replayedEvents: 0, skippedBatches: 0 };
    try { replayResult = await this.replay(connectorId); } catch (err) {
      stepError = err instanceof Error ? err.message : String(err);
    }
    runtime.lastError = stepError;
    const elapsedMs = Date.now() - tickStartedAt;
    console.log(
      `[local-sync:${connectorId}] scheduled tick: done in ${elapsedMs}ms — ` +
      `replayed ${replayResult.replayedBatches} batch(es), ` +
      `${replayResult.replayedEvents} event(s), ` +
      `${replayResult.skippedBatches} skipped`,
    );
    this.emitStatus(connectorId);
  }

  async stop(connectorId: string): Promise<ConnectorStatus | null> {
    if (!connectorId) return null;
    // Always cancel any armed retry — init() can arm one before any runtime
    // exists (offline recovery before renderer calls start()), and stop()
    // must drain it whether or not a runtime is registered.
    this.cancelRetry(connectorId);
    const runtime = this.runtimes.get(connectorId);
    if (!runtime) return this.getStatus(connectorId) as ConnectorStatus;
    if (runtime.scheduleTimer) {
      clearTimeout(runtime.scheduleTimer);
      runtime.scheduleTimer = null;
    }
    // NOTE: regular stop() does NOT flip runtime.shuttingDown. stop() is
    // called both on app quit (via shutdown()) and on watcher replacement
    // (start() with a different signature, user-initiated disable). In the
    // latter cases buffered events should still dispatch as part of the
    // drain — only app quit needs the journal-only fast path. shutdown()
    // sets shuttingDown on every runtime *before* calling stop().
    if (runtime.watcher) {
      try { await runtime.watcher.stop(); } catch { /* ignore */ }
    }
    runtime.watcherState = 'stopped';
    this.runtimes.delete(connectorId);
    this.emitStatus(connectorId);
    return this.getStatus(connectorId) as ConnectorStatus;
  }

  /**
   * Replay batches from the journal. Mirrors CLI `replayPendingWatchBatches`.
   *
   * - default: incremental — only `pending` + `failed`
   * - { includeSynced: true }: full resync — replays every journal line,
   *   flipping already-`synced` lines back through dispatch as if fresh.
   *
   * Stops on the FIRST failing batch and rethrows (CLI parity), preserving
   * journal order so the backend never sees events out of order.
   */
  replay(connectorId: string, opts?: { includeSynced?: boolean }): Promise<ReplayResult> {
    // Single-flight per connector: an armed retry timer and a live-event
    // dispatch can both call replay() concurrently. Without coalescing,
    // both iterate the journal in order and re-dispatch the same first
    // pending batch — backend sees duplicates and the slower finisher's
    // status write clobbers the faster one's mark.
    const existing = this.replayInFlight.get(connectorId);
    if (existing) return existing;
    const prev = this.opChain.get(connectorId) || Promise.resolve();
    const promise = prev
      .catch(() => { /* prior op's error must not block this one */ })
      .then(() => this._replayInner(connectorId, opts))
      .finally(() => {
        if (this.replayInFlight.get(connectorId) === promise) {
          this.replayInFlight.delete(connectorId);
        }
      });
    this.replayInFlight.set(connectorId, promise);
    this.opChain.set(connectorId, promise.catch(() => {}));
    return promise;
  }

  private async _replayInner(
    connectorId: string,
    opts?: { includeSynced?: boolean },
  ): Promise<ReplayResult> {
    const meta = this.journal.getMeta(connectorId);
    if (!meta || !meta.apiBaseUrl) {
      return { replayedBatches: 0, replayedEvents: 0, skippedBatches: 0 };
    }
    const token = this.getRuntimeAccessToken(connectorId);
    if (!token) return { replayedBatches: 0, replayedEvents: 0, skippedBatches: 0 };

    const batches = this.journal.getReplayableBatches(connectorId, opts);
    let replayedBatches = 0;
    let replayedEvents = 0;
    let skippedBatches = 0;
    let rethrow: unknown = null;

    for (const batch of batches) {
      // Poison-pill guard: a batch that has already failed MAX_BATCH_ATTEMPTS times
      // is quarantined so it stops blocking the rest of the journal. The skip is
      // safe-ish for our event types (file CREATED/MODIFIED/DELETED): subsequent
      // events for the same path overwrite, and a manual full-resync wipes any
      // leftover divergence. Without this, a permanent 4xx (e.g. revoked perms)
      // retries forever at RETRY_MAX_MS cadence.
      if ((batch.attemptCount || 0) >= MAX_BATCH_ATTEMPTS) {
        this.journal.updateBatchStatus(connectorId, batch.batchId, 'quarantined', {
          lastError: batch.lastError || `quarantined after ${MAX_BATCH_ATTEMPTS} attempts`,
        });
        console.warn(
          `[local-sync:${connectorId}] quarantined batch ${batch.batchId} after ${batch.attemptCount} attempts: ${batch.lastError}`,
        );
        skippedBatches += 1;
        continue;
      }

      const stored = Array.isArray(batch.replayEvents) && batch.replayEvents.length > 0
        ? batch.replayEvents
        : null;
      const events: WatchEvent[] = stored
        ? stored
        : ((batch.events || []).some((e) => e.isDirectory)
          ? expandWatchEventsForReplay(batch.events || [], loadWatcherStateFiles(this.baseDir, connectorId))
          : (batch.events || []));

      if (!events || events.length === 0) {
        this.journal.updateBatchStatus(connectorId, batch.batchId, 'synced', { lastError: null });
        skippedBatches += 1;
        continue;
      }

      try {
        await this.dispatchFileEventBatch({
          apiBaseUrl: meta.apiBaseUrl,
          accessToken: token,
          connectorId,
          batchId: batch.batchId,
          timestamp: batch.timestamp,
          events,
          rootPath: meta.rootPath,
          refreshAccessToken: () => this.refreshAccessTokenForConnector(connectorId),
        });
        this.journal.updateBatchStatus(connectorId, batch.batchId, 'synced', { lastError: null });
        replayedBatches += 1;
        replayedEvents += events.length;
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        // Mark failed first (this also increments attemptCount). If we've now hit
        // the cap, quarantine on the spot so the *next* replay can proceed past it
        // immediately rather than waiting for one more retry to see attemptCount==MAX.
        this.journal.updateBatchStatus(connectorId, batch.batchId, 'failed', {
          lastError: msg,
        });
        const updated = this.journal.listBatches(connectorId).find((b) => b.batchId === batch.batchId);
        if (updated && (updated.attemptCount || 0) >= MAX_BATCH_ATTEMPTS) {
          this.journal.updateBatchStatus(connectorId, batch.batchId, 'quarantined', {
            lastError: msg,
          });
          console.warn(
            `[local-sync:${connectorId}] quarantined batch ${batch.batchId} after ${updated.attemptCount} attempts: ${msg}`,
          );
          skippedBatches += 1;
          // Don't break — continue draining the rest of the journal.
          continue;
        }
        rethrow = error;
        break;
      }
    }

    this.emitStatus(connectorId);
    if (rethrow) {
      this.armRetry(connectorId, RECOVERY_MODE_REPLAY_ONLY);
      throw rethrow;
    }
    if (replayedBatches > 0) this.cancelRetry(connectorId);
    return { replayedBatches, replayedEvents, skippedBatches };
  }

  /**
   * Refresh callback handed to the dispatcher: read the token held by the live
   * runtime. The renderer calls start() with fresh tokens, and the unchanged
   * configuration branch updates the runtime without rewriting local metadata.
   */
  private async refreshAccessTokenForConnector(connectorId: string): Promise<string | null> {
    return this.getRuntimeAccessToken(connectorId);
  }

  private getRuntimeAccessToken(connectorId: string): string | null {
    return this.runtimes.get(connectorId)?.accessToken || null;
  }

  /** Full resync: reset backend state from the current disk snapshot after replaying pending batches. */
  async fullResync(connectorId: string): Promise<ReplayResult> {
    const replayResult = await this.replay(connectorId);
    await this.triggerBackendFullSync(connectorId);
    return replayResult;
  }

  /**
   * Stop all active watchers. Called on app quit. Marks every runtime as
   * shutting down *before* calling stop() so the drain-on-stop path in
   * processBatch journal-only-returns instead of awaiting a 30s network
   * dispatch — app exit must not block on a slow backend.
   */
  async shutdown(): Promise<void> {
    for (const runtime of this.runtimes.values()) {
      runtime.shuttingDown = true;
    }
    const ids = Array.from(this.runtimes.keys());
    await Promise.allSettled(ids.map((id) => this.stop(id)));
  }

  getStatus(connectorId?: string): ConnectorStatus | ConnectorStatus[] {
    if (connectorId) {
      const runtime = this.getRuntime(connectorId);
      const summary = this.journal.getSummary(connectorId);
      const cursor = this.journal.readCursor(connectorId);
      const watcherStatus = runtime && runtime.watcher ? runtime.watcher.getStatus() : null;
      return {
        connectorId,
        watcherState: (runtime && runtime.watcherState) || 'stopped',
        rootPath: (runtime && runtime.rootPath) || (this.journal.getMeta(connectorId) || {}).rootPath || null,
        lastError: (runtime && runtime.lastError) || null,
        trackedFiles: watcherStatus ? watcherStatus.trackedFiles : null,
        pendingEvents: watcherStatus ? watcherStatus.pendingEvents : null,
        lastAckBatchId: cursor.lastAckBatchId || null,
        lastRecordedBatchId: cursor.lastRecordedBatchId || null,
        syncStrategy: (runtime && runtime.syncStrategy) || (this.journal.getMeta(connectorId) || {}).syncStrategy || 'MANUAL',
        scheduledConfig: (runtime && runtime.scheduledConfig) || (this.journal.getMeta(connectorId) || {}).scheduledConfig || null,
        ...summary,
      };
    }
    const ids = new Set([
      ...this.journal.listConnectorIds(),
      ...Array.from(this.runtimes.keys()),
    ]);
    return Array.from(ids).map((id) => this.getStatus(id) as ConnectorStatus);
  }

  /** Used by tests to reach into the journal/state directory. */
  get _baseDir(): string { return this.baseDir; }
}

// CommonJS interop: tests import `LocalSyncJournal` directly off the index.
export { LocalSyncJournal };
