import prompts from "prompts";
import {
  BackendClient,
  BackendClientError,
  FOLDER_SYNC_SYNC_ROOT_KEY,
} from "../api/backend_client";
import {
  readIncludeSubfoldersFromEtcd,
  readSyncSettingsFromEtcd,
} from "../sync/folder_sync_filters";

export type FolderSyncConnectorRow = {
  id: string;
  name: string;
  active: string;
  syncRoot: string;
  subfolders: string;
};

export function rowHasSyncRootConfigured(row: FolderSyncConnectorRow): boolean {
  const s = row.syncRoot.trim();
  if (!s || s === "—") return false;
  if (s === "(not set)" || s === "(config unavailable)") return false;
  return true;
}

export async function fetchFolderSyncConnectorRows(
  api: BackendClient
): Promise<FolderSyncConnectorRow[]> {
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

  return Promise.all(
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
}

export async function promptPickFolderSyncConnector(
  rows: FolderSyncConnectorRow[],
  intro: string
): Promise<string> {
  if (rows.length === 0) {
    throw new Error("No connectors to pick.");
  }
  console.log(intro);
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

export async function pickFolderSyncConnectorForIndexing(
  api: BackendClient
): Promise<string> {
  const rows = await fetchFolderSyncConnectorRows(api);
  if (rows.length === 0) {
    throw new Error(
      "No personal Folder Sync connectors found. Add one in the app or run: pipeshub setup"
    );
  }
  return promptPickFolderSyncConnector(
    rows,
    "\nPersonal Folder Sync connectors (pick one for this command; manage instances in the web app):\n"
  );
}

export async function pickFolderSyncConnectorForRun(
  api: BackendClient
): Promise<FolderSyncConnectorRow> {
  const allRows = await fetchFolderSyncConnectorRows(api);
  const rows = allRows.filter(rowHasSyncRootConfigured);
  if (rows.length === 0) {
    throw new Error(
      "No Folder Sync connectors with a sync folder path set. Run: pipeshub setup or set Local folder path in the app."
    );
  }
  const cid = await promptPickFolderSyncConnector(
    rows,
    "\nFolder Sync connectors with a path configured (pick one to run):\n"
  );
  const picked = rows.find((r) => r.id === cid);
  if (!picked) {
    throw new Error("Invalid selection");
  }
  return picked;
}
