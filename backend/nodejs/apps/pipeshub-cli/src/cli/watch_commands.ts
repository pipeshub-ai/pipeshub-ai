import { spawn } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import prompts from "prompts";
import { AuthManager } from "../auth/auth_manager";
import { CredentialStore } from "../auth/credential_store";
import {
  BackendClient,
  BackendClientError,
  FOLDER_SYNC_CONNECTOR_TYPE,
  FOLDER_SYNC_INCLUDE_SUBFOLDERS_KEY,
  FOLDER_SYNC_SYNC_ROOT_KEY,
} from "../api/backend_client";
import {
  daemonConfigComplete,
  loadDaemonConfig,
  saveDaemonConfig,
} from "../config/daemon_config";
import {
  readAllowedFileExtensionsFromEtcd,
  readEnableManualSyncFromEtcd,
  readIncludeSubfoldersFromEtcd,
} from "../sync/folder_sync_filters";
import { FileWatcher } from "../sync/file_watcher";
import type { FileEvent } from "../sync/watcher_state";
import { watcherEventsJournalPath } from "../sync/watcher_sync_journal";
import { replayPendingWatchBatches } from "../sync/watcher_resync_replayer";
import { createBackendClient } from "./context";
import { pickFolderSyncConnectorForRun } from "./folder_sync_connector_picker";
import { validateSyncRoot } from "./setup_commands";

const MIN_SCHEDULE_INTERVAL_MINUTES = 5;

type SyncStrategyChoice = "MANUAL" | "SCHEDULED";

function buildSyncPayloadForRun(args: {
  rootPath: string;
  includeSubfolders: boolean;
  strategy: SyncStrategyChoice;
  intervalMinutes?: number;
  etcd: Record<string, unknown>;
}): Record<string, unknown> {
  const top = args.etcd["sync"];
  const existingSync =
    top && typeof top === "object" && top !== null
      ? { ...(top as Record<string, unknown>) }
      : {};
  const existingScheduled = existingSync["scheduledConfig"];
  const scheduledBase =
    existingScheduled &&
    typeof existingScheduled === "object" &&
    existingScheduled !== null
      ? { ...(existingScheduled as Record<string, unknown>) }
      : {};
  const out: Record<string, unknown> = {
    [FOLDER_SYNC_SYNC_ROOT_KEY]: args.rootPath,
    [FOLDER_SYNC_INCLUDE_SUBFOLDERS_KEY]: args.includeSubfolders,
    selectedStrategy: args.strategy,
  };
  if (args.strategy === "SCHEDULED") {
    out.scheduledConfig = {
      ...scheduledBase,
      intervalMinutes: args.intervalMinutes ?? 60,
    };
  }
  return out;
}

function normalizedStrategyFromEtcd(etcd: Record<string, unknown>): SyncStrategyChoice {
  const sync = etcd["sync"] as Record<string, unknown> | undefined;
  const raw = String(sync?.selectedStrategy ?? "").toUpperCase();
  if (raw === "SCHEDULED") {
    return "SCHEDULED";
  }
  return "MANUAL";
}

function readIntervalMinutesFromEtcd(etcd: Record<string, unknown>): number | undefined {
  const sync = etcd["sync"] as Record<string, unknown> | undefined;
  const sc = sync?.scheduledConfig as Record<string, unknown> | undefined;
  const m = sc?.intervalMinutes;
  if (typeof m === "number" && Number.isFinite(m)) {
    return m;
  }
  return undefined;
}

/** Interactive MANUAL/SCHEDULED + interval; `null` if user cancelled. */
async function promptInteractiveSyncStrategy(): Promise<{
  strategy: SyncStrategyChoice;
  intervalMinutes: number;
} | null> {
  let strategy: SyncStrategyChoice = "MANUAL";
  let intervalMinutes = 60;
  const { syncMode } = await prompts({
    type: "select",
    name: "syncMode",
    message: "Server sync type",
    choices: [
      { title: "Manual (trigger sync from CLI/app)", value: "MANUAL" },
      { title: "Scheduled (repeat full sync on an interval)", value: "SCHEDULED" },
    ],
    initial: 0,
  });
  if (syncMode === undefined) {
    return null;
  }
  if (syncMode === "SCHEDULED") {
    strategy = "SCHEDULED";
    for (;;) {
      const { minutes } = await prompts({
        type: "number",
        name: "minutes",
        message: `Sync interval (minutes, minimum ${MIN_SCHEDULE_INTERVAL_MINUTES})`,
        initial: 60,
        min: MIN_SCHEDULE_INTERVAL_MINUTES,
      });
      if (minutes === undefined) {
        return null;
      }
      const m = Number(minutes);
      if (
        Number.isFinite(m) &&
        m >= MIN_SCHEDULE_INTERVAL_MINUTES &&
        Number.isInteger(m)
      ) {
        intervalMinutes = m;
        break;
      }
      console.log(
        `Enter a whole number ≥ ${MIN_SCHEDULE_INTERVAL_MINUTES}.`
      );
    }
  } else if (syncMode !== "MANUAL") {
    return null;
  }
  return { strategy, intervalMinutes };
}

