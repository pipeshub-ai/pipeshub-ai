const fs = require('fs');
const path = require('path');
const { safeStorage } = require('electron');
const { ConnectorFsWatcher } = require('./watcher');
const { LocalSyncJournal } = require('./journal');
const { dispatchFileEventBatch } = require('./dispatcher');
const { expandWatchEventsForReplay } = require('./replayer');
const { connectorFileSegment, scanSyncRoot } = require('./watcher-state');
const { scheduleCrawlingManagerJob, unscheduleCrawlingManagerJob } = require('./backend-client');
const { buildCronFromSchedule } = require('./cron-from-schedule');

const RETRY_BASE_MS = 5_000;
const RETRY_MAX_MS = 5 * 60_000;

function encryptToken(value) {
  if (!value) return null;
  try {
    if (safeStorage && safeStorage.isEncryptionAvailable()) {
      return { enc: safeStorage.encryptString(String(value)).toString('base64') };
    }
  } catch { /* fall through */ }
  return { raw: String(value) };
}

function decryptToken(stored) {
  if (!stored) return null;
  if (typeof stored === 'string') return stored; // legacy plain
  if (stored.raw) return stored.raw;
  if (stored.enc) {
    try {
      if (safeStorage && safeStorage.isEncryptionAvailable()) {
        return safeStorage.decryptString(Buffer.from(stored.enc, 'base64'));
      }
    } catch { /* ignore */ }
  }
  return null;
}

function loadWatcherStateFiles(baseDir, connectorId) {
  const p = path.join(baseDir, `watcher_state.${connectorFileSegment(connectorId)}.json`);
  if (!fs.existsSync(p)) return {};
  try {
    const raw = JSON.parse(fs.readFileSync(p, 'utf8'));
    return (raw && raw.files) || {};
  } catch {
    return {};
  }
}

class LocalSyncManager {
  constructor({ app, onStatusChange }) {
    this.app = app;
    this.onStatusChange = onStatusChange;
    this.baseDir = path.join(this.app.getPath('userData'), 'local-sync-journal');
    this.journal = new LocalSyncJournal(this.baseDir);
    this.runtimes = new Map();
    // Retry scheduling keyed on connectorId so it survives even when the
    // renderer hasn't (yet) called start() — i.e. during offline recovery on
    // app launch before the window mounts the connector UI.
    this.retryTimers = new Map();
    this.retryAttempts = new Map();
  }

  async init() {
    const connectorIds = this.journal.listConnectorIds();
    for (const connectorId of connectorIds) {
      // Recovery order on app restart:
      //  1. Replay only pending/failed batches from the prior session (NOT
      //     already-synced ones — replaying stale rename/move events corrupts
      //     backend state when files have since been renamed again offline).
      //  2. If replay fails (still offline), arm the retry loop.
      //  3. Full filesystem crawl is triggered via triggerBackendFullSync()
      //     after start() so the backend reconciles against actual disk state.
      //  4. Watcher rescan+reconcile (offline FS deltas) runs inside
      //     watcher.start() when the renderer calls start().
      try {
        await this.replay(connectorId);
      } catch (error) {
        console.warn(`[local-sync] startup replay failed for ${connectorId}:`, error);
        this.armRetry(connectorId);
      }
      // Full-sync: scan every file on disk and send to backend. This covers
      // files renamed/added/deleted while the app was closed.
      this.triggerBackendFullSync(connectorId).catch((err) => {
        console.warn(`[local-sync:${connectorId}] startup full-sync failed:`, err.message || err);
        this.armRetry(connectorId);
      });
      // If a connector was set up with SCHEDULED strategy in a prior session,
      // restart its desktop-side timer without a full watcher restart. The
      // watcher itself is only restarted when the renderer calls start() again.
      const meta = this.journal.getMeta(connectorId);
      if (meta && meta.syncStrategy === 'SCHEDULED' && meta.scheduledConfig) {
        const interval = Math.max(1, Number(meta.scheduledConfig.intervalMinutes || 0));
        if (interval) {
          // Fire a one-shot tick so pending work is replayed promptly on relaunch,
          // then settle into the regular cadence.
          this.runScheduledTick(connectorId).catch(() => { /* ignore */ });
          // Note: the timer lives on a runtime that start() will create. If the
          // renderer hasn't called start() yet, replay covered the gap.
        }
      }
    }
  }

