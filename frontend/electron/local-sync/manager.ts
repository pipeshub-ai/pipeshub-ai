import * as fs from 'fs';
import * as path from 'path';
import electron from 'electron';
import { ConnectorFsWatcher, type BatchPayload } from './watcher/connector-fs-watcher';
import {
  LocalSyncJournal,
  type ConnectorMeta,
  type ScheduledConfig,
  type JournalRecord,
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
import { buildCronFromSchedule } from './cron-from-schedule';

const RETRY_BASE_MS = 5_000;
const RETRY_MAX_MS = 5 * 60_000;
const FULL_SYNC_MODE_DELTA = 'delta' as const;
const FULL_SYNC_MODE_REPLACE = 'replace' as const;
const RECOVERY_MODE_REPLAY_ONLY = 'replay-only' as const;

type FullSyncMode = typeof FULL_SYNC_MODE_DELTA | typeof FULL_SYNC_MODE_REPLACE;
type RecoveryMode = FullSyncMode | typeof RECOVERY_MODE_REPLAY_ONLY;

export interface ReplayResult {
  replayedBatches: number;
  replayedEvents: number;
  skippedBatches: number;
}

export type StoredToken = { enc: string } | { raw: string } | string | null | undefined;

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
  allowedExtensions?: string[];
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
  scheduledCron: string | null;
  watcher: ConnectorFsWatcher | null;
  watcherState: 'starting' | 'watching' | 'stopped';
  lastError: string | null;
  scheduleTimer: NodeJS.Timeout | null;
  startSignature: string;
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
  scheduledCron: string | null;
  pendingCount: number;
  failedCount: number;
  syncedCount: number;
  lastBatchId: string | null;
  lastAckAt: number | null;
}

interface RootPathLock {
  connectorId: string;
  connectorName?: string;
}

function encryptToken(value: string | null | undefined): StoredToken {
  if (!value) return null;
  try {
    if (electron.safeStorage && electron.safeStorage.isEncryptionAvailable()) {
      return { enc: electron.safeStorage.encryptString(String(value)).toString('base64') };
    }
  } catch { /* fall through */ }
  return { raw: String(value) };
}

