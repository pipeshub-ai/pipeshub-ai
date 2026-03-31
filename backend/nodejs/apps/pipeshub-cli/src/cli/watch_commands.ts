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
  FOLDER_SYNC_INCLUDE_SUBFOLDERS_KEY,
  FOLDER_SYNC_SYNC_ROOT_KEY,
} from "../api/backend_client";
import { loadDaemonConfig } from "../config/daemon_config";
import {
  readAllowedFileExtensionsFromEtcd,
  readIncludeSubfoldersFromEtcd,
} from "../sync/folder_sync_filters";
import { FileWatcher } from "../sync/file_watcher";
import type { FileEvent } from "../sync/watcher_state";
import { watcherEventsJournalPath } from "../sync/watcher_sync_journal";
import { createBackendClient } from "./context";
import { validateSyncRoot } from "./setup_commands";

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

function throwIfWatcherAlreadyRunning(cid: string): void {
  clearStaleWatcherPidIfDead(cid);
  if (!fs.existsSync(watcherPidPath(cid))) return;
  const pid = Number(fs.readFileSync(watcherPidPath(cid), "utf8").trim());
  throw new Error(
    `Background watcher already running (PID ${pid}). Stop with: pipeshub watch-stop`
  );
}

function pathToCliScript(): string {
  return path.join(__dirname, "..", "cli.js");
}

function spawnWatchWorker(config: WatchSpawnConfig): number {
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

  let rootPath: string;
  if (opts.rootOverride) {
    rootPath = validateSyncRoot(opts.rootOverride);
  } else if (dc.sync_root) {
    rootPath = validateSyncRoot(dc.sync_root);
  } else {
    throw new Error(
      "No sync root. Run: pipeshub setup  (or pass a path argument)."
    );
  }
  if (!dc.connector_instance_id?.trim()) {
    throw new Error("No connector linked. Run: pipeshub setup");
  }
  const cid = dc.connector_instance_id.trim();

  if (!withBackend) {
    const includeSubfolders = dc.include_subfolders ?? true;
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
    if (!foreground) {
      throwIfWatcherAlreadyRunning(cid);
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
    });
    return;
  }

  if (!manager) {
    throw new Error("Auth manager required");
  }

  const { api, base } = await createBackendClient(manager);
  const fullSync = true;

  let includeSubfolders = dc.include_subfolders ?? true;
  let allowedExtensions: string[] | undefined;
  let isActive = false;
  try {
    const cfg = await api.getConnectorConfig(cid);
    isActive = cfg.isActive;
    const fromEtcd = readIncludeSubfoldersFromEtcd(cfg.etcd);
    if (fromEtcd !== undefined) {
      includeSubfolders = fromEtcd;
    }
    allowedExtensions = readAllowedFileExtensionsFromEtcd(cfg.etcd);
  } catch (e) {
    if (e instanceof BackendClientError) {
      console.warn(
        `Could not read connector config (HTTP ${e.status ?? "?"}). ` +
          `Using include_subfolders from daemon.json (if set) or default true.`
      );
      try {
        const inst = await api.getConnectorInstance(cid);
        isActive = inst.isActive;
      } catch (e2) {
        throw e2;
      }
    } else {
      throw e;
    }
  }

  const { ok } = await prompts({
    type: "confirm",
    name: "ok",
    message: `Sync and watch this folder?\n  ${rootPath}\n  Include subfolders: ${includeSubfolders ? "yes" : "no"}`,
    initial: true,
  });
  if (ok !== true) {
    console.log("Cancelled.");
    return;
  }

  try {
    await api.updateConnectorFiltersSync(cid, {
      sync: {
        [FOLDER_SYNC_SYNC_ROOT_KEY]: rootPath,
        [FOLDER_SYNC_INCLUDE_SUBFOLDERS_KEY]: includeSubfolders,
      },
    });
  } catch (e) {
    if (e instanceof BackendClientError) {
      console.warn(
        `Could not update folder path on the server (HTTP ${e.status ?? "?"}). ` +
          `Continuing with sync — ensure Local folder path matches in the app if sync fails.`
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

  if (!foreground) {
    throwIfWatcherAlreadyRunning(cid);
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
  /** No [watcher] logs or footer (background worker). */
  quiet?: boolean;
  /** Remove ~/.config/pipeshub/watcher.<cid>.pid on exit (background worker). */
  removePidFileOnExit?: boolean;
}): Promise<void> {
  const { rootPath, cid, includeSubfolders, allowedExtensions, manager, backendBaseUrl } =
    opts;
  const quiet = opts.quiet === true;
  const removePid = opts.removePidFileOnExit === true;
  const watchOnly = !manager || !backendBaseUrl;

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
  });

  let stopping = false;
  const shutdown = async (signal: string) => {
    if (stopping) return;
    stopping = true;
    if (!quiet) console.log(`\n${signal} received. Stopping…`);
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
      quiet,
      removePidFileOnExit: true,
    });
    return;
  }

  await startFileWatcherAndWait({
    rootPath,
    cid,
    includeSubfolders: cfg.includeSubfolders !== false,
    allowedExtensions: cfg.allowedExtensions,
    quiet,
    removePidFileOnExit: true,
  });
}
