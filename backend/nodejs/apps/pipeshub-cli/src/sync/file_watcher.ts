import * as fs from "fs";
import * as path from "path";
import { watch, type FSWatcher } from "chokidar";
import { EventCorrelator, type RawEventType } from "./event_correlator";
import {
  BatchDispatcher,
  type DispatchFn,
  type BatchDispatcherOptions,
} from "./batch_dispatcher";
import { recordWatchBatch } from "./watcher_sync_journal";
import {
  WatcherStateStore,
  scanSyncRoot,
  contentQuickHash,
  type FileEntry,
  type FileEvent,
  type WatcherStateStoreOptions,
} from "./watcher_state";
import { expandWatchEventsForReplay } from "./watcher_resync_replayer";

/** Temp/editor files that should always be ignored. */
const IGNORED_PATTERNS: (string | RegExp)[] = [
  // Editors / OS
  /(?:^|[/\\])\.DS_Store$/,
  /(?:^|[/\\])Thumbs\.db$/,
  /(?:^|[/\\])desktop\.ini$/,
  // Vim
  /\.swp$/, /\.swo$/, /~$/,
  // Emacs
  /(?:^|[/\\])\.#/, /#$/,
  // JetBrains
  /(?:^|[/\\])___jb_\w+___$/,
  // VS Code
  /\.crswap$/,
  // General temp
  /\.tmp$/,
  // Version control
  /(?:^|[/\\])\.git(?:[/\\]|$)/,
  // Dependencies / build
  /(?:^|[/\\])node_modules(?:[/\\]|$)/,
  /(?:^|[/\\])__pycache__(?:[/\\]|$)/,
  /(?:^|[/\\])\.venv(?:[/\\]|$)/,
];

export interface FileWatcherOptions {
  syncRoot: string;
  connectorInstanceId: string;
  /** Called with batched file events to send to backend. */
  dispatchFn: DispatchFn;
  /** Optional file extension filter (lowercase, no dot). Empty = allow all. */
  allowedExtensions?: string[];
  includeSubfolders?: boolean;
  /** Event correlation window ms. Default 250. */
  correlationWindowMs?: number;
  /** Change debounce ms. Default 300. */
  changeDebounceMs?: number;
  /** Batch dispatcher options. */
  batchOptions?: BatchDispatcherOptions;
  /** Use polling (for network drives). Default false. */
  usePolling?: boolean;
  /** Polling interval ms when usePolling is true. Default 1000. */
  pollInterval?: number;
  /** Log callback. Default console.log. */
  log?: (msg: string) => void;
  /** Auth dir for watcher state file. */
  authDir?: string;
  /** Debounce ms before rescanning disk to refresh watcher_state.json after events. Default 500. */
  stateSyncDebounceMs?: number;
  /** If true, journal marks batches as local_only (no backend). Default false. */
  journalLocalOnly?: boolean;
}

export type WatcherStatus = {
  running: boolean;
  syncRoot: string;
  connectorInstanceId: string;
  trackedFiles: number;
  pendingEvents: number;
};

export class FileWatcher {
  private readonly syncRoot: string;
  private readonly connectorInstanceId: string;
  private readonly includeSubfolders: boolean;
  private readonly allowedExtensions: Set<string>;
  private readonly log: (msg: string) => void;
  private readonly usePolling: boolean;
  private readonly pollInterval: number;
  private readonly stateSyncDebounceMs: number;
  private readonly journalLocalOnly: boolean;

  private watcher: FSWatcher | null = null;
  private stateSyncTimer: ReturnType<typeof setTimeout> | null = null;
  private periodicRescanTimer: ReturnType<typeof setInterval> | null = null;
  private correlator: EventCorrelator;
  private dispatcher: BatchDispatcher;
  private stateStore: WatcherStateStore;
  private running = false;
  private ready = false;

