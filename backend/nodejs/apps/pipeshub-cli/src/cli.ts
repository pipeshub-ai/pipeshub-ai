#!/usr/bin/env node
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { Command } from "commander";
import dotenv from "dotenv";
import prompts from "prompts";
import { AuthManager } from "./auth_manager";
import {
  BackendClient,
  BackendClientError,
  FOLDER_SYNC_INCLUDE_SUBFOLDERS_KEY,
  FOLDER_SYNC_SYNC_ROOT_KEY,
  knowledgeBaseRecordDocumentKey,
} from "./backend_client";
import {
  loadDaemonConfig,
  saveDaemonConfig,
  type DaemonConfig,
} from "./daemon_config";
import {
  applySetupSyncPathAndFilters,
  emptyFolderSyncFilterCliState,
  readIncludeSubfoldersFromEtcd,
  readSyncSettingsFromEtcd,
  type FolderSyncFilterCliState,
} from "./folder_sync_filters";
import { CredentialStore } from "./credential_store";

function loadEnvFiles(): void {
  const candidates = [
    path.join(process.cwd(), ".env"),
    path.join(__dirname, "..", ".env"),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) {
      dotenv.config({ path: p });
      break;
    }
  }
}

loadEnvFiles();

async function backendBase(manager: AuthManager): Promise<string> {
  const stored = await manager.getStoredBaseUrl();
  if (stored) return stored.replace(/\/$/, "");
  return (process.env.PIPESHUB_BACKEND_URL || "http://localhost:3000").replace(
    /\/$/,
    ""
  );
}

function validateSyncRoot(raw: string): string {
  const resolved = path.resolve(raw.replace(/^~(?=$|[\\/])/, os.homedir()));
  if (!fs.existsSync(resolved)) {
    throw new Error(`Path does not exist: ${resolved}`);
  }
  if (!fs.statSync(resolved).isDirectory()) {
    throw new Error(`Not a directory: ${resolved}`);
  }
  try {
    const tmp = path.resolve(os.tmpdir());
    if (resolved === tmp || resolved.startsWith(tmp + path.sep)) {
      console.warn(
        "Warning: Sync root is under the system temp directory — not recommended."
      );
    }
  } catch {
    /* ignore */
  }
  return resolved;
}

/** Server sync_root for this connector, else local daemon.json if it matches the same id. */
async function suggestedSyncRootForSetup(
  api: BackendClient,
  connectorInstanceId: string
): Promise<string> {
  const cid = connectorInstanceId.trim();
  if (!cid) return "";
  try {
    const { etcd } = await api.getConnectorConfig(cid);
    const sync = readSyncSettingsFromEtcd(etcd);
    const raw = sync[FOLDER_SYNC_SYNC_ROOT_KEY];
    const s =
      raw !== undefined && String(raw).trim() ? String(raw).trim() : "";
    if (s) return s;
  } catch {
    /* fall through */
  }
  const d = loadDaemonConfig();
  if (d.connector_instance_id.trim() === cid && d.sync_root.trim()) {
    return d.sync_root.trim();
  }
  return "";
}