  emitStatus(connectorId) {
    if (this.onStatusChange) this.onStatusChange(this.getStatus(connectorId));
  }

  getRuntime(connectorId) {
    return this.runtimes.get(connectorId);
  }

  async start({
    connectorId, connectorName, rootPath, apiBaseUrl, accessToken,
    allowedExtensions, includeSubfolders,
    connectorDisplayType,
    syncStrategy,          // 'MANUAL' | 'SCHEDULED'
    scheduledConfig,       // { intervalMinutes, startTime?, timezone? }
  }) {
    if (!connectorId) throw new Error('connectorId is required');
    if (!rootPath) throw new Error('rootPath is required');
    if (!apiBaseUrl) throw new Error('apiBaseUrl is required');
    if (!accessToken) throw new Error('accessToken is required');

    await this.stop(connectorId);

    const strategy = syncStrategy || 'MANUAL';
    const interval = scheduledConfig && Math.max(1, Number(scheduledConfig.intervalMinutes || 0));
    const cron = strategy === 'SCHEDULED' && interval
      ? buildCronFromSchedule({
          intervalMinutes: interval,
          startTime: scheduledConfig.startTime,
          timezone: scheduledConfig.timezone,
        })
      : null;

    this.journal.setMeta(connectorId, {
      connectorName, rootPath, apiBaseUrl,
      accessTokenEnc: encryptToken(accessToken),
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
          startTime: scheduledConfig && scheduledConfig.startTime,
          timezone: scheduledConfig && scheduledConfig.timezone,
        });
      } catch (err) {
        console.warn(`[local-sync:${connectorId}] schedule registration failed:`, err.message || err);
      }
    }

    const runtime = {
      connectorId, connectorName, rootPath, apiBaseUrl, accessToken,
      connectorDisplayType,
      syncStrategy: strategy,
      scheduledConfig: strategy === 'SCHEDULED' ? scheduledConfig : null,
      scheduledCron: cron,
      watcher: null, watcherState: 'starting', lastError: null,
      scheduleTimer: null,
    };
    this.runtimes.set(connectorId, runtime);

    const processBatch = async ({ batchId, timestamp, events, source }) => {
      // Pre-compute replayEvents for batches containing directory-level events,
      // using the watcher state as it looks right now. Mirrors CLI behavior:
      // if the directory is deleted before a replay happens, we still know
      // which children to re-send to the backend.
      let replayEvents;
      if ((events || []).some((e) => e.isDirectory)) {
        const stateFiles = loadWatcherStateFiles(this.baseDir, connectorId);
        replayEvents = expandWatchEventsForReplay(events, stateFiles);
      }
      this.journal.appendBatch(connectorId, { batchId, timestamp, events, source, replayEvents });
      this.emitStatus(connectorId);
      try {
        await dispatchFileEventBatch({
          apiBaseUrl: runtime.apiBaseUrl,
          accessToken: runtime.accessToken,
          connectorId,
          batchId, timestamp, events,
        });
        this.journal.updateBatchStatus(connectorId, batchId, 'synced', { lastError: null });
        runtime.lastError = null;
        this.cancelRetry(connectorId);
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        this.journal.updateBatchStatus(connectorId, batchId, 'failed', { lastError: msg });
        runtime.lastError = msg;
        this.armRetry(connectorId);
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
      log: (msg) => console.log(`[local-sync:${connectorId}]`, msg),
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
    this.triggerBackendFullSync(connectorId).catch((err) => {
      console.warn(`[local-sync:${connectorId}] backend full-sync trigger failed:`, err.message || err);
    });

    // Scheduled-sync tick (desktop-side mirror of the backend cron job).
    // Fires every `intervalMinutes`, runs replay + rescan — same work the
    // CLI's `localfs:resync` socket listener triggers on each server cron tick.
    if (strategy === 'SCHEDULED' && interval) {
      const periodMs = Math.max(60_000, interval * 60_000);
      runtime.scheduleTimer = setInterval(() => {
        this.runScheduledTick(connectorId).catch((err) => {
          console.warn(`[local-sync:${connectorId}] scheduled tick error:`, err);
        });
      }, periodMs);
      // Node refs prevent app from exiting; allow clean shutdown.
      if (runtime.scheduleTimer.unref) runtime.scheduleTimer.unref();
    }

    this.emitStatus(connectorId);
    return this.getStatus(connectorId);
  }

  // Scan every file in the root folder and send CREATED events to the
  // backend. The backend upserts idempotently (external_record_id is a
  // deterministic hash of connector + relative path), so this is safe to
  // call on every restart — it brings the backend in sync with actual disk
  // state without relying on replaying historical journal events.
  async triggerBackendFullSync(connectorId) {
    const meta = this.journal.getMeta(connectorId);
    if (!meta || !meta.rootPath || !meta.apiBaseUrl) return;
    const token = decryptToken(meta.accessTokenEnc) || meta.accessToken;
    if (!token) return;

    const rootPath = path.resolve(meta.rootPath);
    if (!fs.existsSync(rootPath)) return;

    const scan = await scanSyncRoot(rootPath, { includeSubfolders: true });
    const currentFiles = new Set();
    const events = [];
    for (const [relPath, entry] of scan) {
      if (entry.isDirectory) continue;
      currentFiles.add(relPath);
      events.push({
        type: 'CREATED',
        path: relPath,
        timestamp: Date.now(),
        size: entry.size,
        isDirectory: false,
      });
    }

    // Collect every file path the backend might have a record for: union of
    // watcher state + all paths ever referenced in journal CREATED/MODIFIED/
    // RENAMED-to events. Then DELETE any that aren't currently on disk.
    const knownPaths = new Set();

    const oldFiles = loadWatcherStateFiles(this.baseDir, connectorId);
    for (const oldPath of Object.keys(oldFiles)) {
      const entry = oldFiles[oldPath];
      if (entry && !entry.isDirectory) knownPaths.add(oldPath);
    }

    const journalBatches = this.journal.listBatches(connectorId);
    for (const batch of journalBatches) {
      for (const ev of (batch.events || [])) {
        if (ev.isDirectory) continue;
        if (ev.path) knownPaths.add(ev.path);
        if (ev.replayEvents) {
          for (const re of ev.replayEvents) {
            if (!re.isDirectory && re.path) knownPaths.add(re.path);
          }
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

    if (events.length === 0) return;

    // Mark all existing pending/failed journal batches as synced — the full
    // scan supersedes any stale incremental events.
    const pending = this.journal.getPendingOrFailedBatches(connectorId);
    for (const batch of pending) {
      this.journal.updateBatchStatus(connectorId, batch.batchId, 'synced', { lastError: null });
    }
    if (pending.length > 0) {
      console.log(`[local-sync:${connectorId}] full-sync: marked ${pending.length} stale journal batch(es) as synced`);
    }

    const batchSize = 50;
    for (let i = 0; i < events.length; i += batchSize) {
      const chunk = events.slice(i, i + batchSize);
      const batchId = `fullsync-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      await dispatchFileEventBatch({
        apiBaseUrl: meta.apiBaseUrl,
        accessToken: token,
        connectorId,
        batchId,
        timestamp: Date.now(),
        events: chunk,
      });
    }
    this.cancelRetry(connectorId);
    console.log(`[local-sync:${connectorId}] backend full-sync: sent ${events.length} file(s)`);
  }

  // Self-scheduling retry: when a dispatch fails (network down, server 5xx
  // after dispatcher max attempts), re-run replay() with exponential backoff
  // until the journal clears. Keyed on connectorId so it works both while the
  // watcher is running AND during pre-start() startup recovery.
  armRetry(connectorId) {
    if (!connectorId || this.retryTimers.has(connectorId)) return;
    const attempt = this.retryAttempts.get(connectorId) || 0;
    const delay = Math.min(RETRY_BASE_MS * 2 ** attempt, RETRY_MAX_MS);
    this.retryAttempts.set(connectorId, attempt + 1);
    const timer = setTimeout(() => {
      this.retryTimers.delete(connectorId);
      this.replay(connectorId)
        .then(() => { this.retryAttempts.delete(connectorId); })
        .catch(() => { this.armRetry(connectorId); });
    }, delay);
    if (timer.unref) timer.unref();
    this.retryTimers.set(connectorId, timer);
  }

  cancelRetry(connectorId) {
    const timer = this.retryTimers.get(connectorId);
    if (timer) { clearTimeout(timer); this.retryTimers.delete(connectorId); }
    this.retryAttempts.delete(connectorId);
  }

  async runScheduledTick(connectorId) {
    const runtime = this.runtimes.get(connectorId);
    if (!runtime) return;
    try { await this.replay(connectorId); } catch (err) {
      runtime.lastError = err instanceof Error ? err.message : String(err);
    }
    try { if (runtime.watcher) await runtime.watcher.rescan(); } catch (err) {
      runtime.lastError = err instanceof Error ? err.message : String(err);
    }
    this.emitStatus(connectorId);
  }

  async stop(connectorId) {
    if (!connectorId) return null;
    const runtime = this.runtimes.get(connectorId);
    if (!runtime) return this.getStatus(connectorId);
    if (runtime.scheduleTimer) {
      clearInterval(runtime.scheduleTimer);
      runtime.scheduleTimer = null;
    }
    this.cancelRetry(connectorId);
    if (runtime.watcher) {
      try { await runtime.watcher.stop(); } catch { /* ignore */ }
    }
    runtime.watcherState = 'stopped';
    this.runtimes.delete(connectorId);
    this.emitStatus(connectorId);
    return this.getStatus(connectorId);
  }

  /** Update the schedule of a running connector without restarting the watcher. */
  async setSchedule(connectorId, { syncStrategy, scheduledConfig, connectorDisplayType } = {}) {
    const meta = this.journal.getMeta(connectorId);
    if (!meta) return this.getStatus(connectorId);

    const strategy = syncStrategy || 'MANUAL';
    const interval = scheduledConfig && Math.max(1, Number(scheduledConfig.intervalMinutes || 0));
    const cron = strategy === 'SCHEDULED' && interval
      ? buildCronFromSchedule({
          intervalMinutes: interval,
          startTime: scheduledConfig && scheduledConfig.startTime,
          timezone: scheduledConfig && scheduledConfig.timezone,
        })
      : null;

    this.journal.setMeta(connectorId, {
      ...meta,
      syncStrategy: strategy,
      scheduledConfig: strategy === 'SCHEDULED' ? scheduledConfig : null,
      scheduledCron: cron,
      connectorDisplayType: connectorDisplayType || meta.connectorDisplayType,
    });

    const token = decryptToken(meta.accessTokenEnc) || meta.accessToken;
    if (strategy === 'SCHEDULED' && interval && token && (connectorDisplayType || meta.connectorDisplayType)) {
      try {
        await scheduleCrawlingManagerJob({
          apiBaseUrl: meta.apiBaseUrl, accessToken: token,
          connectorDisplayType: connectorDisplayType || meta.connectorDisplayType,
          connectorInstanceId: connectorId,
          intervalMinutes: interval,
          startTime: scheduledConfig && scheduledConfig.startTime,
          timezone: scheduledConfig && scheduledConfig.timezone,
        });
      } catch (err) {
        console.warn(`[local-sync:${connectorId}] schedule update failed:`, err.message || err);
      }
    } else if (strategy !== 'SCHEDULED' && token && (connectorDisplayType || meta.connectorDisplayType)) {
      await unscheduleCrawlingManagerJob({
        apiBaseUrl: meta.apiBaseUrl, accessToken: token,
        connectorDisplayType: connectorDisplayType || meta.connectorDisplayType,
        connectorInstanceId: connectorId,
      });
    }

    const runtime = this.runtimes.get(connectorId);
    if (runtime) {
      if (runtime.scheduleTimer) { clearInterval(runtime.scheduleTimer); runtime.scheduleTimer = null; }
      if (strategy === 'SCHEDULED' && interval) {
        const periodMs = Math.max(60_000, interval * 60_000);
        runtime.scheduleTimer = setInterval(() => {
          this.runScheduledTick(connectorId).catch(() => { /* ignore */ });
        }, periodMs);
        if (runtime.scheduleTimer.unref) runtime.scheduleTimer.unref();
      }
      runtime.syncStrategy = strategy;
      runtime.scheduledConfig = strategy === 'SCHEDULED' ? scheduledConfig : null;
      runtime.scheduledCron = cron;
    }

    this.emitStatus(connectorId);
    return this.getStatus(connectorId);
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
   *
   * @returns {{ replayedBatches: number, replayedEvents: number, skippedBatches: number }}
   */
  async replay(connectorId, opts) {
    const meta = this.journal.getMeta(connectorId);
    if (!meta || !meta.apiBaseUrl) {
      return { replayedBatches: 0, replayedEvents: 0, skippedBatches: 0 };
    }
    const token = decryptToken(meta.accessTokenEnc) || meta.accessToken;
    if (!token) return { replayedBatches: 0, replayedEvents: 0, skippedBatches: 0 };

    const batches = this.journal.getReplayableBatches(connectorId, opts);
    let replayedBatches = 0;
    let replayedEvents = 0;
    let skippedBatches = 0;
    let rethrow = null;

    for (const batch of batches) {
      const stored = Array.isArray(batch.replayEvents) && batch.replayEvents.length > 0
        ? batch.replayEvents
        : null;
      const events = stored
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
        await dispatchFileEventBatch({
          apiBaseUrl: meta.apiBaseUrl,
          accessToken: token,
          connectorId,
          batchId: batch.batchId,
          timestamp: batch.timestamp,
          events,
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
      this.armRetry(connectorId);
      throw rethrow;
    }
    if (replayedBatches > 0) this.cancelRetry(connectorId);
    return { replayedBatches, replayedEvents, skippedBatches };
  }

  /** Full resync: same as {@link replay} but replays every journal line, including synced. */
  async fullResync(connectorId) {
    return this.replay(connectorId, { includeSynced: true });
  }

  /** Stop all active watchers, draining pending dispatches. Called on app quit. */
  async shutdown() {
    const ids = Array.from(this.runtimes.keys());
    await Promise.allSettled(ids.map((id) => this.stop(id)));
  }

  async rescan(connectorId) {
    const runtime = this.runtimes.get(connectorId);
    if (!runtime || !runtime.watcher) return this.getStatus(connectorId);
    try { await runtime.watcher.rescan(); } catch (err) {
      runtime.lastError = err instanceof Error ? err.message : String(err);
    }
    this.emitStatus(connectorId);
    return this.getStatus(connectorId);
  }

  getStatus(connectorId) {
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
    return Array.from(ids).map((id) => this.getStatus(id));
  }
}

module.exports = { LocalSyncManager };