  constructor(opts: FileWatcherOptions) {
    this.syncRoot = path.resolve(opts.syncRoot);
    this.connectorInstanceId = opts.connectorInstanceId;
    this.includeSubfolders = opts.includeSubfolders !== false;
    this.allowedExtensions = new Set(
      (opts.allowedExtensions ?? []).map((e) => e.toLowerCase().replace(/^\./, ""))
    );
    this.log = opts.log ?? console.log;
    this.usePolling = opts.usePolling === true;
    this.pollInterval =
      typeof opts.pollInterval === "number" && opts.pollInterval > 0
        ? opts.pollInterval
        : 1000;
    this.stateSyncDebounceMs =
      typeof opts.stateSyncDebounceMs === "number" && opts.stateSyncDebounceMs >= 0
        ? opts.stateSyncDebounceMs
        : 500;
    this.journalLocalOnly = opts.journalLocalOnly === true;

    this.stateStore = new WatcherStateStore({
      syncRoot: this.syncRoot,
      connectorInstanceId: this.connectorInstanceId,
      authDir: opts.authDir,
    });

    const userDispatch = opts.dispatchFn;
    const wrappedDispatch: DispatchFn = async (batch, meta) => {
      const auth = this.stateStore.authDirPath();
      const replayStateFiles = this.cloneStateFiles(this.stateStore.getSnapshot().files);
      const replayEvents = expandWatchEventsForReplay(batch, replayStateFiles);
      try {
        await userDispatch(batch, meta);
      } catch (err) {
        try {
          recordWatchBatch(auth, {
            connectorInstanceId: this.connectorInstanceId,
            syncRoot: this.syncRoot,
            batchId: meta.batchId,
            source: meta.source,
            events: batch,
            backendStatus: "failed",
            replayEvents,
          });
        } catch {
          /* journal */
        }
        throw err;
      }
      recordWatchBatch(auth, {
        connectorInstanceId: this.connectorInstanceId,
        syncRoot: this.syncRoot,
        batchId: meta.batchId,
        source: meta.source,
        events: batch,
        backendStatus: this.journalLocalOnly ? "pending" : "synced",
        replayEvents,
      });
    };

    this.dispatcher = new BatchDispatcher(wrappedDispatch, opts.batchOptions);

    this.correlator = new EventCorrelator({
      syncRoot: this.syncRoot,
      correlationWindowMs: opts.correlationWindowMs,
      changeDebounceMs: opts.changeDebounceMs,
      shouldSuppressModifiedChange: async (ev) => {
        if (ev.isDirectory) return false;
        const prev = this.stateStore.getSnapshot().files[ev.relKey];
        if (!prev || prev.isDirectory || !prev.quickHash) return false;
        const cur = await contentQuickHash(ev.absPath);
        return cur !== undefined && cur === prev.quickHash;
      },
    });

    // Correlator → filter → dispatcher (backend). State file is refreshed from disk on raw
    // chokidar events (below) so watcher_state.json stays accurate even when correlation
    // or extension filters emit nothing.
    this.correlator.setListener((events) => {
      const filtered = this.applyFilters(events);
      if (filtered.length > 0) {
        this.logEvents(filtered);
        this.dispatcher.push(filtered);
      }
    });
  }

  private cloneStateFiles(
    files: Record<string, FileEntry>
  ): Record<string, FileEntry> {
    return Object.fromEntries(
      Object.entries(files).map(([relPath, entry]) => [relPath, { ...entry }])
    );
  }

  private scanOptions(): {
    includeSubfolders: boolean;
    previousByRelPath: Map<string, FileEntry>;
  } {
    return {
      includeSubfolders: this.includeSubfolders,
      previousByRelPath: new Map(
        Object.entries(this.stateStore.getSnapshot().files)
      ),
    };
  }

  /** Refresh persisted snapshot from a full tree scan (matches disk after correlated events). */
  private scheduleStateSyncFromDisk(): void {
    if (this.stateSyncTimer) {
      clearTimeout(this.stateSyncTimer);
    }
    this.stateSyncTimer = setTimeout(() => {
      this.stateSyncTimer = null;
      void this.syncStateFromDisk();
    }, this.stateSyncDebounceMs);
  }

  private async syncStateFromDisk(): Promise<void> {
    try {
      const scan = await scanSyncRoot(this.syncRoot, this.scanOptions());
      this.stateStore.applyScan(scan);
      this.stateStore.flushSave();
    } catch (err) {
      this.log(
        `State rescan error: ${err instanceof Error ? err.message : String(err)}`
      );
    }
  }