async function resolveConnectorInstanceId(
  client: BackendClient,
  opts: {
    connectorId: string | undefined;
    create: boolean;
    instanceName: string | undefined;
  }
): Promise<string> {
  if (opts.connectorId?.trim()) {
    return opts.connectorId.trim();
  }

  console.log("Checking for personal Folder Sync connectors…");

  let instances: Record<string, unknown>[];
  try {
    instances = await client.listFolderSyncInstances();
  } catch (e) {
    if (e instanceof BackendClientError) {
      let hint = "";
      if (e.status === 401 || e.status === 403) {
        hint =
          " Your OAuth client may need CONNECTOR_READ / CONNECTOR_WRITE scopes, " +
          "or create the connector in the app (Personal → Folder Sync).";
      } else if (e.status === 429) {
        hint = " Wait and run setup again.";
      }
      throw new Error(`${e.message}${hint}`);
    }
    throw e;
  }

  if (opts.create) {
    const name = (opts.instanceName || "My computer").trim() || "My computer";
    try {
      return await client.createFolderSyncInstance(name);
    } catch (e) {
      if (e instanceof BackendClientError) {
        throw new Error(e.message);
      }
      throw e;
    }
  }

  if (instances.length === 0) {
    console.log(
      "No personal Folder Sync connector found.\n" +
        "You can create one in the app: Personal → Connectors → Folder Sync, " +
        "or create via this CLI."
    );
    const { ok } = await prompts({
      type: "confirm",
      name: "ok",
      message: "Create a new Folder Sync connector now?",
      initial: true,
    });
    if (ok === true) {
      const { name } = await prompts({
        type: "text",
        name: "name",
        message: "Instance name",
        initial: "My computer",
      });
      const nm = String(name || "My computer").trim() || "My computer";
      try {
        return await client.createFolderSyncInstance(nm);
      } catch (e) {
        if (e instanceof BackendClientError) {
          throw new Error(e.message);
        }
        throw e;
      }
    }
    throw new Error(
      "Add a Folder Sync connector in the app, then run: pipeshub setup"
    );
  }

  if (instances.length === 1) {
    const inst = instances[0]!;
    const cid = String(inst._key || "");
    if (!cid) {
      throw new Error("Connector instance missing _key in API response");
    }
    const label = String(inst.name || cid);
    const { ok } = await prompts({
      type: "confirm",
      name: "ok",
      message: `Use connector ${label} (${cid})?`,
      initial: false,
    });
    if (ok === true) return cid;

    console.log("Creating a new Folder Sync connector instead.");
    const { name } = await prompts({
      type: "text",
      name: "name",
      message: "Instance name",
      initial: "My computer",
    });
    const nm = String(name || "My computer").trim() || "My computer";
    try {
      return await client.createFolderSyncInstance(nm);
    } catch (e) {
      if (e instanceof BackendClientError) {
        throw new Error(e.message);
      }
      throw e;
    }
  }

  console.log(
    "Personal Folder Sync connectors (pick one to link on this machine, or create new). " +
      "Rename, disable, or delete connectors in the web app."
  );
  instances.forEach((inst, i) => {
    const id = String(inst._key || "").trim();
    const label = String(inst.name || id).trim() || id;
    console.log(`  ${i + 1}. ${label}  (${id})`);
  });
  const createNewNum = instances.length + 1;
  console.log(`  ${createNewNum}. Create a new Folder Sync connector`);
  const { choice } = await prompts({
    type: "number",
    name: "choice",
    message: `Choose (1–${createNewNum})`,
    min: 1,
    max: createNewNum,
  });
  const n = Number(choice);
  if (!Number.isFinite(n) || n < 1 || n > createNewNum) {
    throw new Error("Invalid choice");
  }
  if (n === createNewNum) {
    console.log("Creating a new Folder Sync connector.");
    const { name } = await prompts({
      type: "text",
      name: "name",
      message: "Instance name",
      initial: "My computer",
    });
    const nm = String(name || "My computer").trim() || "My computer";
    try {
      return await client.createFolderSyncInstance(nm);
    } catch (e) {
      if (e instanceof BackendClientError) {
        throw new Error(e.message);
      }
      throw e;
    }
  }
  const picked = instances[n - 1]!;
  const cid = picked._key;
  if (!cid) throw new Error("Missing connector id");
  return String(cid);
}

