import * as fs from 'fs';
import * as path from 'path';
import { ConnectorFsWatcher, type BatchPayload } from './watcher/connector-fs-watcher';
import { LocalSyncJournal, type ConnectorMeta, type JournalRecord } from './persistence/journal';
import { expandWatchEventsForReplay, type WatchEvent } from './watcher/replay-event-expander';
import {
  decodeCursor,
  encodeCursor,
  pullFailure,
  type ServePullPage,
  type ServePullRequest,
  type ServePullResponse,
  type SyncMode,
} from './pull-responder-types';
import {
  connectorFileSegment,
  scanSyncRoot,
  type FileSnapshotMap,
} from './persistence/watcher-state-store';
import { IGNORED_PATTERNS } from './watcher/ignored-patterns';

/** Bounded history so a straggler from an abandoned run gets STALE_RUN, not a restart. */
const MAX_REMEMBERED_SUPERSEDED_RUNS = 8;

export interface BootstrapResult {
  connectorId: string;
  ok: boolean;
  error?: string;
}

export interface LocalSyncManagerOptions {
  app: Pick<Electron.App, 'getPath'>;
  onStatusChange?: (status: ConnectorStatus) => void;
}

export interface StartArgs {
  connectorId: string;
  connectorName?: string;
  rootPath: string;
  apiBaseUrl?: string;
  includeSubfolders?: boolean;
  connectorDisplayType?: string;
  syncStrategy?: 'MANUAL' | 'SCHEDULED';
}

interface RuntimeState {
  connectorId: string;
  connectorName?: string;
  rootPath: string;
  normalizedRootPath: string;
  apiBaseUrl?: string;
  connectorDisplayType?: string;
  syncStrategy: 'MANUAL' | 'SCHEDULED';
  watcher: ConnectorFsWatcher | null;
  watcherState: 'starting' | 'watching' | 'stopped';
  lastError: string | null;
  startSignature: string;
}

/**
 * One server-driven run. Held in memory only: if the app restarts mid-run the
 * next page cannot be resolved and the desktop answers CURSOR_UNKNOWN, which
 * the connector recovers from by restarting the run as FULL.
 */
interface RunState {
  runId: string;
  mode: SyncMode;
  /** FULL: the materialised sorted walk, plus a path -> index for cursor lookup. */
  fullWalk: WatchEvent[] | null;
  fullWalkIndex: Map<string, number> | null;
  /**
   * FULL: journal head captured *before* the walk. Edits journaled during the
   * walk are handed back incrementally rather than assumed covered by it.
   */
  fullHandoffBatchId: string | null;
  /** Byte-identical answer to a repeated (runId, batchIndex). */
  lastPage: { batchIndex: number; response: ServePullPage } | null;
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
  /** Set when another machine holds this connector's registration. */
  ownedByOtherDevice: boolean;
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
  includeSubfolders?: boolean;
  connectorDisplayType?: string;
  syncStrategy?: 'MANUAL' | 'SCHEDULED';
}

/**
 * Identity for an in-flight runtime — everything that requires recreating the
 * watcher. Renderer effects refire often (Zustand list updates change array
 * refs), so start() with an unchanged config must be a no-op rather than a
 * teardown-and-remount that discards the correlator's buffered events.
 */
function buildRuntimeSignature(args: RuntimeSignatureArgs): string {
  return JSON.stringify({
    rootPath: path.resolve(String(args.rootPath || '')),
    includeSubfolders: args.includeSubfolders !== false,
    connectorDisplayType: String(args.connectorDisplayType || ''),
    syncStrategy: args.syncStrategy === 'SCHEDULED' ? 'SCHEDULED' : 'MANUAL',
  });
}

/**
 * Owns the local watchers and answers the server's pull.
 *
 * Nothing here initiates a network call: the connector service drives every
 * sync and this class only responds. Live watcher events accumulate in the
 * journal as `pending` and are handed out one page at a time.
 */
