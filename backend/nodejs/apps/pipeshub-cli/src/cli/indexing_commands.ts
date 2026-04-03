import prompts from "prompts";
import { AuthManager } from "../auth/auth_manager";
import { CredentialStore } from "../auth/credential_store";
import {
  BackendClient,
  BackendClientError,
  knowledgeBaseRecordDocumentKey,
} from "../api/backend_client";
import { createBackendClient } from "./context";
import { pickLocalFsConnectorForIndexing } from "./local_fs_connector_picker";

export { pickLocalFsConnectorForIndexing };

export function kbCommandErrorHint(e: BackendClientError): string {
  if (e.status === 401 || e.status === 403) {
    return (
      " Your OAuth client may need KB_READ (list/stats) and KB_WRITE (reindex / queue-manual) scopes."
    );
  }
  return "";
}

export async function runIndexingAuthenticated(
  fn: (api: BackendClient) => Promise<void>
): Promise<void> {
  const store = new CredentialStore();
  const manager = new AuthManager(store);
  if (!(await manager.isLoggedIn())) {
    console.error("Not logged in. Run: pipeshub login");
    process.exit(1);
  }
  try {
    const { api } = await createBackendClient(manager);
    await fn(api);
  } catch (e) {
    if (e instanceof BackendClientError) {
      console.error(`${e.message}${kbCommandErrorHint(e)}`);
      process.exit(1);
    }
    console.error(String(e));
    process.exit(1);
  }
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
export async function printConnectorIndexingSummary(
  api: BackendClient,
  cid: string
): Promise<void> {
  const [registry, stats] = await Promise.all([
    api.getConnectorInstanceRecord(cid),
    api.getConnectorKnowledgeStats(cid),
  ]);
  const label =
    String(registry.name ?? "Local FS").trim() || "Local FS";
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

/** Prefer AUTO_INDEX_OFF backlog; if empty, list all files for this instance (scoped in backend_client for Local FS + Neo4j). */
async function loadRecordsForIndexingPicker(
  api: BackendClient,
  cid: string
): Promise<{
  rows: IndexingPickRow[];
  mode: IndexingPickerListMode;
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
  return { rows, mode };
}

/** Interactive: numbered file names, then index by number, name fragment, or `all` (pending only). */
export async function runIndexingPickPrompt(
  api: BackendClient,
  cid: string,
  heading: string
): Promise<void> {
  const { rows, mode } = await loadRecordsForIndexingPicker(api, cid);
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

/** Same flow as `pipeshub indexing` default and `pipeshub indexing status`. */
export async function runIndexingStatusFlow(api: BackendClient): Promise<void> {
  const cid = await pickLocalFsConnectorForIndexing(api);
  await printConnectorIndexingSummary(api, cid);
  await runIndexingPickPrompt(api, cid, "Files waiting to index:");
  process.exit(0);
}