async function setupAsync(
  manager: AuthManager,
  opts: {
    connectorId: string | undefined;
    syncRoot: string | undefined;
    create: boolean;
    instanceName: string | undefined;
  }
): Promise<void> {
  const token = await manager.getValidAccessToken();
  const base = await backendBase(manager);
  console.log(`API base: ${base}`);
  const api = new BackendClient(base, token);

  const cid = await resolveConnectorInstanceId(api, {
    connectorId: opts.connectorId,
    create: opts.create,
    instanceName: opts.instanceName,
  });

  let connectorActive = true;
  let connectorName = cid;
  try {
    const inst = await api.getConnectorInstance(cid);
    connectorName = inst.name?.trim() || connectorName;
  } catch {
    /* keep fallback */
  }
  try {
    const { isActive } = await api.getConnectorConfig(cid);
    connectorActive = Boolean(isActive);
  } catch {
    /* If config cannot be read, avoid pushing indexing/filter changes (same as active). */
    connectorActive = true;
  }

  if (connectorActive) {
    const { activeAction } = await prompts({
      type: "select",
      name: "activeAction",
      message: `Connector "${connectorName}" is ON. Choose action`,
      choices: [
        { title: "Keep ON and exit setup", value: "keep_on" },
        { title: "Disable connector and exit setup", value: "turn_off" },
        { title: "Rename connector", value: "rename" },
        { title: "Delete connector", value: "delete" },
      ],
      initial: 0,
    });

    if (activeAction === "keep_on") {
      console.log("Connector remains ON. No path changes attempted.");
      console.log("Setup complete. Queue a sync with: pipeshub run");
      return;
    }
    if (activeAction === "rename") {
      const { newName } = await prompts({
        type: "text",
        name: "newName",
        message: "New connector name",
        initial: connectorName,
      });
      const nm = String(newName ?? "").trim();
      if (!nm) throw new Error("Name required");
      await api.renameConnectorInstance(cid, nm);
      console.log(`Renamed to "${nm}".`);
      console.log("Setup complete.");
      return;
    }
    if (activeAction === "delete") {
      const { ok } = await prompts({
        type: "confirm",
        name: "ok",
        message: `Delete connector "${connectorName}"?`,
        initial: false,
      });
      if (ok === true) {
        await api.deleteConnectorInstance(cid);
        console.log("Connector deleted.");
      } else {
        console.log("Delete cancelled.");
      }
      console.log("Setup complete.");
      return;
    }
    if (activeAction === "turn_off") {
      try {
        await api.toggleConnectorSync(cid, { fullSync: false });
        const { isActive } = await api.getConnectorConfig(cid);
        connectorActive = Boolean(isActive);
        console.log(
          `Connector is now ${connectorActive ? "ENABLED" : "DISABLED"}.`
        );
        console.log("Setup complete.");
        return;
      } catch (e) {
        if (e instanceof BackendClientError) {
          console.log(`Could not disable connector (HTTP ${e.status ?? "?"}).`);
          console.log("Setup complete.");
          return;
        } else {
          throw e;
        }
      }
    }
  }

  let rootPath: string;
  if (opts.syncRoot) {
    rootPath = validateSyncRoot(opts.syncRoot);
  } else {
    const suggested = await suggestedSyncRootForSetup(api, cid);
    const { p } = await prompts({
      type: "text",
      name: "p",
      message: suggested
        ? "Absolute path to the folder to sync (Enter = keep current path)"
        : "Absolute path to the folder to sync",
      initial: suggested,
    });
    let raw = String(p ?? "").trim();
    if (!raw && suggested) raw = suggested;
    if (!raw) throw new Error("Path required");
    rootPath = validateSyncRoot(raw);
  }

  const cfg: DaemonConfig = {
    sync_root: rootPath,
    connector_instance_id: cid,
    include_subfolders: true,
  };
  saveDaemonConfig(cfg);

  let filterState: FolderSyncFilterCliState = {
    ...emptyFolderSyncFilterCliState,
  };

  if (!connectorActive) {
    const { manual } = await prompts({
      type: "confirm",
      name: "manual",
      message:
        "Enable manual indexing only (new files stay unindexed until you run indexing or sync actions)?",
      initial: false,
    });
    if (manual === true) {
      filterState = { ...filterState, manualIndexing: true };
    }
  } else {
    console.log("Connector is ON. Manual indexing settings are skipped while it is active.");
  }

  try {
    await applySetupSyncPathAndFilters(
      api,
      cid,
      rootPath,
      true,
      filterState
    );
  } catch (e) {
    if (e instanceof BackendClientError) {
      if (connectorActive) {
        console.log("Connector is ON. Path update is blocked while it is running.");
      } else {
        console.log(`Path update failed (HTTP ${e.status ?? "?"}).`);
      }
    } else if (
      e instanceof Error &&
      e.message.includes("Connector is active")
    ) {
      console.warn(
        `${e.message} Disable the connector in the app, then run setup again.`
      );
    } else {
      throw e;
    }
  }

  console.log("Setup complete. Queue a sync with: pipeshub run");
}