export class LocalSyncManager {
  app: Pick<Electron.App, 'getPath'>;
  onStatusChange?: (status: ConnectorStatus) => void;
  baseDir: string;
  journal: LocalSyncJournal;
  private runtimes: Map<string, RuntimeState>;
  private rootPathStartLocks: Map<string, RootPathLock>;
  private runs: Map<string, RunState>;
  private supersededRuns: Map<string, string[]>;
  /** Serialises pulls per connector so a slow FULL walk can't race the next page. */
  private pullChain: Map<string, Promise<unknown>>;
  private foreignOwnedConnectors: Set<string>;

  constructor({ app, onStatusChange }: LocalSyncManagerOptions) {
    this.app = app;
    this.onStatusChange = onStatusChange;
    this.baseDir = path.join(this.app.getPath('userData'), 'local-sync-journal');
    this.journal = new LocalSyncJournal(this.baseDir);
    this.runtimes = new Map();
    this.rootPathStartLocks = new Map();
    this.runs = new Map();
    this.supersededRuns = new Map();
    this.pullChain = new Map();
    this.foreignOwnedConnectors = new Set();
  }

  /** Connector ids this machine has a configured folder for — the register set. */
  listConnectorIds(): string[] {
    return this.journal
      .listConnectorIds()
      .filter((id) => Boolean(this.journal.getMeta(id)?.rootPath));
  }

  /** Record which connectors another machine already claimed, for the UI. */
  setForeignOwnedConnectors(connectorIds: string[]): void {
    this.foreignOwnedConnectors = new Set(connectorIds);
    for (const connectorId of connectorIds) this.emitStatus(connectorId);
  }