/** Register or clear BullMQ repeat job so scheduled Folder Sync appears under crawling manager. */
async function applyFolderSyncCrawlingManagerSchedule(
  api: BackendClient,
  connectorId: string,
  strategy: SyncStrategyChoice,
  intervalMinutes: number,
  etcd: Record<string, unknown>
): Promise<void> {
  const sync = etcd["sync"] as Record<string, unknown> | undefined;
  const sc = sync?.scheduledConfig as Record<string, unknown> | undefined;
  const timezone =
    typeof sc?.timezone === "string" && sc.timezone.trim()
      ? sc.timezone.trim().toUpperCase()
      : "UTC";
  const startTime =
    typeof sc?.startTime === "number" && Number.isFinite(sc.startTime)
      ? sc.startTime
      : undefined;

  if (strategy === "SCHEDULED") {
    try {
      await api.scheduleCrawlingManagerJob(FOLDER_SYNC_CONNECTOR_TYPE, connectorId, {
        intervalMinutes,
        startTime,
        timezone,
      });
      console.log(
        `Crawling manager: registered repeat sync every ${intervalMinutes} min (Folder Sync).`
      );
    } catch (e) {
      if (e instanceof BackendClientError && (e.status === 401 || e.status === 403)) {
        console.warn(
          `Could not register crawling manager schedule (HTTP ${e.status}). ` +
            `Add CRAWL_WRITE to your OAuth client so scheduled sync runs on the server.`
        );
        return;
      }
      throw e;
    }
  } else {
    try {
      await api.removeCrawlingManagerJob(FOLDER_SYNC_CONNECTOR_TYPE, connectorId);
    } catch (e) {
      if (e instanceof BackendClientError && (e.status === 401 || e.status === 403)) {
        console.warn(
          `Could not remove crawling manager schedule (HTTP ${e.status}). CRAWL_DELETE may be required.`
        );
      }
    }
  }
}

/**
 * When manual-only indexing is enabled on the connector, keep server sync mode from config (no prompts).
 * Otherwise ask MANUAL vs SCHEDULED (and interval for scheduled).
 */
async function resolveSyncStrategyForRun(
  etcd: Record<string, unknown>
): Promise<{
  strategy: SyncStrategyChoice;
  intervalMinutes: number;
} | null> {
  if (readEnableManualSyncFromEtcd(etcd) === true) {
    const strategy = normalizedStrategyFromEtcd(etcd);
    const rawInterval = readIntervalMinutesFromEtcd(etcd);
    const intervalMinutes =
      strategy === "SCHEDULED"
        ? Math.max(
            MIN_SCHEDULE_INTERVAL_MINUTES,
            Math.floor(rawInterval ?? 60)
          )
        : 60;
    console.log(
      "Manual indexing only is on for this connector — using existing server sync mode from config (no prompt)."
    );
    return { strategy, intervalMinutes };
  }
  return promptInteractiveSyncStrategy();
}

export type WatchSpawnConfig = {
  connectorInstanceId: string;
  syncRoot: string;
  includeSubfolders: boolean;
  withBackend: boolean;
  allowedExtensions?: string[];
};

export function pipeshubConfigDir(): string {
  return path.join(os.homedir(), ".config", "pipeshub");
}

export function watcherPidPath(cid: string): string {
  return path.join(pipeshubConfigDir(), `watcher.${cid}.pid`);
}

function watcherSpawnConfigPath(cid: string): string {
  return path.join(pipeshubConfigDir(), `watch_spawn.${cid}.json`);
}