/**
 * List all personal Folder Sync connectors with registry + sync config details,
 * then prompt which one to use for indexing.
 */
async function pickFolderSyncConnectorForIndexing(
  api: BackendClient
): Promise<string> {
  let instances: Record<string, unknown>[];
  try {
    instances = await api.listFolderSyncInstances();
  } catch (e) {
    if (e instanceof BackendClientError) {
      throw new Error(
        `${e.message} (need CONNECTOR_READ to list connectors.)`
      );
    }
    throw e;
  }
  if (instances.length === 0) {
    throw new Error(
      "No personal Folder Sync connectors found. Add one in the app or run: pipeshub setup"
    );
  }

  type Row = {
    id: string;
    name: string;
    active: string;
    syncRoot: string;
    subfolders: string;
  };

  const rows: Row[] = await Promise.all(
    instances.map(async (inst) => {
      const id = String(inst._key || "").trim();
      const name = String(inst.name || id).trim() || id;
      let active = "unknown";
      let syncRoot = "—";
      let subfolders = "—";
      if (id) {
        try {
          const rec = await api.getConnectorInstanceRecord(id);
          active = Boolean(rec.isActive) ? "yes" : "no";
        } catch {
          /* keep unknown */
        }
        try {
          const { etcd } = await api.getConnectorConfig(id);
          const sync = readSyncSettingsFromEtcd(etcd);
          const rawRoot = sync[FOLDER_SYNC_SYNC_ROOT_KEY];
          syncRoot =
            rawRoot !== undefined && String(rawRoot).trim()
              ? String(rawRoot).trim()
              : "(not set)";
          const sub = readIncludeSubfoldersFromEtcd(etcd);
          subfolders =
            sub === undefined ? "(default)" : sub ? "yes" : "no";
        } catch {
          syncRoot = "(config unavailable)";
        }
      }
      return {
        id,
        name,
        active,
        syncRoot,
        subfolders,
      };
    })
  );

  console.log(
    "\nPersonal Folder Sync connectors (pick one for this command; manage instances in the web app):\n"
  );
  rows.forEach((r, i) => {
    console.log(`${i + 1}. ${r.name}`);
    console.log(`   Id:            ${r.id}`);
    console.log(`   Active:        ${r.active}`);
    console.log(`   Sync root:     ${r.syncRoot}`);
    console.log(`   Subfolders:    ${r.subfolders}`);
    console.log("");
  });

  const { choice } = await prompts({
    type: "number",
    name: "choice",
    message: `Which connector (1–${rows.length})?`,
    min: 1,
    max: rows.length,
  });
  const n = Number(choice);
  if (!Number.isFinite(n) || n < 1 || n > rows.length) {
    throw new Error("Invalid choice");
  }
  return rows[n - 1]!.id;
}

function kbCommandErrorHint(e: BackendClientError): string {
  if (e.status === 401 || e.status === 403) {
    return (
      " Your OAuth client may need KB_READ (list/stats) and KB_WRITE (reindex / queue-manual) scopes."
    );
  }
  return "";
}

