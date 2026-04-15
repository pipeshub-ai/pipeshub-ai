const fs = require('fs');
const path = require('path');
const { safeStorage } = require('electron');
const { ConnectorFsWatcher } = require('./watcher');
const { LocalSyncJournal } = require('./journal');
const { dispatchFileEventBatch } = require('./dispatcher');
const { expandWatchEventsForReplay } = require('./replayer');
const { connectorFileSegment } = require('./watcher-state');
const { scheduleCrawlingManagerJob, unscheduleCrawlingManagerJob } = require('./backend-client');
const { buildCronFromSchedule } = require('./cron-from-schedule');

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
  }

  async init() {
    const connectorIds = this.journal.listConnectorIds();
    for (const connectorId of connectorIds) {
      try {
        await this.replay(connectorId);
      } catch (error) {
        console.warn(`[local-sync] startup replay failed for ${connectorId}:`, error);
      }
      // If a connector was set up with SCHEDULED strategy in a prior session,
      // restart its desktop-side timer without a full watcher restart. The
      // watcher itself is only restarted when the renderer calls start() again.
      const meta = this.journal.getMeta(connectorId);
      if (meta && meta.syncStrategy === 'SCHEDULED' && meta.scheduledConfig) {
        const interval = Math.max(1, Number(meta.scheduledConfig.intervalMinutes || 0));
        if (interval) {
          const periodMs = Math.max(60_000, interval * 60_000);
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
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        this.journal.updateBatchStatus(connectorId, batchId, 'failed', { lastError: msg });
        runtime.lastError = msg;
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
    if (rethrow) throw rethrow;
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