export function isProcessRunning(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function clearStaleWatcherPidIfDead(cid: string): void {
  const p = watcherPidPath(cid);
  if (!fs.existsSync(p)) return;
  const pid = Number(fs.readFileSync(p, "utf8").trim());
  if (!Number.isFinite(pid) || pid <= 0) {
    try {
      fs.unlinkSync(p);
    } catch {
      /* ignore */
    }
    return;
  }
  if (!isProcessRunning(pid)) {
    try {
      fs.unlinkSync(p);
    } catch {
      /* ignore */
    }
  }
}

const STALE_SPAWN_LOCK_MS = 120_000;

/** Serialize background watcher spawn per connector (O_EXCL lock + stale cleanup). */
function withWatcherSpawnLock<T>(cid: string, fn: () => T): T {
  const dir = pipeshubConfigDir();
  fs.mkdirSync(dir, { recursive: true });
  const lockPath = path.join(dir, `watcher.${cid}.spawn.lock`);
  clearStaleWatcherPidIfDead(cid);

  let fd: number;
  try {
    fd = fs.openSync(lockPath, "wx", 0o600);
  } catch (e) {
    const err = e as NodeJS.ErrnoException;
    if (err.code === "EEXIST") {
      let stale = false;
      try {
        const st = fs.statSync(lockPath);
        stale = Date.now() - st.mtimeMs > STALE_SPAWN_LOCK_MS;
      } catch {
        stale = true;
      }
      if (stale) {
        try {
          fs.unlinkSync(lockPath);
        } catch {
          /* ignore */
        }
        fd = fs.openSync(lockPath, "wx", 0o600);
      } else {
        const pp = watcherPidPath(cid);
        if (fs.existsSync(pp)) {
          const pid = Number(fs.readFileSync(pp, "utf8").trim());
          if (Number.isFinite(pid) && pid > 0 && isProcessRunning(pid)) {
            throw new Error(
              `Background watcher already running (PID ${pid}). Stop with: pipeshub watch-stop`
            );
          }
        }
        throw new Error(
          "Another watcher is starting for this connector. Wait a moment and try again."
        );
      }
    } else {
      throw e;
    }
  }

  try {
    return fn();
  } finally {
    try {
      fs.closeSync(fd);
    } catch {
      /* ignore */
    }
    try {
      fs.unlinkSync(lockPath);
    } catch {
      /* ignore */
    }
  }
}

function pathToCliScript(): string {
  return path.join(__dirname, "..", "cli.js");
}

function spawnWatchWorker(config: WatchSpawnConfig): number {
  return withWatcherSpawnLock(config.connectorInstanceId, () => {
    fs.mkdirSync(pipeshubConfigDir(), { recursive: true });
    const configPath = watcherSpawnConfigPath(config.connectorInstanceId);
    fs.writeFileSync(configPath, JSON.stringify(config), { mode: 0o600 });
    const child = spawn(
      process.execPath,
      [pathToCliScript(), "watch-worker", "--config", configPath, "--quiet"],
      { detached: true, stdio: "ignore" }
    );
    child.unref();
    if (child.pid === undefined) {
      try {
        fs.unlinkSync(configPath);
      } catch {
        /* ignore */
      }
      throw new Error("Failed to spawn background watcher process.");
    }
    fs.writeFileSync(watcherPidPath(config.connectorInstanceId), String(child.pid), {
      mode: 0o600,
    });
    return child.pid;
  });
}

function printWatcherStartedBackground(cid: string, pid: number): void {
  const dir = pipeshubConfigDir();
  const statePath = path.join(dir, `watcher_state.${cid}.json`);
  const eventsPath = watcherEventsJournalPath(dir, cid);
  console.log(
    `\nWatching in the background (PID ${pid}). This process exits; file changes are written to:\n`
  );
  console.log(`  State:  ${statePath}`);
  console.log(`  Events: ${eventsPath}`);
  console.log(`\nStop the watcher: pipeshub watch-stop\n`);
}

export async function runSyncAsync(
  manager: AuthManager | undefined,
  opts: { rootOverride?: string; withBackend?: boolean; foreground?: boolean }
): Promise<void> {
  const withBackend = opts.withBackend === true;
  const foreground = opts.foreground === true;
  const dc = loadDaemonConfig();

  const loggedIn = Boolean(manager && (await manager.isLoggedIn()));

  let cid: string;
  let rootPath: string;
  let includeSubfolders: boolean;
  let allowedExtensions: string[] | undefined;
  let isActive = false;
  /** Connector etcd blob (for merging sync strategy on PUT). */
  let etcdForSync: Record<string, unknown> = {};
  let api: BackendClient | undefined;
  let base: string | undefined;

  if (loggedIn && manager) {
    const client = await createBackendClient(manager);
    api = client.api;
    base = client.base;
    const picked = await pickFolderSyncConnectorForRun(api);
    cid = picked.id.trim();
    if (opts.rootOverride?.trim()) {
      rootPath = validateSyncRoot(opts.rootOverride);
    } else {
      rootPath = validateSyncRoot(picked.syncRoot);
    }
    try {
      const cfg = await api.getConnectorConfig(cid);
      isActive = cfg.isActive;
      etcdForSync = cfg.etcd;
      const fromEtcd = readIncludeSubfoldersFromEtcd(cfg.etcd);
      includeSubfolders = fromEtcd !== undefined ? fromEtcd : true;
      allowedExtensions = readAllowedFileExtensionsFromEtcd(cfg.etcd);
    } catch (e) {
      if (e instanceof BackendClientError) {
        console.warn(
          `Could not read connector config (HTTP ${e.status ?? "?"}). ` +
            `Using include_subfolders default true.`
        );
        try {
          const inst = await api.getConnectorInstance(cid);
          isActive = inst.isActive;
        } catch (e2) {
          throw e2;
        }
        includeSubfolders = true;
        allowedExtensions = undefined;
        etcdForSync = {};
      } else {
        throw e;
      }
    }
    saveDaemonConfig({
      sync_root: rootPath,
      connector_instance_id: cid,
      include_subfolders: includeSubfolders,
    });
  } else {
    if (!daemonConfigComplete(dc)) {
      throw new Error(
        "No sync root or connector in config. Run: pipeshub login (to pick a connector) or pipeshub setup."
      );
    }
    cid = dc.connector_instance_id.trim();
    if (opts.rootOverride?.trim()) {
      rootPath = validateSyncRoot(opts.rootOverride);
    } else if (dc.sync_root?.trim()) {
      rootPath = validateSyncRoot(dc.sync_root);
    } else {
      throw new Error(
        "No sync root. Run: pipeshub setup  (or pass a path argument)."
      );
    }
    includeSubfolders = dc.include_subfolders ?? true;
    allowedExtensions = undefined;
  }

  if (!withBackend) {
    const { ok } = await prompts({
      type: "confirm",
      name: "ok",
      message: `Watch this folder?\n  ${rootPath}\n  Include subfolders: ${includeSubfolders ? "yes" : "no"}`,
      initial: true,
    });
    if (ok !== true) {
      console.log("Cancelled.");
      return;
    }

    /** Logged-in run: server sync mode + path, enable/resync, then local watch (no file-event POST). */
    if (loggedIn && api) {
      const resolved = await resolveSyncStrategyForRun(etcdForSync);
      if (!resolved) {
        console.log("Cancelled.");
        return;
      }
      const { strategy, intervalMinutes } = resolved;
      const fullSync = true;
      const syncPayload = buildSyncPayloadForRun({
        rootPath,
        includeSubfolders,
        strategy,
        intervalMinutes:
          strategy === "SCHEDULED" ? intervalMinutes : undefined,
        etcd: etcdForSync,
      });
      try {
        await api.updateConnectorFiltersSync(cid, {
          sync: syncPayload,
        });
      } catch (e) {
        if (e instanceof BackendClientError) {
          const detail = `${e.message}`.toLowerCase();
          const activeHint =
            e.status === 400 && detail.includes("active")
              ? " Disable the connector in the app to update path or sync mode on the server, or continue if settings already match."
              : "";
          console.warn(
            `Could not update sync settings on the server (HTTP ${e.status ?? "?"}).${activeHint} ` +
              `Continuing — ensure Local folder path and sync mode match the app if sync fails.`
          );
        } else {
          throw e;
        }
      }
      try {
        if (!isActive) {
          await api.toggleConnectorSync(cid, { fullSync });
          console.log("Sync enabled on the server; a full sync has been queued.");
        } else {
          await api.resyncConnectorRecords(cid, { fullSync });
          console.log("Full sync has been queued on the server.");
        }
      } catch (e) {
        if (e instanceof BackendClientError) {
          let hint = "";
          if (e.status === 401 || e.status === 403) {
            hint =
              " Your OAuth client may need CONNECTOR_SYNC and KB_WRITE (same as pipeshub run --with-backend).";
          }
          throw new Error(`${e.message}${hint}`);
        }
        throw e;
      }
      await applyFolderSyncCrawlingManagerSchedule(
        api,
        cid,
        strategy,
        intervalMinutes,
        etcdForSync
      );
    }

    if (!foreground) {
      const pid = spawnWatchWorker({
        connectorInstanceId: cid,
        syncRoot: rootPath,
        includeSubfolders,
        withBackend: false,
      });
      printWatcherStartedBackground(cid, pid);
      return;
    }
    await startFileWatcherAndWait({
      rootPath,
      cid,
      includeSubfolders,
      allowedExtensions: undefined,
      controlManager: manager,
      controlBackendBaseUrl: base,
    });
    return;
  }

  if (!manager || !api || !base) {
    throw new Error("Auth manager required for --with-backend");
  }

  const fullSync = true;

  const resolved = await resolveSyncStrategyForRun(etcdForSync);
  if (!resolved) {
    console.log("Cancelled.");
    return;
  }
  const { strategy, intervalMinutes } = resolved;

  const { ok } = await prompts({
    type: "confirm",
    name: "ok",
    message:
      `Sync and watch this folder?\n  ${rootPath}\n  Include subfolders: ${includeSubfolders ? "yes" : "no"}\n` +
      `  Server sync: ${strategy === "SCHEDULED" ? `scheduled every ${intervalMinutes} min` : "manual"}`,
    initial: true,
  });
  if (ok !== true) {
    console.log("Cancelled.");
    return;
  }

  const syncPayload = buildSyncPayloadForRun({
    rootPath,
    includeSubfolders,
    strategy,
    intervalMinutes: strategy === "SCHEDULED" ? intervalMinutes : undefined,
    etcd: etcdForSync,
  });

  try {
    await api.updateConnectorFiltersSync(cid, {
      sync: syncPayload,
    });
  } catch (e) {
    if (e instanceof BackendClientError) {
      const detail = `${e.message}`.toLowerCase();
      const activeHint =
        e.status === 400 && detail.includes("active")
          ? " Disable the connector in the app to change sync settings, then try again."
          : "";
      console.warn(
        `Could not update sync settings on the server (HTTP ${e.status ?? "?"}).${activeHint} ` +
          `Continuing with sync — ensure Local folder path and sync mode match the app if needed.`
      );
    } else {
      throw e;
    }
  }

  if (!isActive) {
    await api.toggleConnectorSync(cid, { fullSync });
    console.log("Sync enabled on the server; a full sync has been queued.");
  } else {
    await api.resyncConnectorRecords(cid, { fullSync });
    console.log("Full sync has been queued on the server.");
  }

  await applyFolderSyncCrawlingManagerSchedule(
    api,
    cid,
    strategy,
    intervalMinutes,
    etcdForSync
  );

  if (!foreground) {
    const pid = spawnWatchWorker({
      connectorInstanceId: cid,
      syncRoot: rootPath,
      includeSubfolders,
      withBackend: true,
      allowedExtensions,
    });
    printWatcherStartedBackground(cid, pid);
    return;
  }

  await startFileWatcherAndWait({
    rootPath,
    cid,
    includeSubfolders,
    allowedExtensions,
    manager,
    backendBaseUrl: base,
    controlManager: manager,
    controlBackendBaseUrl: base,
  });
}

async function startFileWatcherAndWait(opts: {
  rootPath: string;
  cid: string;
  includeSubfolders: boolean;
  allowedExtensions?: string[];
  /** If set, POST file-events to the backend; otherwise local watch only. */
  manager?: AuthManager;
  backendBaseUrl?: string;
  /** If set, keep a control socket registered for redirected Folder Sync resyncs. */
  controlManager?: AuthManager;
  controlBackendBaseUrl?: string;
  /** No [watcher] logs or footer (background worker). */
  quiet?: boolean;
  /** Remove ~/.config/pipeshub/watcher.<cid>.pid on exit (background worker). */
  removePidFileOnExit?: boolean;
}): Promise<void> {
  const { rootPath, cid, includeSubfolders, allowedExtensions, manager, backendBaseUrl } =
    opts;
  const controlManager = opts.controlManager;
  const controlBackendBaseUrl = opts.controlBackendBaseUrl;
  const quiet = opts.quiet === true;
  const removePid = opts.removePidFileOnExit === true;
  const watchOnly = !manager || !backendBaseUrl;
  const authDir = pipeshubConfigDir();

  const log = quiet
    ? () => {}
    : (msg: string) => {
        console.log(`[watcher] ${msg}`);
      };

  const watcher = new FileWatcher({
    syncRoot: rootPath,
    connectorInstanceId: cid,
    includeSubfolders,
    allowedExtensions,
    journalLocalOnly: watchOnly,
    dispatchFn: watchOnly
      ? async () => {}
      : async (events: FileEvent[], meta) => {
          try {
            const freshToken = await manager!.getValidAccessToken();
            const freshApi = new BackendClient(backendBaseUrl!, freshToken);
            await freshApi.notifyFileChanges(cid, events, meta.batchId);
          } catch (err) {
            if (!quiet) {
              console.error(
                `Failed to send file events: ${err instanceof Error ? err.message : String(err)}`
              );
            }
            throw err;
          }
        },
    log,
    authDir,
  });

  let controlApi: BackendClient | undefined;

  let stopping = false;
  const shutdown = async (signal: string) => {
    if (stopping) return;
    stopping = true;
    if (!quiet) console.log(`\n${signal} received. Stopping…`);
    controlApi?.disconnectControlSocket();
    await watcher.stop();
    if (removePid) {
      try {
        const pp = watcherPidPath(cid);
        if (fs.existsSync(pp)) fs.unlinkSync(pp);
      } catch {
        /* ignore */
      }
    }
    process.exit(0);
  };
  process.on("SIGINT", () => void shutdown("SIGINT"));
  process.on("SIGTERM", () => void shutdown("SIGTERM"));

  await watcher.start();

  if (controlManager && controlBackendBaseUrl) {
    try {
      controlApi = new BackendClient(
        controlBackendBaseUrl,
        await controlManager.getValidAccessToken()
      );
      await controlApi.onFolderSyncResync(async (request) =>
        replayPendingWatchBatches(
          authDir,
          cid,
          async (events, batchId) => {
            const freshToken = await controlManager.getValidAccessToken();
            const freshApi = new BackendClient(controlBackendBaseUrl, freshToken);
            await freshApi.notifyFileChanges(cid, events, batchId);
          },
          {
            // Explicit/full resync should treat the watcher journal as the source
            // of truth for connector-side changes, not just retry failed batches.
            includeSynced: request.fullSync === true,
          }
        )
      );
      await controlApi.registerFolderSyncWatcherControl(cid);
    } catch (error) {
      await watcher.stop();
      throw error;
    }
  }

  const status = watcher.getStatus();
  if (!quiet) {
    console.log(`\nWatching ${status.trackedFiles} file(s). Press Ctrl+C to stop.`);
    console.log(`State file:   ${path.join(os.homedir(), ".config", "pipeshub", `watcher_state.${cid}.json`)}`);
    console.log(`Change log:   ${watcherEventsJournalPath(path.join(os.homedir(), ".config", "pipeshub"), cid)}\n`);
  }

  await new Promise<void>(() => {});
}

export async function executeWatchWorker(
  cfg: WatchSpawnConfig,
  quiet: boolean
): Promise<void> {
  if (!cfg.connectorInstanceId?.trim() || !cfg.syncRoot?.trim()) {
    throw new Error("Invalid watch spawn config (connector id or sync root).");
  }
  const cid = cfg.connectorInstanceId.trim();
  const rootPath = path.resolve(cfg.syncRoot);

  if (cfg.withBackend) {
    const store = new CredentialStore();
    const manager = new AuthManager(store);
    if (!(await manager.isLoggedIn())) {
      throw new Error("Not logged in. Run: pipeshub login");
    }
    const { base } = await createBackendClient(manager);
    await startFileWatcherAndWait({
      rootPath,
      cid,
      includeSubfolders: cfg.includeSubfolders !== false,
      allowedExtensions: cfg.allowedExtensions,
      manager,
      backendBaseUrl: base,
      controlManager: manager,
      controlBackendBaseUrl: base,
      quiet,
      removePidFileOnExit: true,
    });
    return;
  }

  const store = new CredentialStore();
  const manager = new AuthManager(store);
  const loggedIn = await manager.isLoggedIn();
  const base = loggedIn ? (await createBackendClient(manager)).base : undefined;

  await startFileWatcherAndWait({
    rootPath,
    cid,
    includeSubfolders: cfg.includeSubfolders !== false,
    allowedExtensions: cfg.allowedExtensions,
    controlManager: loggedIn ? manager : undefined,
    controlBackendBaseUrl: base,
    quiet,
    removePidFileOnExit: true,
  });
}