/** One-line summary: total records + non-zero statuses only (default `pipeshub indexing`). */
function printKnowledgeBaseIndexingSummaryCompact(
  stats: Record<string, unknown>
): void {
  const st = stats["stats"] as Record<string, unknown> | undefined;
  const idx = st?.["indexingStatus"] as Record<string, number> | undefined;
  const totalRaw = st?.["total"];
  const total = typeof totalRaw === "number" ? totalRaw : undefined;
  const head =
    typeof total === "number"
      ? `${total} record(s) on this connector`
      : "Records on this connector (total unknown)";
  console.log(head);
  if (!idx || typeof idx !== "object") {
    console.log("  (no status breakdown)");
    return;
  }
  const nonzero = Object.keys(idx)
    .sort()
    .filter((k) => {
      const v = idx[k];
      return typeof v === "number" && v > 0;
    })
    .map((k) => `${k}: ${idx[k]}`);
  if (nonzero.length === 0) {
    console.log("  Status: all zero");
    return;
  }
  console.log(`  ${nonzero.join(" · ")}`);
}

/** Connector label plus compact KB stats (default indexing flows). */
async function printConnectorIndexingSummary(
  api: BackendClient,
  cid: string
): Promise<void> {
  const [registry, stats] = await Promise.all([
    api.getConnectorInstanceRecord(cid),
    api.getConnectorKnowledgeStats(cid),
  ]);
  const label =
    String(registry.name ?? "Folder Sync").trim() || "Folder Sync";
  console.log(label);
  console.log("");
  printKnowledgeBaseIndexingSummaryCompact(stats);
  console.log("");
}

type IndexingPickRow = {
  _key: string;
  recordName: string;
  indexingStatus?: string;
};

type IndexingPickerListMode = "pending" | "all";

/** Prefer AUTO_INDEX_OFF backlog; if empty, list all files for this instance (scoped in backend_client for Folder Sync + Neo4j). */
async function loadRecordsForIndexingPicker(
  api: BackendClient,
  cid: string
): Promise<{
  rows: IndexingPickRow[];
  mode: IndexingPickerListMode;
  pagination: { totalCount: number; totalPages: number };
}> {
  let raw = await api.listKnowledgeBaseRecordsForConnectorInstance(cid, {
    indexingStatus: ["AUTO_INDEX_OFF"],
    maxRecords: 200,
  });
  let mode: IndexingPickerListMode = "pending";
  if (raw.length === 0) {
    raw = await api.listKnowledgeBaseRecordsForConnectorInstance(cid, {
      maxRecords: 200,
    });
    mode = "all";
  }
  const rows: IndexingPickRow[] = raw.map((r) => ({
    _key: knowledgeBaseRecordDocumentKey(r),
    recordName: String(r.recordName ?? "(unnamed)"),
    indexingStatus: String(r.indexingStatus ?? ""),
  }));
  return {
    rows,
    mode,
    pagination: { totalCount: rows.length, totalPages: 1 },
  };
}