function decryptToken(stored: StoredToken): string | null {
  if (!stored) return null;
  if (typeof stored === 'string') return stored; // legacy plain
  if ('raw' in stored && stored.raw) return stored.raw;
  if ('enc' in stored && stored.enc) {
    try {
      if (electron.safeStorage && electron.safeStorage.isEncryptionAvailable()) {
        return electron.safeStorage.decryptString(Buffer.from(stored.enc, 'base64'));
      }
    } catch { /* ignore */ }
  }
  return null;
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
function fileMatchesAllowedExtensions(relPath: string, extSet: Set<string> | null): boolean {
  if (!extSet || extSet.size === 0) return true;
  const ext = path.extname(relPath).replace(/^\./, '').toLowerCase();
  if (!ext) return true;
  return extSet.has(ext);
}

function allowedExtensionSetFromMeta(meta: ConnectorMeta | null): Set<string> | null {
  const allowed = meta && meta.allowedExtensions;
  if (!Array.isArray(allowed) || allowed.length === 0) return null;
  return new Set(allowed.map((e) => String(e).toLowerCase().replace(/^\./, '')));
}

function buildFullSyncSignature(meta: ConnectorMeta | null): string {
  if (!meta) return '';
  const allowed = Array.isArray(meta.allowedExtensions)
    ? [...meta.allowedExtensions]
      .map((e) => String(e).toLowerCase().replace(/^\./, ''))
      .filter(Boolean)
      .sort((a, b) => a.localeCompare(b))
    : [];
  return JSON.stringify({
    rootPath: path.resolve(String(meta.rootPath || '')),
    apiBaseUrl: String(meta.apiBaseUrl || ''),
    includeSubfolders: meta.includeSubfolders !== false,
    allowedExtensions: allowed,
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
  allowedExtensions?: string[];
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
  const allowed = Array.isArray(args.allowedExtensions)
    ? [...args.allowedExtensions]
      .map((e) => String(e).toLowerCase().replace(/^\./, ''))
      .filter(Boolean)
      .sort((a, b) => a.localeCompare(b))
    : [];
  const sched = args.syncStrategy === 'SCHEDULED' && args.scheduledConfig
    ? {
        intervalMinutes: Number(args.scheduledConfig.intervalMinutes) || 0,
        startTime: args.scheduledConfig.startTime != null ? args.scheduledConfig.startTime : null,
        timezone: args.scheduledConfig.timezone || null,
      }
    : null;
  return JSON.stringify({
    rootPath: path.resolve(String(args.rootPath || '')),
    apiBaseUrl: String(args.apiBaseUrl || ''),
    includeSubfolders: args.includeSubfolders !== false,
    allowedExtensions: allowed,
    connectorDisplayType: String(args.connectorDisplayType || ''),
    syncStrategy: args.syncStrategy === 'SCHEDULED' ? 'SCHEDULED' : 'MANUAL',
    scheduledConfig: sched,
  });
}

/** Collects file paths referenced by a single journal/replay event (non-directory). */
function addKnownFilePathsFromEvent(ev: WatchEvent, knownPaths: Set<string>): void {
  if (!ev || ev.isDirectory) return;
  if (ev.path) knownPaths.add(ev.path);
  if (ev.oldPath && (ev.type === 'RENAMED' || ev.type === 'MOVED')) {
    knownPaths.add(ev.oldPath);
  }
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
  }

  async init(): Promise<void> {
    const connectorIds = this.journal.listConnectorIds();
    for (const connectorId of connectorIds) {
      // Recovery order on app restart:
      //  1. Replay only pending/failed batches from the prior session (NOT
      //     already-synced ones — replaying stale rename/move events corrupts
      //     backend state when files have since been renamed again offline).
      //  2. If replay fails (still offline), arm the retry loop.
      //  3. Full filesystem crawl is triggered on startup as a replace-sync,
      //     so the backend is rebuilt from the current disk snapshot.
      //  4. Watcher rescan+reconcile (offline FS deltas) runs inside
      //     watcher.start() when the renderer calls start().
      try {
        await this.replay(connectorId);
      } catch (error) {
        console.warn(`[local-sync] startup replay failed for ${connectorId}:`, error);
        this.armRetry(connectorId, FULL_SYNC_MODE_REPLACE);
      }
      // Full-sync: scan every file on disk and send to backend. This covers
      // files renamed/added/deleted while the app was closed.
      this.triggerBackendFullSync(connectorId, { mode: FULL_SYNC_MODE_REPLACE }).then(() => {
        const meta = this.journal.getMeta(connectorId);
        this.lastReplaceSyncSignature.set(connectorId, buildFullSyncSignature(meta));
      }).catch((err: unknown) => {
        console.warn(
          `[local-sync:${connectorId}] startup full-sync failed:`,
          err instanceof Error ? err.message : err,
        );
        this.armRetry(connectorId, FULL_SYNC_MODE_REPLACE);
      });
      // SCHEDULED strategy: the periodic timer lives on a runtime that the
      // renderer's start() will create. Replay + triggerBackendFullSync above
      // already covered the relaunch gap.
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
    allowedExtensions, includeSubfolders,
    connectorDisplayType,
    syncStrategy,
    scheduledConfig,
  }: StartArgs): Promise<ConnectorStatus> {
    if (!connectorId) throw new Error('connectorId is required');
    if (!rootPath) throw new Error('rootPath is required');
    if (!apiBaseUrl) throw new Error('apiBaseUrl is required');
    if (!accessToken) throw new Error('accessToken is required');

    const startSignature = buildRuntimeSignature({
      rootPath, apiBaseUrl, includeSubfolders, allowedExtensions,
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
      const meta = this.journal.getMeta(connectorId) || {};
      this.journal.setMeta(connectorId, {
        ...meta,
        accessTokenEnc: encryptToken(accessToken) as ConnectorMeta['accessTokenEnc'],
      });
      this.emitStatus(connectorId);
      return this.getStatus(connectorId) as ConnectorStatus;
    }

    const normalizedRootPath = normalizeSyncRootPath(rootPath);
    this.assertRootPathAvailable(normalizedRootPath, connectorId);
    this.rootPathStartLocks.set(normalizedRootPath, { connectorId, connectorName });

    await this.stop(connectorId);

    const strategy: 'MANUAL' | 'SCHEDULED' = syncStrategy || 'MANUAL';
    const interval = scheduledConfig && Math.max(1, Number(scheduledConfig.intervalMinutes || 0));
    const cron = strategy === 'SCHEDULED' && interval
      ? buildCronFromSchedule({
          intervalMinutes: interval,
          startTime: scheduledConfig!.startTime ?? undefined,
          timezone: scheduledConfig!.timezone ?? undefined,
        })
      : null;

    this.journal.setMeta(connectorId, {
      connectorName, rootPath, apiBaseUrl,
      accessTokenEnc: encryptToken(accessToken) as ConnectorMeta['accessTokenEnc'],
      allowedExtensions, includeSubfolders,
      connectorDisplayType, syncStrategy: strategy,
      scheduledConfig: strategy === 'SCHEDULED' ? scheduledConfig : null,
      scheduledCron: cron,
    });

    // Register the BullMQ repeat job on the backend (same as CLI). Best-effort:
    // failure here doesn't break local watching — the local interval below
    // still runs resyncs on the desktop side.
    if (strategy === 'SCHEDULED' && connectorDisplayType && interval) {
      try {
        await scheduleCrawlingManagerJob({
          apiBaseUrl, accessToken, connectorDisplayType,
          connectorInstanceId: connectorId,
          intervalMinutes: interval,
          startTime: scheduledConfig?.startTime ?? undefined,
          timezone: scheduledConfig?.timezone ?? undefined,
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
      scheduledConfig: strategy === 'SCHEDULED' ? scheduledConfig ?? null : null,
      scheduledCron: cron,
      watcher: null, watcherState: 'starting', lastError: null,
      scheduleTimer: null,
      startSignature,
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
      if (runtime.syncStrategy === 'SCHEDULED') {
        const totalPending = this.journal.getPendingOrFailedBatches(connectorId).length;
        console.log(
          `[local-sync:${connectorId}] SCHEDULED: queued batch ${batchId} with ${events.length} event(s) ` +
          `(${totalPending} pending; next tick will drain)`,
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
      allowedExtensions,
      includeSubfolders,
      log: (msg: string) => console.log(`[local-sync:${connectorId}]`, msg),
    });

    try {
      await this.replay(connectorId);
    } catch (error) {
      console.warn(`[local-sync] replay during start for ${connectorId}:`, error);
    }
    await runtime.watcher.start();
    runtime.watcherState = 'watching';

    // Trigger a backend full-sync so it reconciles against actual disk state.
    // This covers the "app restart → full sync" edge case: the backend crawls
    // every file in the sync root and upserts/deletes as needed, independent
    // of journal history.
    if (shouldRunReplaceFullSync) {
      this.triggerBackendFullSync(connectorId, { mode: FULL_SYNC_MODE_REPLACE }).then(() => {
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
      runtime.scheduleTimer = setInterval(() => {
        this.runScheduledTick(connectorId).catch((err: unknown) => {
          console.warn(`[local-sync:${connectorId}] scheduled tick error:`, err);
        });
      }, periodMs);
      // Node refs prevent app from exiting; allow clean shutdown.
      if (runtime.scheduleTimer.unref) runtime.scheduleTimer.unref();
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
   * Coalesces concurrent full-syncs (e.g. init + start) into one in-flight run per connector.
   */
  triggerBackendFullSync(connectorId: string, opts: { mode?: FullSyncMode } = {}): Promise<void> {
    if (!connectorId) return Promise.resolve();
    const inflight = this.fullSyncInFlight.get(connectorId);
    if (inflight) return inflight;
    const p = this._triggerBackendFullSyncBody(connectorId, opts).finally(() => {
      this.fullSyncInFlight.delete(connectorId);
    });
    this.fullSyncInFlight.set(connectorId, p);
    return p;
  }

  private async _triggerBackendFullSyncBody(
    connectorId: string,
    opts: { mode?: FullSyncMode } = {},
  ): Promise<void> {
    const meta = this.journal.getMeta(connectorId);
    if (!meta || !meta.rootPath || !meta.apiBaseUrl) return;
    const token = decryptToken(meta.accessTokenEnc as StoredToken) || meta.accessToken;
    if (!token) return;

    const rootPath = path.resolve(meta.rootPath);
    if (!fs.existsSync(rootPath)) return;

    const includeSubfolders = meta.includeSubfolders !== false;
    const extSet = allowedExtensionSetFromMeta(meta);
    const mode: FullSyncMode = opts && opts.mode === FULL_SYNC_MODE_REPLACE
      ? FULL_SYNC_MODE_REPLACE
      : FULL_SYNC_MODE_DELTA;

    const scan = await scanSyncRoot(rootPath, { includeSubfolders });
    const events: WatchEvent[] = [];
    for (const [relPath, entry] of scan) {
      if (entry.isDirectory) continue;
      if (!fileMatchesAllowedExtensions(relPath, extSet)) continue;
      events.push({
        type: 'CREATED',
        path: relPath,
        timestamp: Date.now(),
        size: entry.size,
        isDirectory: false,
      });
    }

    if (mode === FULL_SYNC_MODE_DELTA) {
      const currentFiles = new Set(events.map((event) => event.path));

      // Collect every file path the backend might have a record for: union of
      // watcher state + all paths ever referenced in journal CREATED/MODIFIED/
      // RENAMED/MOVED (including batch-level directory expansions). Then DELETE
      // any that aren't currently on disk (for allowed extensions / scan depth).
      const knownPaths = new Set<string>();

      const oldFiles = loadWatcherStateFiles(this.baseDir, connectorId);
      for (const oldPath of Object.keys(oldFiles)) {
        const entry = oldFiles[oldPath];
        if (entry && !entry.isDirectory) knownPaths.add(oldPath);
      }

      const journalBatches = this.journal.listBatches(connectorId);
      for (const batch of journalBatches) {
        for (const ev of (batch.events || [])) {
          if (ev.isDirectory) continue;
          addKnownFilePathsFromEvent(ev, knownPaths);
        }
        if (Array.isArray(batch.replayEvents)) {
          for (const re of batch.replayEvents) {
            addKnownFilePathsFromEvent(re, knownPaths);
          }
        }
      }

      for (const oldPath of knownPaths) {
        if (!currentFiles.has(oldPath)) {
          events.push({
            type: 'DELETED',
            path: oldPath,
            timestamp: Date.now(),
            isDirectory: false,
          });
        }
      }
    }

    const pending = this.journal.getPendingOrFailedBatches(connectorId);
    const batchSize = 50;
    try {
      const batches: Array<{ events: WatchEvent[]; resetBeforeApply: boolean }> = [];
      if (events.length === 0 && mode === FULL_SYNC_MODE_REPLACE) {
        batches.push({ events: [], resetBeforeApply: true });
      } else {
        for (let i = 0; i < events.length; i += batchSize) {
          batches.push({
            events: events.slice(i, i + batchSize),
            resetBeforeApply: mode === FULL_SYNC_MODE_REPLACE && i === 0,
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
    if (mode === FULL_SYNC_MODE_REPLACE) {
      this.lastReplaceSyncSignature.set(connectorId, buildFullSyncSignature(meta));
    }
    console.log(`[local-sync:${connectorId}] backend full-sync (${mode}): sent ${events.length} file(s)`);
  }

  /**
   * Replay pending/failed journal batches from the journal. Only startup/restart
   * recovery escalates to a replace full-sync; reconnect recovery stays journal-driven.
   */
  async runRecoveryTick(connectorId: string): Promise<ReplayResult> {
    const mode = this.retryModes.get(connectorId) || RECOVERY_MODE_REPLAY_ONLY;
    const replayResult = await this.replay(connectorId);
    if (mode === FULL_SYNC_MODE_REPLACE) {
      await this.triggerBackendFullSync(connectorId, { mode: FULL_SYNC_MODE_REPLACE });
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

  async runScheduledTick(connectorId: string): Promise<void> {
    const runtime = this.runtimes.get(connectorId);
    if (!runtime) return;
    const tickStartedAt = Date.now();
    const pendingBefore = this.journal.getPendingOrFailedBatches(connectorId).length;
    console.log(
      `[local-sync:${connectorId}] scheduled tick: starting ` +
      `(${pendingBefore} batch(es) pending before rescan)`,
    );
    // Drain live events first: anything sitting in the correlator's 250ms
    // window or the dispatcher's 1s buffer needs to reach the journal before
    // we replay. Otherwise a change made just before the tick is invisible to
    // both rescan (state already updated by applyEventsToState) and replay
    // (journal hasn't received the batch yet) and slips to the next tick.
    try { if (runtime.watcher) await runtime.watcher.drainLiveEvents(); } catch (err) {
      runtime.lastError = err instanceof Error ? err.message : String(err);
    }
    // Rescan so any offline deltas land in the journal as 'pending' batches;
    // then replay drains everything (including those new batches and anything
    // held back by the SCHEDULED gate in processBatch) in one tick.
    try { if (runtime.watcher) await runtime.watcher.rescan(); } catch (err) {
      runtime.lastError = err instanceof Error ? err.message : String(err);
    }
    let replayResult: ReplayResult = { replayedBatches: 0, replayedEvents: 0, skippedBatches: 0 };
    try { replayResult = await this.replay(connectorId); } catch (err) {
      runtime.lastError = err instanceof Error ? err.message : String(err);
    }
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
      clearInterval(runtime.scheduleTimer);
      runtime.scheduleTimer = null;
    }
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
    const promise = this._replayInner(connectorId, opts).finally(() => {
      this.replayInFlight.delete(connectorId);
    });
    this.replayInFlight.set(connectorId, promise);
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
    const token = decryptToken(meta.accessTokenEnc as StoredToken) || meta.accessToken;
    if (!token) return { replayedBatches: 0, replayedEvents: 0, skippedBatches: 0 };

    const batches = this.journal.getReplayableBatches(connectorId, opts);
    let replayedBatches = 0;
    let replayedEvents = 0;
    let skippedBatches = 0;
    let rethrow: unknown = null;

    for (const batch of batches) {
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
        });
        this.journal.updateBatchStatus(connectorId, batch.batchId, 'synced', { lastError: null });
        replayedBatches += 1;
        replayedEvents += events.length;
      } catch (error) {
        this.journal.updateBatchStatus(connectorId, batch.batchId, 'failed', {
          lastError: error instanceof Error ? error.message : String(error),
        });
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

  /** Full resync: reset backend state from the current disk snapshot after replaying pending batches. */
  async fullResync(connectorId: string): Promise<ReplayResult> {
    const replayResult = await this.replay(connectorId);
    await this.triggerBackendFullSync(connectorId, { mode: FULL_SYNC_MODE_REPLACE });
    return replayResult;
  }

  /** Stop all active watchers, draining pending dispatches. Called on app quit. */
  async shutdown(): Promise<void> {
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
        scheduledCron: (runtime && runtime.scheduledCron) || (this.journal.getMeta(connectorId) || {}).scheduledCron || null,
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
