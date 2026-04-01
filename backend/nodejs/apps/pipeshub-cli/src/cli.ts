#!/usr/bin/env node
import * as fs from "fs";
import * as path from "path";
import { Command } from "commander";
import prompts from "prompts";
import { AuthManager } from "./auth/auth_manager";
import {
  BackendClientError,
  knowledgeBaseRecordDocumentKey,
} from "./api/backend_client";
import {
  daemonConfigComplete,
  loadDaemonConfig,
} from "./config/daemon_config";
import { CredentialStore } from "./auth/credential_store";
import { loadEnvFiles } from "./cli/env";
import {
  pickFolderSyncConnectorForIndexing,
  printConnectorIndexingSummary,
  runIndexingAuthenticated,
  runIndexingPickPrompt,
  runIndexingStatusFlow,
} from "./cli/indexing_commands";
import { setupAsync } from "./cli/setup_commands";
import {
  executeWatchWorker,
  isProcessRunning,
  pipeshubConfigDir,
  runSyncAsync,
  watcherPidPath,
  type WatchSpawnConfig,
} from "./cli/watch_commands";

loadEnvFiles();

const program = new Command();

program
  .name("pipeshub")
  .description(
    "Pipeshub CLI — authenticate, link Folder Sync, run sync, and manage indexing."
  )
  .version("0.1.0");

program
  .command("login")
  .description(
    "Log in with your Pipeshub client ID and secret. Tokens are stored in the OS keychain when available, else in an encrypted local file. API base URL: set PIPESHUB_BACKEND_URL or you will be prompted."
  )
  .action(async () => {
    let resolvedBase = (process.env.PIPESHUB_BACKEND_URL || "")
      .trim()
      .replace(/\/$/, "");
    if (!resolvedBase) {
      const { v: baseInput } = await prompts({
        type: "text",
        name: "v",
        message: "Backend base URL",
        initial: "http://localhost:3000",
      });
      resolvedBase = String(baseInput || "")
        .trim()
        .replace(/\/$/, "");
    }
    if (!resolvedBase) {
      console.error("Backend URL is required (set PIPESHUB_BACKEND_URL or enter when prompted).");
      process.exit(1);
    }

    const { v: cid } = await prompts({
      type: "text",
      name: "v",
      message: "Client ID",
    });
    const clientId = String(cid || "").trim();
    if (!clientId) {
      console.error("Client ID is required.");
      process.exit(1);
    }

    const { v: csec } = await prompts({
      type: "password",
      name: "v",
      message: "Client secret",
    });
    const clientSecret = String(csec || "").trim();
    if (!clientSecret) {
      console.error("Client secret is required.");
      process.exit(1);
    }

    const store = new CredentialStore();
    const manager = new AuthManager(store, resolvedBase);
    try {
      await manager.login(clientId, clientSecret, resolvedBase);
    } catch (e) {
      console.error(`Login failed: ${e}`);
      process.exit(1);
    }
    console.log("Login successful.");
  });

program
  .command("setup")
  .description(
    "Interactively link a personal Folder Sync connector and save the folder path. Configure filters in the web app."
  )
  .argument("[root]", "Optional absolute path to the folder (otherwise you are prompted).")
  .action(async (rootArg: string | undefined) => {
    const store = new CredentialStore();
    const manager = new AuthManager(store);
    if (!(await manager.isLoggedIn())) {
      console.error("Not logged in. Run: pipeshub login");
      process.exit(1);
    }
    try {
      await setupAsync(manager, {
        connectorId: undefined,
        syncRoot: rootArg?.trim() || undefined,
      });
      process.exit(0);
    } catch (e) {
      console.error(String(e));
      process.exit(1);
    }
  });

program
  .command("verify")
  .description("Check with the backend that your access token is active (authorized).")
  .action(async () => {
    const store = new CredentialStore();
    const manager = new AuthManager(store);
    if (!(await manager.isLoggedIn())) {
      console.error("Not logged in. Run: pipeshub login");
      process.exit(1);
    }
    let result: Record<string, unknown>;
    try {
      result = await manager.verifyTokenWithBackend();
    } catch (e) {
      console.error(`Verification failed: ${e}`);
      process.exit(1);
    }
    if (result.active) {
      console.log("OK.");
    } else {
      console.error("Token inactive or invalid.");
      process.exit(1);
    }
  });

program
  .command("logout")
  .description("Remove stored credentials (tokens in keychain or auth.enc).")
  .action(async () => {
    const store = new CredentialStore();
    const manager = new AuthManager(store);
    if (await manager.logout()) {
      console.log("Logged out.");
    } else {
      console.log("No credentials.");
    }
  });