/** Interactive: numbered file names, then index by number, name fragment, or `all` (pending only). */
async function runIndexingPickPrompt(
  api: BackendClient,
  cid: string,
  heading: string
): Promise<void> {
  const { rows, mode, pagination } = await loadRecordsForIndexingPicker(
    api,
    cid
  );
  console.log(heading);
  if (rows.length === 0) {
    console.log(
      "  (no files in the knowledge base for this connector yet — run pipeshub run first)"
    );
    return;
  }
  if (mode === "all") {
    console.log(
      "  (no AUTO_INDEX_OFF backlog — listing all files; pick one to reindex)"
    );
  }
  rows.forEach((r, i) => {
    const st =
      mode === "all" && r.indexingStatus?.trim()
        ? ` (${r.indexingStatus})`
        : "";
    console.log(`  ${i + 1}. ${r.recordName}${st}`);
  });
  if (pagination.totalCount > rows.length) {
    console.log(
      `  … ${pagination.totalCount - rows.length} more not shown (first ${rows.length} only)`
    );
  }
  const promptMsg =
    mode === "pending"
      ? 'Which one? (number, part of the name, "all" = index every pending file, Enter = skip)'
      : "Which to reindex? (number or part of the name, Enter = skip)";
  const { choice } = await prompts({
    type: "text",
    name: "choice",
    message: promptMsg,
  });
  const raw = String(choice ?? "").trim();
  if (!raw) {
    console.log("Skipped.");
    return;
  }
  if (raw.toLowerCase() === "all") {
    if (mode === "all") {
      throw new Error(
        'There is no "all" when nothing was pending — pick a number or name.'
      );
    }
    const ids = rows.map((r) => r._key).filter(Boolean);
    await api.queueKnowledgeBaseReindexForRecordIds(ids);
    console.log(
      `Queued indexing for ${ids.length} pending file(s) (per-record API).`
    );
    return;
  }
  const numOnly = /^(\d+)$/.exec(raw.trim());
  if (numOnly) {
    const n = parseInt(numOnly[1]!, 10);
    if (n >= 1 && n <= rows.length) {
      const pick = rows[n - 1]!;
      await api.reindexKnowledgeBaseRecord(pick._key);
      console.log(`Queued indexing: ${pick.recordName}`);
      return;
    }
  }
  const frag = raw.toLowerCase();
  const matches = rows.filter((r) => r.recordName.toLowerCase().includes(frag));
  if (matches.length === 1) {
    await api.reindexKnowledgeBaseRecord(matches[0]!._key);
    console.log(`Queued indexing: ${matches[0]!.recordName}`);
    return;
  }
  if (matches.length === 0) {
    throw new Error("No file name matches — use a number from the list or a clearer name fragment.");
  }
  throw new Error(
    `Multiple matches (${matches.length}): ${matches.map((m) => m.recordName).join(", ")} — use a number or a longer name.`
  );
}

async function runSyncAsync(
  manager: AuthManager,
  opts: { rootOverride?: string }
): Promise<void> {
  const token = await manager.getValidAccessToken();
  const base = await backendBase(manager);
  const api = new BackendClient(base, token);
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
  const fullSync = true;

  let includeSubfolders = dc.include_subfolders ?? true;
  let isActive = false;
  try {
    const cfg = await api.getConnectorConfig(cid);
    isActive = cfg.isActive;
    const fromEtcd = readIncludeSubfoldersFromEtcd(cfg.etcd);
    if (fromEtcd !== undefined) {
      includeSubfolders = fromEtcd;
    }
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
    message: `Queue sync for this folder?\n  ${rootPath}\n  Include subfolders: ${includeSubfolders ? "yes" : "no"}`,
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
    console.log(
      fullSync
        ? "Sync enabled on the server; a full sync has been queued."
        : "Sync enabled on the server; a sync has been queued."
    );
    return;
  }

  await api.resyncConnectorRecords(cid, { fullSync });
  console.log(
    fullSync
      ? "Full sync has been queued on the server."
      : "Resync has been queued on the server."
  );
}

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
        create: false,
        instanceName: undefined,
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
    const store = new CredentialStore();
    const manager = new AuthManager(store);
    if (!(await manager.isLoggedIn())) {
      console.error("Not logged in. Run: pipeshub login");
      process.exit(1);
    }
    try {
      const token = await manager.getValidAccessToken();
      const base = await backendBase(manager);
      const api = new BackendClient(base, token);
      const cid = await pickFolderSyncConnectorForIndexing(api);
      await printConnectorIndexingSummary(api, cid);
      try {
        await runIndexingPickPrompt(api, cid, "Files waiting to index:");
        process.exit(0);
      } catch (pickErr) {
        console.error(String(pickErr));
        process.exit(1);
      }
    } catch (e) {
      if (e instanceof BackendClientError) {
        console.error(`${e.message}${kbCommandErrorHint(e)}`);
        process.exit(1);
      }
      console.error(String(e));
      process.exit(1);
    }
  });