  async start(): Promise<void> {
    if (this.running) return;

    // Validate sync root
    if (!fs.existsSync(this.syncRoot) || !fs.statSync(this.syncRoot).isDirectory()) {
      throw new Error(`Sync root does not exist or is not a directory: ${this.syncRoot}`);
    }

    this.running = true;
    this.log(`Starting file watcher on: ${this.syncRoot}`);

    // Load persisted state and do startup reconciliation
    this.stateStore.load();
    const prevState = this.stateStore.getSnapshot();
    const hasPreviousState = Object.keys(prevState.files).length > 0;

    if (hasPreviousState) {
      this.log("Performing startup reconciliation...");
      const currentScan = await scanSyncRoot(this.syncRoot, this.scanOptions());
      const offlineEvents = this.stateStore.commitReconcile(currentScan);
      if (offlineEvents.length > 0) {
        const filtered = this.applyFilters(offlineEvents);
        if (filtered.length > 0) {
          this.log(`Found ${filtered.length} change(s) since last run.`);
          this.logEvents(filtered);
          this.dispatcher.push(filtered, { source: "reconcile" });
        }
      } else {
        this.log("No changes since last run.");
      }
    }

    // Start chokidar
    const depth = this.includeSubfolders ? undefined : 0;

    this.watcher = watch(this.syncRoot, {
      ignored: IGNORED_PATTERNS,
      persistent: true,
      ignoreInitial: true, // We handle initial state via reconciliation
      followSymlinks: false,
      depth,
      alwaysStat: true,
      usePolling: this.usePolling,
      interval: this.pollInterval,
      awaitWriteFinish: {
        stabilityThreshold: 200,
        pollInterval: 100,
      },
      ignorePermissionErrors: true,
      atomic: 200,
    });

    this.watcher.on("all", (eventName, filePath, stats) => {
      if (!this.ready) return; // Ignore events during initial scan
      const rawType = eventName as RawEventType;
      if (!["add", "addDir", "unlink", "unlinkDir", "change"].includes(rawType)) {
        return;
      }
      this.scheduleStateSyncFromDisk();
      void this.correlator.push(rawType, filePath, stats);
    });

    this.watcher.on("ready", () => {
      this.ready = true;
      // If no previous state, capture initial scan as baseline
      if (!hasPreviousState) {
        this.log("Initial scan complete. Capturing baseline state...");
        void this.captureBaseline();
      } else {
        this.log("Watcher ready. Monitoring for changes...");
      }

      // Background watcher (pipeshub run): full-tree rescan on an interval so
      // watcher_state.json converges if FSEvents/chokidar misses events.
      if (process.env.PIPESHUB_WATCH_DAEMON === "1") {
        const ms = parseInt(
          process.env.PIPESHUB_WATCH_RESCAN_MS || "15000",
          10
        );
        if (Number.isFinite(ms) && ms >= 5000) {
          this.periodicRescanTimer = setInterval(() => {
            void this.syncStateFromDisk();
          }, ms);
        }
      }
    });

    this.watcher.on("error", (err) => {
      this.log(`Watcher error: ${err instanceof Error ? err.message : String(err)}`);
    });
  }

  async stop(): Promise<void> {
    if (!this.running) return;
    this.log("Stopping file watcher...");

    if (this.stateSyncTimer) {
      clearTimeout(this.stateSyncTimer);
      this.stateSyncTimer = null;
    }
    if (this.periodicRescanTimer) {
      clearInterval(this.periodicRescanTimer);
      this.periodicRescanTimer = null;
    }

    await this.correlator.drain();

    // Flush dispatcher
    await this.dispatcher.flush();

    // Close chokidar
    if (this.watcher) {
      await this.watcher.close();
      this.watcher = null;
    }

    await this.syncStateFromDisk();

    // Save final state
    this.stateStore.flushSave();

    this.running = false;
    this.ready = false;
    this.log("File watcher stopped.");
  }

  getStatus(): WatcherStatus {
    const state = this.stateStore.getSnapshot();
    return {
      running: this.running,
      syncRoot: this.syncRoot,
      connectorInstanceId: this.connectorInstanceId,
      trackedFiles: Object.keys(state.files).length,
      pendingEvents: this.dispatcher.pending,
    };
  }

  /** Scan the sync root and save as baseline without emitting events. */
  private async captureBaseline(): Promise<void> {
    try {
      const scan = await scanSyncRoot(this.syncRoot, this.scanOptions());
      this.stateStore.applyScan(scan);
      this.stateStore.flushSave();
      this.log(`Baseline captured: ${scan.size} file(s)/folder(s) tracked.`);
      this.log("Monitoring for changes...");
    } catch (err) {
      this.log(`Baseline scan error: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  /** Apply extension filters. */
  private applyFilters(events: FileEvent[]): FileEvent[] {
    if (this.allowedExtensions.size === 0) return events;
    return events.filter((ev) => {
      // Always allow directory events
      if (ev.isDirectory) return true;
      const ext = path.extname(ev.path).replace(/^\./, "").toLowerCase();
      if (!ext) return true; // No extension → allow
      return this.allowedExtensions.has(ext);
    });
  }

  private logEvents(events: FileEvent[]): void {
    for (const ev of events) {
      const old = ev.oldPath ? ` (was: ${ev.oldPath})` : "";
      const size = ev.size !== undefined ? ` [${formatSize(ev.size)}]` : "";
      this.log(`  ${ev.type}: ${ev.path}${old}${size}`);
    }
  }

  /**
   * Refresh the watcher state from disk.
   * Useful after a full sync to re-baseline.
   */
  async rescan(): Promise<FileEvent[]> {
    this.log("Performing manual rescan...");
    const scan = await scanSyncRoot(this.syncRoot, this.scanOptions());
    const events = this.stateStore.commitReconcile(scan);
    this.stateStore.flushSave();
    return events;
  }
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}
