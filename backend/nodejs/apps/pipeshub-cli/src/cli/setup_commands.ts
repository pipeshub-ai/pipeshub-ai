import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import prompts from "prompts";
import { AuthManager } from "../auth/auth_manager";
import {
  BackendClient,
  BackendClientError,
  LOCAL_FS_SYNC_ROOT_KEY,
} from "../api/backend_client";
import {
  loadDaemonConfig,
  saveDaemonConfig,
  type DaemonConfig,
} from "../config/daemon_config";
import {
  applySetupSyncPathAndFilters,
  emptyLocalFsFilterCliState,
  readSyncSettingsFromEtcd,
  type LocalFsFilterCliState,
} from "../sync/local_fs_filters";
import { createBackendClient } from "./context";

export function validateSyncRoot(raw: string): string {
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
export async function suggestedSyncRootForSetup(
  api: BackendClient,
  connectorInstanceId: string
): Promise<string> {
  const cid = connectorInstanceId.trim();
  if (!cid) return "";
  try {
    const { etcd } = await api.getConnectorConfig(cid);
    const sync = readSyncSettingsFromEtcd(etcd);
    const raw = sync[LOCAL_FS_SYNC_ROOT_KEY];
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

async function createLocalFsInstanceOrThrow(
  client: BackendClient,
  name: string
): Promise<string> {
  try {
    return await client.createLocalFsInstance(name);
  } catch (e) {
    if (e instanceof BackendClientError) {
      throw new Error(e.message);
    }
    throw e;
  }
}

export async function resolveConnectorInstanceId(
  client: BackendClient,
  opts: { connectorId: string | undefined }
): Promise<string> {
  if (opts.connectorId?.trim()) {
    return opts.connectorId.trim();
  }

  console.log("Checking for personal Local FS connectors…");

  let instances: Record<string, unknown>[];
  try {
    instances = await client.listLocalFsInstances();
  } catch (e) {
    if (e instanceof BackendClientError) {
      let hint = "";
      if (e.status === 401 || e.status === 403) {
        hint =
          " Your OAuth client may need CONNECTOR_READ / CONNECTOR_WRITE scopes, " +
          "or create the connector in the app (Personal → Local FS).";
      } else if (e.status === 429) {
        hint = " Wait and run setup again.";
      }
      throw new Error(`${e.message}${hint}`);
    }
    throw e;
  }

  if (instances.length === 0) {
    console.log(
      "No personal Local FS connector found.\n" +
        "You can create one in the app: Personal → Connectors → Local FS, " +
        "or create via this CLI."
    );
    const { ok } = await prompts({
      type: "confirm",
      name: "ok",
      message: "Create a new Local FS connector now?",
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
      return createLocalFsInstanceOrThrow(client, nm);
    }
    throw new Error(
      "Add a Local FS connector in the app, then run: pipeshub setup"
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

    console.log("Creating a new Local FS connector instead.");
    const { name } = await prompts({
      type: "text",
      name: "name",
      message: "Instance name",
      initial: "My computer",
    });
    const nm = String(name || "My computer").trim() || "My computer";
    return createLocalFsInstanceOrThrow(client, nm);
  }

  console.log(
    "Personal Local FS connectors (pick one to link on this machine, or create new). " +
      "Rename, disable, or delete connectors in the web app."
  );
  instances.forEach((inst, i) => {
    const id = String(inst._key || "").trim();
    const label = String(inst.name || id).trim() || id;
    console.log(`  ${i + 1}. ${label}  (${id})`);
  });
  const createNewNum = instances.length + 1;
  console.log(`  ${createNewNum}. Create a new Local FS connector`);
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
    console.log("Creating a new Local FS connector.");
    const { name } = await prompts({
      type: "text",
      name: "name",
      message: "Instance name",
      initial: "My computer",
    });
    const nm = String(name || "My computer").trim() || "My computer";
    return createLocalFsInstanceOrThrow(client, nm);
  }
  const picked = instances[n - 1]!;
  const cid = picked._key;
  if (!cid) throw new Error("Missing connector id");
  return String(cid);
}

export async function setupAsync(
  manager: AuthManager,
  opts: { connectorId: string | undefined; syncRoot: string | undefined }
): Promise<void> {
  const { api, base } = await createBackendClient(manager);
  console.log(`API base: ${base}`);

  const cid = await resolveConnectorInstanceId(api, {
    connectorId: opts.connectorId,
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
      console.log(
        "Setup complete. Start watching: pipeshub run  (use --with-backend to queue sync and push file events to the API)"
      );
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

  let filterState: LocalFsFilterCliState = {
    ...emptyLocalFsFilterCliState,
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

  console.log(
    "Setup complete. Start watching: pipeshub run  (use --with-backend to queue sync and push file events to the API)"
  );
}
