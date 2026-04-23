const fs = require('fs');
const path = require('path');
const { safeStorage } = require('electron');
const { ConnectorFsWatcher } = require('./watcher');
const { LocalSyncJournal } = require('./journal');
const { dispatchFileEventBatch: defaultDispatchFileEventBatch } = require('./dispatcher');
const { expandWatchEventsForReplay } = require('./replayer');
const { connectorFileSegment, scanSyncRoot } = require('./watcher-state');
const { scheduleCrawlingManagerJob, unscheduleCrawlingManagerJob } = require('./backend-client');
const { buildCronFromSchedule } = require('./cron-from-schedule');

const RETRY_BASE_MS = 5_000;
const RETRY_MAX_MS = 5 * 60_000;
const FULL_SYNC_MODE_DELTA = 'delta';
const FULL_SYNC_MODE_REPLACE = 'replace';
const RECOVERY_MODE_REPLAY_ONLY = 'replay-only';

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

/** Matches [ConnectorFsWatcher.applyFilters](watcher.js) for files. */
function fileMatchesAllowedExtensions(relPath, extSet) {
  if (!extSet || extSet.size === 0) return true;
  const ext = path.extname(relPath).replace(/^\./, '').toLowerCase();
  if (!ext) return true;
  return extSet.has(ext);
}

function allowedExtensionSetFromMeta(meta) {
  const allowed = meta && meta.allowedExtensions;
  if (!Array.isArray(allowed) || allowed.length === 0) return null;
  return new Set(allowed.map((e) => String(e).toLowerCase().replace(/^\./, '')));
}

function buildFullSyncSignature(meta) {
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

/** Collects file paths referenced by a single journal/replay event (non-directory). */
function addKnownFilePathsFromEvent(ev, knownPaths) {
  if (!ev || ev.isDirectory) return;
  if (ev.path) knownPaths.add(ev.path);
  if (ev.oldPath && (ev.type === 'RENAMED' || ev.type === 'MOVED')) {
    knownPaths.add(ev.oldPath);
  }
}

class LocalSyncManager {
  constructor({ app, onStatusChange, dispatchFileEventBatch } = {}) {
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
    /** @type {Map<string, Promise<void>>} */
    this.fullSyncInFlight = new Map();
  }

  async init() {
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
      }).catch((err) => {
        console.warn(`[local-sync:${connectorId}] startup full-sync failed:`, err.message || err);
        this.armRetry(connectorId, FULL_SYNC_MODE_REPLACE);
      });
      // SCHEDULED strategy: the periodic timer lives on a runtime that the
      // renderer's start() will create. Replay + triggerBackendFullSync above
      // already covered the relaunch gap.
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
    const currentMeta = this.journal.getMeta(connectorId);
    const currentFullSyncSignature = buildFullSyncSignature(currentMeta);
    const shouldRunReplaceFullSync = this.lastReplaceSyncSignature.get(connectorId) !== currentFullSyncSignature;

    const processBatch = async ({ batchId, timestamp, events, source }) => {
      const backlogBeforeAppend = this.journal.getPendingOrFailedBatches(connectorId);
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
      if (!events || events.length === 0) {
        this.journal.updateBatchStatus(connectorId, batchId, 'synced', { lastError: null });
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
    if (shouldRunReplaceFullSync) {
      this.triggerBackendFullSync(connectorId, { mode: FULL_SYNC_MODE_REPLACE }).then(() => {
        this.lastReplaceSyncSignature.set(connectorId, currentFullSyncSignature);
      }).catch((err) => {
        console.warn(`[local-sync:${connectorId}] backend full-sync trigger failed:`, err.message || err);
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
  /**
   * Coalesces concurrent full-syncs (e.g. init + start) into one in-flight run per connector.
   */
  async triggerBackendFullSync(connectorId, opts) {
    if (!connectorId) return;
    let p = this.fullSyncInFlight.get(connectorId);
    if (p) return p;
    p = this._triggerBackendFullSyncBody(connectorId, opts).finally(() => {
      this.fullSyncInFlight.delete(connectorId);
    });
    this.fullSyncInFlight.set(connectorId, p);
    return p;
  }

  async _triggerBackendFullSyncBody(connectorId, opts) {
    const meta = this.journal.getMeta(connectorId);
    if (!meta || !meta.rootPath || !meta.apiBaseUrl) return;
    const token = decryptToken(meta.accessTokenEnc) || meta.accessToken;
    if (!token) return;

    const rootPath = path.resolve(meta.rootPath);
    if (!fs.existsSync(rootPath)) return;

    const includeSubfolders = meta.includeSubfolders !== false;
    const extSet = allowedExtensionSetFromMeta(meta);
    const mode = opts && opts.mode === FULL_SYNC_MODE_REPLACE
      ? FULL_SYNC_MODE_REPLACE
      : FULL_SYNC_MODE_DELTA;

    const scan = await scanSyncRoot(rootPath, { includeSubfolders });
    const events = [];
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
          addKnownFilePathsFromEvent(ev, knownPaths);
          if (ev.replayEvents) {
            for (const re of ev.replayEvents) {
              addKnownFilePathsFromEvent(re, knownPaths);
            }
          }
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
      const batches = [];
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
  async runRecoveryTick(connectorId) {
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
  armRetry(connectorId, mode) {
    if (!connectorId) return;
    const nextMode = mode === FULL_SYNC_MODE_REPLACE
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
        .catch(() => { this.armRetry(connectorId, this.retryModes.get(connectorId)); });
    }, delay);
    if (timer.unref) timer.unref();
    this.retryTimers.set(connectorId, timer);
  }

  cancelRetry(connectorId) {
    const timer = this.retryTimers.get(connectorId);
    if (timer) { clearTimeout(timer); this.retryTimers.delete(connectorId); }
    this.retryAttempts.delete(connectorId);
    this.retryModes.delete(connectorId);
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
    // Always cancel any armed retry — init() can arm one before any runtime
    // exists (offline recovery before renderer calls start()), and stop()
    // must drain it whether or not a runtime is registered.
    this.cancelRetry(connectorId);
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
        await this.dispatchFileEventBatch({
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
      this.armRetry(connectorId, RECOVERY_MODE_REPLAY_ONLY);
      throw rethrow;
    }
    if (replayedBatches > 0) this.cancelRetry(connectorId);
    return { replayedBatches, replayedEvents, skippedBatches };
  }

  /** Full resync: reset backend state from the current disk snapshot after replaying pending batches. */
  async fullResync(connectorId) {
    const replayResult = await this.replay(connectorId);
    await this.triggerBackendFullSync(connectorId, { mode: FULL_SYNC_MODE_REPLACE });
    return replayResult;
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