  getRootPath(connectorId: string): string | null {
    return this.journal.getMeta(connectorId)?.rootPath || null;
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

  /** Read-only lookup backing both the throwing check in start() and the
   * renderer's pre-activation preflight (checkRootPathConflict) — sets no
   * locks, so calling it repeatedly (e.g. on every keystroke while editing a
   * path) has no side effects. */
  private findRootPathConflict(
    normalizedRootPath: string,
    connectorId: string,
  ): RootPathLock | null {
    const existingRuntime = this.findRuntimeByRootPath(normalizedRootPath, connectorId);
    if (existingRuntime) {
      return { connectorId: existingRuntime.connectorId, connectorName: existingRuntime.connectorName };
    }
    const lock = this.rootPathStartLocks.get(normalizedRootPath);
    if (lock && lock.connectorId !== connectorId) {
      return lock;
    }
    return null;
  }

  private assertRootPathAvailable(normalizedRootPath: string, connectorId: string): void {
    const conflict = this.findRootPathConflict(normalizedRootPath, connectorId);
    if (conflict) {
      const owner = conflict.connectorName || conflict.connectorId;
      throw new Error(`Local sync root is already watched by connector "${owner}": ${normalizedRootPath}`);
    }
  }

  /**
   * Preflight for the renderer to call *before* activating a Local FS
   * connector (toggling sync on / creating it) — lets the caller reject the
   * activation outright instead of flipping the connector active in the
   * backend and only discovering the root-path conflict when the watcher
   * fails to start afterwards.
   *
   * This is per-machine by construction. Two laptops signed into one account
   * cannot see each other here; that case is caught by socket registration.
   */
  checkRootPathConflict(
    connectorId: string,
    rootPath: string,
  ): { available: boolean; ownerConnectorId?: string; ownerConnectorName?: string } {
    if (!connectorId || !rootPath) return { available: true };
    const normalizedRootPath = normalizeSyncRootPath(rootPath);
    const conflict = this.findRootPathConflict(normalizedRootPath, connectorId);
    if (!conflict) return { available: true };
    return {
      available: false,
      ownerConnectorId: conflict.connectorId,
      ownerConnectorName: conflict.connectorName,
    };
  }

  async start({
    connectorId, connectorName, rootPath, apiBaseUrl,
    includeSubfolders,
    connectorDisplayType,
    syncStrategy,
  }: StartArgs): Promise<ConnectorStatus> {
    if (!connectorId) throw new Error('connectorId is required');
    if (!rootPath) throw new Error('rootPath is required');

    const startSignature = buildRuntimeSignature({
      rootPath, includeSubfolders, connectorDisplayType, syncStrategy,
    });
    const existing = this.runtimes.get(connectorId);
    if (
      existing
      && existing.startSignature === startSignature
      && (existing.watcherState === 'watching' || existing.watcherState === 'starting')
    ) {
      this.emitStatus(connectorId);
      return this.getStatus(connectorId) as ConnectorStatus;
    }

    const normalizedRootPath = normalizeSyncRootPath(rootPath);
    this.assertRootPathAvailable(normalizedRootPath, connectorId);
    this.rootPathStartLocks.set(normalizedRootPath, { connectorId, connectorName });

    await this.stop(connectorId);

    const strategy: 'MANUAL' | 'SCHEDULED' = syncStrategy || 'MANUAL';
    this.journal.setMeta(connectorId, {
      connectorName, rootPath, apiBaseUrl,
      includeSubfolders,
      connectorDisplayType, syncStrategy: strategy,
    });

    const runtime: RuntimeState = {
      connectorId, connectorName, rootPath, normalizedRootPath, apiBaseUrl,
      connectorDisplayType,
      syncStrategy: strategy,
      watcher: null, watcherState: 'starting', lastError: null,
      startSignature,
    };
    this.runtimes.set(connectorId, runtime);
    this.rootPathStartLocks.delete(normalizedRootPath);

    const processBatch = async ({ batchId, timestamp, events, source }: BatchPayload): Promise<void> => {
      // Already expanded to file granularity by the watcher, against its live
      // in-memory state — re-expanding here would duplicate every child, since
      // the directory event stays in the array and this side can only read the
      // debounced on-disk snapshot. Recording it as replayEvents is what makes
      // resolveBatchEvents skip its own expansion when the batch is served.
      const replayEvents = (events || []).some((e) => e.isDirectory) ? events : undefined;
      this.journal.appendBatch(connectorId, { batchId, timestamp, events, source, replayEvents });
      runtime.lastError = null;
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

    try {
      await runtime.watcher.start();
    } catch (error) {
      // A runtime left registered after a failed start (missing folder,
      // unmounted network drive) still answers findRuntimeByRootPath, so it
      // would hold the sync root against every later attempt — including the
      // retry that would have succeeded.
      this.runtimes.delete(connectorId);
      runtime.watcherState = 'stopped';
      runtime.lastError = error instanceof Error ? error.message : String(error);
      this.emitStatus(connectorId);
      throw error;
    }
    runtime.watcherState = 'watching';

    this.emitStatus(connectorId);
    return this.getStatus(connectorId) as ConnectorStatus;
  }

  async stop(connectorId: string): Promise<ConnectorStatus | null> {
    if (!connectorId) return null;
    const runtime = this.runtimes.get(connectorId);
    if (!runtime) return this.getStatus(connectorId) as ConnectorStatus;
    if (runtime.watcher) {
      try { await runtime.watcher.stop(); } catch { /* ignore */ }
    }
    runtime.watcherState = 'stopped';
    this.runtimes.delete(connectorId);
    this.emitStatus(connectorId);
    return this.getStatus(connectorId) as ConnectorStatus;
  }

  /**
   * Forget a connector entirely: stop its watcher and delete its journal,
   * cursor and watcher state.
   *
   * Distinct from `stop()`, which only unmounts. Deleting a connector has to
   * purge the journal too — `bootstrapFromJournal` mounts a watcher for every
   * connector the journal knows about, so a leftover meta file resurrects the
   * deleted connector on the next launch and its watcher then holds the sync
   * root against any new connector pointed at the same folder.
   */
  async remove(connectorId: string): Promise<void> {
    if (!connectorId) return;
    await this.stop(connectorId);
    this.journal.removeConnector(connectorId);
    const statePath = path.join(
      this.baseDir,
      `watcher_state.${connectorFileSegment(connectorId)}.json`,
    );
    try {
      if (fs.existsSync(statePath)) fs.unlinkSync(statePath);
    } catch (error) {
      console.warn(`[local-sync] could not remove watcher state for ${connectorId}:`, error);
    }
    this.runs.delete(connectorId);
    this.supersededRuns.delete(connectorId);
    this.pullChain.delete(connectorId);
    this.foreignOwnedConnectors.delete(connectorId);
  }

  /**
   * Drop journal state for connectors the backend no longer lists.
   *
   * `liveConnectorIds` must be *every* Local FS instance that exists, not just
   * the ones eligible to mount — reaping against an activity-filtered list
   * would throw away the pending events of a connector that was merely toggled
   * off. Returns the ids actually removed.
   */
  async reap(liveConnectorIds: string[]): Promise<string[]> {
    const live = new Set(liveConnectorIds);
    const removed: string[] = [];
    for (const connectorId of this.journal.listConnectorIds()) {
      if (live.has(connectorId)) continue;
      await this.remove(connectorId);
      removed.push(connectorId);
    }
    if (removed.length > 0) {
      console.log(`[local-sync] reaped deleted connector(s): ${removed.join(', ')}`);
    }
    return removed;
  }

  // ── Pull responder ───────────────────────────────────────────────────────

  /**
   * Answer one page of a server-driven run.
   *
   * A page is *not* marked synced when it is served. The server asking for the
   * next page — i.e. arriving with a cursor past this batch — is the ack.
   * These are Kafka consumer-offset semantics; committing on send would lose
   * data whenever a run dies mid-flight.
   */
  servePull(request: ServePullRequest): Promise<ServePullResponse> {
    const connectorId = String(request?.connectorId || '');
    if (!connectorId) {
      return Promise.resolve(
        pullFailure(
          { connectorId, runId: request?.runId || '', batchIndex: request?.batchIndex ?? 0 },
          'CONFIG_MISMATCH',
          'pull request carried no connectorId',
          false,
        ),
      );
    }
    const prev = this.pullChain.get(connectorId) || Promise.resolve();
    const promise = prev
      .catch(() => { /* a prior page's failure must not block this one */ })
      .then(() => this.servePullInner(request));
    this.pullChain.set(connectorId, promise.catch(() => {}));
    return promise;
  }

  private async servePullInner(request: ServePullRequest): Promise<ServePullResponse> {
    const { connectorId, runId, batchIndex, mode } = request;
    const meta = this.journal.getMeta(connectorId);
    if (!meta || !meta.rootPath) {
      return pullFailure(
        request,
        'CONFIG_MISMATCH',
        `connector ${connectorId} has no sync folder configured on this machine`,
        false,
      );
    }
    if ((this.supersededRuns.get(connectorId) || []).includes(runId)) {
      return pullFailure(request, 'STALE_RUN', `run ${runId} was superseded`, false);
    }

    let run = this.runs.get(connectorId);
    if (!run || run.runId !== runId || run.mode !== mode) {
      // A runId we have not seen is by definition the newer one — it arrived
      // later. The run it replaces goes on the superseded list so its
      // stragglers get STALE_RUN instead of silently resurrecting it.
      if (run && run.runId !== runId) this.markSuperseded(connectorId, run.runId);
      run = {
        runId,
        mode,
        fullWalk: null,
        fullWalkIndex: null,
        fullHandoffBatchId: null,
        lastPage: null,
      };
      this.runs.set(connectorId, run);
    }

    if (run.lastPage) {
      // A deliberate re-send of the same index (the connector retries on
      // timeout): answer from cache and do not advance.
      if (run.lastPage.batchIndex === batchIndex) return run.lastPage.response;
      if (batchIndex < run.lastPage.batchIndex) {
        return pullFailure(
          request,
          'CURSOR_UNKNOWN',
          `batch ${batchIndex} is behind the last served page ${run.lastPage.batchIndex}`,
          false,
        );
      }
    }

    let page: ServePullResponse;
    try {
      page =
        mode === 'FULL'
          ? await this.serveFullPage(request, run, meta)
          : await this.serveIncrementalPage(request, run, meta);
    } catch (error) {
      return pullFailure(
        request,
        'ROOT_UNREADABLE',
        error instanceof Error ? error.message : String(error),
        true,
      );
    }
    if (page.ok) {
      run.lastPage = { batchIndex, response: page };
      // Nothing more will be read from a finished walk, and it can be large.
      if (!page.hasMore) {
        run.fullWalk = null;
        run.fullWalkIndex = null;
      }
    }
    return page;
  }

  private markSuperseded(connectorId: string, runId: string): void {
    const history = this.supersededRuns.get(connectorId) || [];
    history.push(runId);
    while (history.length > MAX_REMEMBERED_SUPERSEDED_RUNS) history.shift();
    this.supersededRuns.set(connectorId, history);
  }

  private async serveIncrementalPage(
    request: ServePullRequest,
    _run: RunState,
    meta: ConnectorMeta,
  ): Promise<ServePullResponse> {
    const { connectorId, runId, batchIndex, maxEvents } = request;
    const watcher = await this.ensureWatcher(connectorId, meta);
    // Anything sitting in the correlator's 250ms window or the dispatcher's 1s
    // buffer has to reach the journal before we page over it, or an edit made
    // just before the pull slips to the next run.
    if (watcher) await watcher.drainLiveEvents();

    const batches = this.journal.listBatches(connectorId);
    let startIndex = 0;
    if (request.cursor) {
      const token = decodeCursor(request.cursor, 'INCREMENTAL');
      if (!token || token.mode !== 'INCREMENTAL') {
        return pullFailure(request, 'CURSOR_UNKNOWN', 'cursor is not an incremental token', false);
      }
      const idx = batches.findIndex((b) => b.batchId === token.afterBatchId);
      if (idx < 0) {
        return pullFailure(
          request,
          'CURSOR_UNKNOWN',
          `journal no longer holds batch ${token.afterBatchId}`,
          false,
        );
      }
      startIndex = idx + 1;
      // The cursor being past these batches *is* the server's ack for them.
      this.commitThrough(connectorId, batches, idx);
    }

    const events: WatchEvent[] = [];
    let consumedUpTo = startIndex - 1;
    for (let i = startIndex; i < batches.length; i += 1) {
      const batch = batches[i];
      const batchEvents = await this.resolveBatchEvents(connectorId, batch, meta.rootPath);
      // A single batch wider than maxEvents is still served whole: paging
      // inside a batch would need a sub-batch cursor, and stalling would wedge
      // the run.
      if (batchEvents.length > 0 && events.length > 0 && events.length + batchEvents.length > maxEvents) {
        break;
      }
      events.push(...batchEvents);
      consumedUpTo = i;
      if (events.length >= maxEvents) break;
    }

    const hasMore = consumedUpTo < batches.length - 1;
    const cursorBatchId = consumedUpTo >= 0 ? batches[consumedUpTo].batchId : null;
    return {
      ok: true,
      connectorId,
      runId,
      batchIndex,
      cursor: cursorBatchId
        ? encodeCursor({ v: 1, mode: 'INCREMENTAL', afterBatchId: cursorBatchId })
        : null,
      hasMore,
      events,
      rootPath: meta.rootPath || null,
    };
  }

  private async serveFullPage(
    request: ServePullRequest,
    run: RunState,
    meta: ConnectorMeta,
  ): Promise<ServePullResponse> {
    const { connectorId, runId, batchIndex, maxEvents } = request;
    if (!run.fullWalk) {
      if (request.cursor) {
        // The walk lived in memory and this process no longer has it (restart,
        // or a superseded run). Guessing a resume point would silently drop
        // files, so make the server re-seed instead.
        return pullFailure(
          request,
          'CURSOR_UNKNOWN',
          'full-run walk is no longer held in memory',
          false,
        );
      }
      const watcher = await this.ensureWatcher(connectorId, meta);
      if (watcher) await watcher.drainLiveEvents();
      const batches = this.journal.listBatches(connectorId);
      run.fullHandoffBatchId = batches.length ? batches[batches.length - 1].batchId : null;
      run.fullWalk = await this.buildFullWalk(meta);
      run.fullWalkIndex = new Map(run.fullWalk.map((event, index) => [event.path, index]));
    }

    let start = 0;
    if (request.cursor) {
      const token = decodeCursor(request.cursor, 'FULL');
      if (!token || token.mode !== 'FULL') {
        return pullFailure(request, 'CURSOR_UNKNOWN', 'cursor is not a full-walk token', false);
      }
      const idx = run.fullWalkIndex?.get(token.afterPath);
      if (idx === undefined) {
        return pullFailure(
          request,
          'CURSOR_UNKNOWN',
          `${token.afterPath} is not part of this walk`,
          false,
        );
      }
      start = idx + 1;
    }

    const slice = run.fullWalk.slice(start, start + Math.max(1, maxEvents));
    const hasMore = start + slice.length < run.fullWalk.length;
    // The last FULL page hands off to an incremental position rather than
    // another walk token: the run is complete, and the connector persists this
    // cursor for its *next* run, which is INCREMENTAL. Returning a FULL token
    // there would fail its mode check every time and pin the connector to a
    // full sync forever.
    const cursor = hasMore
      ? encodeCursor({ v: 1, mode: 'FULL', afterPath: slice[slice.length - 1].path })
      : run.fullHandoffBatchId
        ? encodeCursor({ v: 1, mode: 'INCREMENTAL', afterBatchId: run.fullHandoffBatchId })
        : null;

    return {
      ok: true,
      connectorId,
      runId,
      batchIndex,
      cursor,
      hasMore,
      events: slice,
      rootPath: meta.rootPath || null,
    };
  }

  /**
   * Whole-disk snapshot in sorted relative-path order, so `afterPath` is a
   * stable resume key for the life of the run.
   */
  private async buildFullWalk(meta: ConnectorMeta): Promise<WatchEvent[]> {
    const rootPath = path.resolve(String(meta.rootPath || ''));
    if (!fs.existsSync(rootPath)) {
      throw new Error(`Local sync root folder does not exist: ${rootPath}`);
    }
    const scan = await scanSyncRoot(rootPath, {
      includeSubfolders: meta.includeSubfolders !== false,
      ignoredPatterns: IGNORED_PATTERNS,
    });
    const relPaths = Array.from(scan.keys()).sort();
    return relPaths.map((relPath) => {
      const entry = scan.get(relPath)!;
      return {
        type: entry.isDirectory ? 'DIR_CREATED' : 'CREATED',
        path: relPath,
        // The file's own mtime, not now(): the connector stamps records with
        // this and applies the modified/created sync filters against it.
        timestamp: Math.round(entry.mtimeMs) || Date.now(),
        ...(entry.isDirectory ? {} : { size: entry.size, sha256: entry.sha256 }),
        isDirectory: entry.isDirectory,
      } as WatchEvent;
    });
  }

  private async resolveBatchEvents(
    connectorId: string,
    batch: JournalRecord,
    rootPath: string | undefined,
  ): Promise<WatchEvent[]> {
    // Poison-pill guard: a batch set aside by an earlier build stays skipped
    // rather than blocking every page behind it forever.
    if (batch.status === 'quarantined') return [];
    if (Array.isArray(batch.replayEvents) && batch.replayEvents.length > 0) {
      return batch.replayEvents;
    }
    const events = batch.events || [];
    if (!events.some((e) => e.isDirectory)) return events;
    // Only reachable for batches journaled by a build that did not expand at
    // the watcher; current batches always carry replayEvents.
    return expandWatchEventsForReplay(
      events,
      loadWatcherStateFiles(this.baseDir, connectorId),
      rootPath,
    );
  }

  private commitThrough(
    connectorId: string,
    batches: JournalRecord[],
    lastIndex: number,
  ): void {
    const acked: string[] = [];
    for (let i = 0; i <= lastIndex && i < batches.length; i += 1) {
      const batch = batches[i];
      if (batch.status === 'pending' || batch.status === 'failed') acked.push(batch.batchId);
    }
    if (acked.length === 0) return;
    this.journal.markBatchesSynced(connectorId, acked);
    this.emitStatus(connectorId);
  }

  /**
   * Lazy mount. A pull can arrive with nothing watching — the UI no longer
   * mounts a watcher as a side effect of pressing Sync — and without a mounted
   * watcher an incremental page would report an empty journal forever. This is
   * the safety net; the boot bootstrap is the strategy.
   */
  private async ensureWatcher(
    connectorId: string,
    meta: ConnectorMeta,
  ): Promise<ConnectorFsWatcher | null> {
    const runtime = this.runtimes.get(connectorId);
    if (runtime?.watcher && runtime.watcherState === 'watching') return runtime.watcher;
    if (!meta.rootPath) return null;
    await this.start({
      connectorId,
      connectorName: meta.connectorName,
      rootPath: meta.rootPath,
      apiBaseUrl: meta.apiBaseUrl,
      includeSubfolders: meta.includeSubfolders,
      connectorDisplayType: meta.connectorDisplayType,
      syncStrategy: meta.syncStrategy,
    });
    return this.runtimes.get(connectorId)?.watcher || null;
  }

  // ── Lifecycle ────────────────────────────────────────────────────────────

  /**
   * Mount a watcher for every Local FS connector this machine knows about, so
   * the journal is warm between syncs and an incremental pull is cheap.
   * Connectors already running or whose meta lacks a rootPath are skipped.
   */
  async bootstrapFromJournal(): Promise<BootstrapResult[]> {
    const results: BootstrapResult[] = [];
    for (const connectorId of this.journal.listConnectorIds()) {
      if (this.runtimes.has(connectorId)) {
        results.push({ connectorId, ok: true });
        continue;
      }
      const meta = this.journal.getMeta(connectorId);
      if (!meta || !meta.rootPath) continue;
      try {
        await this.start({
          connectorId,
          connectorName: meta.connectorName,
          rootPath: meta.rootPath,
          apiBaseUrl: meta.apiBaseUrl,
          includeSubfolders: meta.includeSubfolders,
          connectorDisplayType: meta.connectorDisplayType,
          syncStrategy: meta.syncStrategy,
        });
        results.push({ connectorId, ok: true });
      } catch (error) {
        console.warn(`[local-sync] bootstrap start failed for ${connectorId}:`, error);
        results.push({
          connectorId,
          ok: false,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    }
    return results;
  }

  /** Stop all active watchers. Called on app quit. */
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
      const meta = this.journal.getMeta(connectorId) || {};
      return {
        connectorId,
        watcherState: (runtime && runtime.watcherState) || 'stopped',
        rootPath: (runtime && runtime.rootPath) || meta.rootPath || null,
        lastError: (runtime && runtime.lastError) || null,
        trackedFiles: watcherStatus ? watcherStatus.trackedFiles : null,
        pendingEvents: watcherStatus ? watcherStatus.pendingEvents : null,
        lastAckBatchId: cursor.lastAckBatchId || null,
        lastRecordedBatchId: cursor.lastRecordedBatchId || null,
        syncStrategy: (runtime && runtime.syncStrategy) || meta.syncStrategy || 'MANUAL',
        ownedByOtherDevice: this.foreignOwnedConnectors.has(connectorId),
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