const indexingCmd = program
  .command("indexing")
  .description(
    "Knowledge-base indexing: choose a Folder Sync connector, then list / reindex / queue-manual (subcommands)."
  );

indexingCmd
  .command("status")
  .description(
    "Pick a Folder Sync connector (see details), then KB summary and file pick."
  )
  .action(async () => {
    await runIndexingAuthenticated(runIndexingStatusFlow);
  });

indexingCmd
  .command("list")
  .description(
    "Choose a Folder Sync connector, then list KB records (first page, up to 50 rows)."
  )
  .action(async () => {
    await runIndexingAuthenticated(async (api) => {
      const cid = await pickFolderSyncConnectorForIndexing(api);
      const page = 1;
      const limit = 50;
      const allForConnector = await api.listKnowledgeBaseRecordsForConnectorInstance(
        cid,
        { maxRecords: limit }
      );
      const records = allForConnector.slice(0, limit);
      console.log(`Page ${page} · showing ${records.length} record(s)`);
      if (records.length === 0) {
        console.log("(no rows — run pipeshub run first)");
        process.exit(0);
      }
      const wId = 38;
      const wName = 42;
      const wSt = 18;
      console.log(
        `${"_key".padEnd(wId)} ${"recordName".padEnd(wName)} ${"indexingStatus".padEnd(wSt)}`
      );
      console.log("-".repeat(wId + wName + wSt + 2));
      for (const r of records) {
        const key = knowledgeBaseRecordDocumentKey(r).slice(0, wId);
        const name = String(r.recordName ?? "").slice(0, wName);
        const st = String(r.indexingStatus ?? "").slice(0, wSt);
        console.log(`${key.padEnd(wId)} ${name.padEnd(wName)} ${st.padEnd(wSt)}`);
      }
      process.exit(0);
    });
  });

indexingCmd
  .command("reindex")
  .description(
    "With record id: queue that record. Without id: pick a connector, then summary and interactive pick."
  )
  .argument("[recordId]", "Optional record id")
  .action(async (recordId: string | undefined) => {
    await runIndexingAuthenticated(async (api) => {
      const rid = recordId?.trim();
      if (rid) {
        await api.reindexKnowledgeBaseRecord(rid);
        console.log(`Queued indexing for record ${rid}.`);
        process.exit(0);
      }
      const cid = await pickFolderSyncConnectorForIndexing(api);
      try {
        await printConnectorIndexingSummary(api, cid);
        await runIndexingPickPrompt(api, cid, "Pick a file to index:");
        process.exit(0);
      } catch (pickErr) {
        console.error(String(pickErr));
        process.exit(1);
      }
    });
  });

indexingCmd
  .command("queue-manual")
  .description(
    "Choose a connector, then queue indexing for all AUTO_INDEX_OFF records on it."
  )
  .action(async () => {
    await runIndexingAuthenticated(async (api) => {
      const cid = await pickFolderSyncConnectorForIndexing(api);
      const { ok } = await prompts({
        type: "confirm",
        name: "ok",
        message: `Queue indexing for all AUTO_INDEX_OFF records on connector ${cid}?`,
        initial: true,
      });
      if (ok !== true) {
        console.log("Cancelled.");
        process.exit(0);
      }
      const pending = await api.listKnowledgeBaseRecordsForConnectorInstance(cid, {
        indexingStatus: ["AUTO_INDEX_OFF"],
        maxRecords: 2000,
      });
      const ids = pending
        .map((r) => knowledgeBaseRecordDocumentKey(r))
        .filter(Boolean);
      if (ids.length === 0) {
        console.log("No AUTO_INDEX_OFF records to queue.");
        process.exit(0);
      }
      await api.queueKnowledgeBaseReindexForRecordIds(ids);
      console.log(`Queued indexing for ${ids.length} pending file(s).`);
      process.exit(0);
    });
  });

indexingCmd.action(async () => {
  await runIndexingAuthenticated(runIndexingStatusFlow);
});

