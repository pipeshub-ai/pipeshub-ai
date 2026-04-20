const fs = require('fs');
const path = require('path');
const chokidar = require('chokidar');
const { EventCorrelator } = require('./event-correlator');
const { BatchDispatcher } = require('./batch-dispatcher');
const {
  WatcherStateStore,
  scanSyncRoot,
  contentQuickHash,
} = require('./watcher-state');
const { IGNORED_PATTERNS } = require('./ignored-patterns');

/**
 * Watches `rootPath` with chokidar, correlates raw events into high-level FileEvents
 * (handling rename/move via inode + quickHash), deduplicates within batches, and
 * emits batches via onBatch. Performs startup reconciliation against a persisted
 * per-connector watcher_state.*.json so offline changes are captured.
 */
class ConnectorFsWatcher {
  constructor({
    connectorId,
    rootPath,
    baseDir,
    onBatch,
    flushMs,
    maxBatchSize,
    allowedExtensions,
    includeSubfolders,
    correlationWindowMs,
    changeDebounceMs,
    stateSyncDebounceMs,
    usePolling,
    pollInterval,
    log,
  }) {
    if (!connectorId) throw new Error('connectorId is required');
    if (!rootPath) throw new Error('rootPath is required');
    if (!baseDir) throw new Error('baseDir is required');
    this.connectorId = connectorId;
    this.rootPath = path.resolve(rootPath);
    this.baseDir = baseDir;
    this.onBatch = onBatch;
    this.includeSubfolders = includeSubfolders !== false;
    this.allowedExtensions = new Set(
      (allowedExtensions || []).map((e) => String(e).toLowerCase().replace(/^\./, ''))
    );
    this.log = log || ((msg) => console.log(`[local-sync:${connectorId}]`, msg));
    this.usePolling = usePolling === true;
    this.pollInterval = typeof pollInterval === 'number' && pollInterval > 0 ? pollInterval : 1000;
    this.stateSyncDebounceMs = typeof stateSyncDebounceMs === 'number' && stateSyncDebounceMs >= 0 ? stateSyncDebounceMs : 500;

    this.watcher = null;
    this.running = false;
    this.ready = false;
    this.stateSyncTimer = null;

    this.stateStore = new WatcherStateStore({
      baseDir: this.baseDir,
      syncRoot: this.rootPath,
      connectorInstanceId: this.connectorId,
    });

    this.dispatcher = new BatchDispatcher(
      async (events, meta) => {
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
        onError: (err) => this.log(`Batch dispatch error: ${err && err.message ? err.message : String(err)}`),
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
      getPreviousFileEntry: (relKey) => this.stateStore.getSnapshot().files[relKey],
    });

    this.correlator.setListener((events) => {
      const filtered = this.applyFilters(events);
      if (filtered.length > 0) this.dispatcher.push(filtered);
    });
  }

  scanOptions() {
    return {
      includeSubfolders: this.includeSubfolders,
      previousByRelPath: new Map(Object.entries(this.stateStore.getSnapshot().files)),
      ignoredPatterns: IGNORED_PATTERNS,
    };
  }

  scheduleStateSyncFromDisk() {
    if (this.stateSyncTimer) clearTimeout(this.stateSyncTimer);
    this.stateSyncTimer = setTimeout(() => {
      this.stateSyncTimer = null;
      this.syncStateFromDisk().catch(() => { /* ignore */ });
    }, this.stateSyncDebounceMs);
  }

  async syncStateFromDisk() {
    try {
      const scan = await scanSyncRoot(this.rootPath, this.scanOptions());
      this.stateStore.applyScan(scan);
      this.stateStore.flushSave();
    } catch (err) {
      this.log(`State rescan error: ${err && err.message ? err.message : String(err)}`);
    }
  }

  applyFilters(events) {
    if (this.allowedExtensions.size === 0) return events;
    return events.filter((ev) => {
      if (ev.isDirectory) return true;
      const pathsToCheck = [ev.path];
      if (
        (ev.type === 'RENAMED' || ev.type === 'MOVED' || ev.type === 'DIR_RENAMED' || ev.type === 'DIR_MOVED')
        && ev.oldPath
      ) {
        pathsToCheck.push(ev.oldPath);
      }
      for (const p of pathsToCheck) {
        const ext = path.extname(p).replace(/^\./, '').toLowerCase();
        if (!ext) return true;
        if (this.allowedExtensions.has(ext)) return true;
      }
      return false;
    });
  }

  async start() {
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
      const currentScan = await scanSyncRoot(this.rootPath, this.scanOptions());
      const offlineEvents = this.stateStore.commitReconcile(currentScan);
      const filtered = this.applyFilters(offlineEvents);
      if (filtered.length > 0) {
        this.log(`Found ${filtered.length} change(s) since last run.`);
        this.dispatcher.push(filtered, { source: 'reconcile' });
      }
    }

    const depth = this.includeSubfolders ? undefined : 0;
    this.watcher = chokidar.watch(this.rootPath, {
      ignored: IGNORED_PATTERNS,
      persistent: true,
      ignoreInitial: true,
      followSymlinks: false,
      depth,
      alwaysStat: true,
      usePolling: this.usePolling,
      interval: this.pollInterval,
      awaitWriteFinish: { stabilityThreshold: 200, pollInterval: 100 },
      ignorePermissionErrors: true,
      atomic: 200,
    });

    this.watcher.on('all', (eventName, filePath, stats) => {
      if (!this.ready) return;
      if (!['add', 'addDir', 'unlink', 'unlinkDir', 'change'].includes(eventName)) return;
      this.scheduleStateSyncFromDisk();
      this.correlator.push(eventName, filePath, stats).catch(() => { /* ignore */ });
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
          this.log(`Baseline scan error: ${err && err.message ? err.message : String(err)}`);
        }
      } else {
        this.log('Watcher ready.');
      }
    });

    this.watcher.on('error', (err) => {
      this.log(`Watcher error: ${err && err.message ? err.message : String(err)}`);
    });
  }

  async stop() {
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

  getStatus() {
    const state = this.stateStore.getSnapshot();
    return {
      running: this.running,
      rootPath: this.rootPath,
      connectorId: this.connectorId,
      trackedFiles: Object.keys(state.files).length,
      pendingEvents: this.dispatcher.pending,
    };
  }

  async rescan() {
    const scan = await scanSyncRoot(this.rootPath, this.scanOptions());
    const events = this.stateStore.commitReconcile(scan);
    this.stateStore.flushSave();
    const filtered = this.applyFilters(events);
    if (filtered.length > 0) this.dispatcher.push(filtered, { source: 'reconcile' });
    return filtered;
  }
}

module.exports = { ConnectorFsWatcher };