indexingCmd
  .command("list")
  .description(
    "Choose a Folder Sync connector, then list KB records (first page, up to 50 rows)."
  )
  .action(async () => {
    const store = new CredentialStore();
    const manager = new AuthManager(store);
    if (!(await manager.isLoggedIn())) {
      console.error("Not logged in. Run: pipeshub login");
      process.exit(1);
    }
    try {
      const token = await manager.getValidAccessToken();
      const base = await backendBase(manager);
      const api = new BackendClient(base, token);
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
    } catch (e) {
      if (e instanceof BackendClientError) {
        console.error(`${e.message}${kbCommandErrorHint(e)}`);
        process.exit(1);
      }
      console.error(String(e));
      process.exit(1);
    }
  });

indexingCmd
  .command("reindex")
  .description(
    "With record id: queue that record. Without id: pick a connector, then summary and interactive pick."
  )
  .argument("[recordId]", "Optional record id")
  .action(async (recordId: string | undefined) => {
    const store = new CredentialStore();
    const manager = new AuthManager(store);
    if (!(await manager.isLoggedIn())) {
      console.error("Not logged in. Run: pipeshub login");
      process.exit(1);
    }
    try {
      const token = await manager.getValidAccessToken();
      const base = await backendBase(manager);
      const api = new BackendClient(base, token);
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
    } catch (e) {
      if (e instanceof BackendClientError) {
        console.error(`${e.message}${kbCommandErrorHint(e)}`);
        process.exit(1);
      }
      console.error(String(e));
      process.exit(1);
    }
  });

indexingCmd
  .command("queue-manual")
  .description(
    "Choose a connector, then queue indexing for all AUTO_INDEX_OFF records on it."
  )
  .action(async () => {
    const store = new CredentialStore();
    const manager = new AuthManager(store);
    if (!(await manager.isLoggedIn())) {
      console.error("Not logged in. Run: pipeshub login");
      process.exit(1);
    }
    try {
      const token = await manager.getValidAccessToken();
      const base = await backendBase(manager);
      const api = new BackendClient(base, token);
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
    } catch (e) {
      if (e instanceof BackendClientError) {
        console.error(`${e.message}${kbCommandErrorHint(e)}`);
        process.exit(1);
      }
      console.error(String(e));
      process.exit(1);
    }
  });

indexingCmd.action(async () => {
  const store = new CredentialStore();
  const manager = new AuthManager(store);
  if (!(await manager.isLoggedIn())) {
    console.error("Not logged in. Run: pipeshub login");
    process.exit(1);
  }
  try {
    const token = await manager.getValidAccessToken();
    const base = await backendBase(manager);
    const api = new BackendClient(base, token);
    const cid = await pickFolderSyncConnectorForIndexing(api);
    await printConnectorIndexingSummary(api, cid);
    try {
      await runIndexingPickPrompt(api, cid, "Files waiting to index:");
      process.exit(0);
    } catch (pickErr) {
      console.error(String(pickErr));
      process.exit(1);
    }
  } catch (e) {
    if (e instanceof BackendClientError) {
      console.error(`${e.message}${kbCommandErrorHint(e)}`);
      process.exit(1);
    }
    console.error(String(e));
    process.exit(1);
  }
});

program
  .command("run")
  .description(
    "Push sync path and include_subfolders (from server or daemon.json) to the backend, then queue a full sync."
  )
  .alias("sync")
  .argument("[root]", "Optional folder path for this run (otherwise uses setup path).")
  .action(async (rootArg: string | undefined) => {
    const store = new CredentialStore();
    const manager = new AuthManager(store);
    if (!(await manager.isLoggedIn())) {
      console.error("Not logged in. Run: pipeshub login");
      process.exit(1);
    }
    try {
      await runSyncAsync(manager, {
        rootOverride: rootArg?.trim() || undefined,
      });
      process.exit(0);
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

program.parseAsync(process.argv).catch((e) => {
  console.error(e);
  process.exit(1);
});