program
  .command("run")
  .description(
    "Start file watching (default: background process, CLI exits). Inspect watcher_state / watcher_events JSON files for changes. Use --foreground to block in this terminal."
  )
  .alias("sync")
  .argument("[root]", "Optional folder path for this run (otherwise uses setup path).")
  .option(
    "-f, --foreground",
    "Keep this process in the foreground until Ctrl+C (instead of a background watcher)."
  )
  .option(
    "--with-backend",
    "Also queue a full sync on the server and POST file change events to the backend (requires login)."
  )
  .action(async (rootArg: string | undefined, opts: { withBackend?: boolean; foreground?: boolean }) => {
    const withBackend = Boolean(opts.withBackend);
    const foreground = Boolean(opts.foreground);
    const store = new CredentialStore();
    const manager = new AuthManager(store);
    const loggedIn = await manager.isLoggedIn();
    if (withBackend && !loggedIn) {
      console.error("Not logged in. Run: pipeshub login");
      process.exit(1);
    }
    if (!withBackend && !loggedIn) {
      if (!daemonConfigComplete(loadDaemonConfig())) {
        console.error(
          "No sync root or connector in config. Run: pipeshub login (to pick a connector) or pipeshub setup."
        );
        process.exit(1);
      }
    }
    try {
      await runSyncAsync(loggedIn ? manager : undefined, {
        rootOverride: rootArg?.trim() || undefined,
        withBackend,
        foreground,
      });
      // Logged-in flows keep a Socket.IO RPC connection open; exit explicitly so the CLI returns to the shell.
      if (!foreground) {
        process.exit(0);
      }
    } catch (e) {
      if (e instanceof BackendClientError) {
        let hint = "";
        if (e.status === 401 || e.status === 403) {
          hint =
            " Your OAuth client may need CONNECTOR_READ, CONNECTOR_WRITE, CONNECTOR_SYNC, and KB_WRITE scopes.";
        }
        console.error(`${e.message}${hint}`);
        process.exit(1);
      }
      console.error(String(e));
      process.exit(1);
    }
  });

program
  .command("watch-worker", { hidden: true })
  .description("Internal: run watcher from a spawn config file.")
  .requiredOption("--config <path>", "Path to JSON watch spawn config")
  .option("--quiet", "Suppress watcher console output", false)
  .action(async (opts: { config: string; quiet?: boolean }) => {
    let raw: string;
    try {
      raw = fs.readFileSync(opts.config, "utf8");
    } catch {
      console.error("Could not read watch spawn config.");
      process.exit(1);
    }
    let cfg: WatchSpawnConfig;
    try {
      cfg = JSON.parse(raw) as WatchSpawnConfig;
    } catch {
      console.error("Invalid watch spawn config JSON.");
      process.exit(1);
    }
    try {
      fs.unlinkSync(opts.config);
    } catch {
      /* ignore */
    }
    const cid = cfg.connectorInstanceId?.trim() || "";
    const quiet = opts.quiet === true;
    try {
      await executeWatchWorker(cfg, quiet);
    } catch (e) {
      if (cid) {
        try {
          fs.unlinkSync(watcherPidPath(cid));
        } catch {
          /* ignore */
        }
        try {
          const errPath = path.join(pipeshubConfigDir(), `watcher_error.${cid}.log`);
          fs.appendFileSync(
            errPath,
            `${new Date().toISOString()} ${e instanceof Error ? e.stack || e.message : String(e)}\n`,
            "utf8"
          );
        } catch {
          /* ignore */
        }
      }
      process.exit(1);
    }
  });

program
  .command("watch-stop")
  .description("Stop the background file watcher started by pipeshub run (default mode).")
  .action(() => {
    const dc = loadDaemonConfig();
    const cid = dc.connector_instance_id?.trim();
    if (!cid) {
      console.error("No connector in daemon.json. Run: pipeshub setup");
      process.exit(1);
    }
    const pp = watcherPidPath(cid);
    if (!fs.existsSync(pp)) {
      console.error("No background watcher PID file. Nothing to stop (or already stopped).");
      process.exit(1);
    }
    const pid = Number(fs.readFileSync(pp, "utf8").trim());
    if (!Number.isFinite(pid) || pid <= 0) {
      try {
        fs.unlinkSync(pp);
      } catch {
        /* ignore */
      }
      console.error("Invalid PID file; removed.");
      process.exit(1);
    }
    if (!isProcessRunning(pid)) {
      try {
        fs.unlinkSync(pp);
      } catch {
        /* ignore */
      }
      console.log("Watcher was not running (stale PID). Cleaned up.");
      process.exit(0);
    }
    try {
      process.kill(pid, "SIGTERM");
    } catch (e) {
      console.error(`Failed to signal PID ${pid}: ${e instanceof Error ? e.message : String(e)}`);
      process.exit(1);
    }
    try {
      fs.unlinkSync(pp);
    } catch {
      /* ignore */
    }
    console.log(`Sent stop signal to watcher (PID ${pid}).`);
  });

program.parseAsync(process.argv).catch((e) => {
  console.error(e);
  process.exit(1);
});
