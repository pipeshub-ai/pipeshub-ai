import {
  BackendClient,
  LOCAL_FS_INCLUDE_SUBFOLDERS_KEY,
  LOCAL_FS_SYNC_ROOT_KEY,
} from "../api/backend_client";

/** Top-level and `sync.values` folder paths / options (etcd). */
export function readSyncSettingsFromEtcd(
  etcd: Record<string, unknown>
): Record<string, unknown> {
  const sync = etcd["sync"] as Record<string, unknown> | undefined;
  if (!sync || typeof sync !== "object") {
    return {};
  }
  const out: Record<string, unknown> = {};
  const pick = (src: Record<string, unknown>, k: string) => {
    if (src[k] !== undefined) {
      out[k] = src[k];
    }
  };
  pick(sync, LOCAL_FS_SYNC_ROOT_KEY);
  pick(sync, LOCAL_FS_INCLUDE_SUBFOLDERS_KEY);
  pick(sync, "selectedStrategy");
  pick(sync, "batchSize");
  pick(sync, "batch_size");
  const values = sync["values"] as Record<string, unknown> | undefined;
  if (values && typeof values === "object") {
    pick(values, LOCAL_FS_SYNC_ROOT_KEY);
    pick(values, LOCAL_FS_INCLUDE_SUBFOLDERS_KEY);
  }
  return out;
}

function parseIncludeSubfoldersRaw(v: unknown): boolean | undefined {
  if (v === undefined || v === null) {
    return undefined;
  }
  if (typeof v === "boolean") {
    return v;
  }
  if (typeof v === "string") {
    const s = v.trim().toLowerCase();
    if (s === "true" || s === "1" || s === "yes" || s === "on") {
      return true;
    }
    if (s === "false" || s === "0" || s === "no" || s === "off") {
      return false;
    }
  }
  if (typeof v === "object" && v !== null && "value" in v) {
    return parseIncludeSubfoldersRaw((v as { value: unknown }).value);
  }
  return undefined;
}

/** Resolved `include_subfolders` from etcd `sync` / `sync.values` (same keys as web). */
export function readIncludeSubfoldersFromEtcd(
  etcd: Record<string, unknown>
): boolean | undefined {
  const sync = readSyncSettingsFromEtcd(etcd);
  return parseIncludeSubfoldersRaw(sync[LOCAL_FS_INCLUDE_SUBFOLDERS_KEY]);
}

export function syncFilterValuesFromEtcd(
  etcd: Record<string, unknown>
): Record<string, unknown> | undefined {
  const filters = etcd["filters"] as Record<string, unknown> | undefined;
  const sync = filters?.["sync"] as Record<string, unknown> | undefined;
  const values = sync?.["values"];
  if (values && typeof values === "object" && values !== null) {
    return { ...(values as Record<string, unknown>) };
  }
  return undefined;
}

export function indexingFilterValuesFromEtcd(
  etcd: Record<string, unknown>
): Record<string, unknown> | undefined {
  const filters = etcd["filters"] as Record<string, unknown> | undefined;
  const indexing = filters?.["indexing"] as Record<string, unknown> | undefined;
  const values = indexing?.["values"];
  if (values && typeof values === "object" && values !== null) {
    return { ...(values as Record<string, unknown>) };
  }
  return undefined;
}

/** Stored filter keys for Local FS indexing (matches Python IndexingFilterKey + enable_manual_sync). */
export const LOCAL_FS_INDEXING_KEYS = [
  "enable_manual_sync",
  "files",
  "documents",
  "images",
  "videos",
  "attachments",
] as const;

export type LocalFsIndexingKey = (typeof LOCAL_FS_INDEXING_KEYS)[number];

export function boolFilterEntry(value: boolean): {
  operator: "is";
  value: boolean;
  type: "boolean";
} {
  return { operator: "is", value, type: "boolean" };
}

/** Multiselect entry for `file_extensions` (sync filter). */
export function fileExtensionsFilterEntry(extensions: string[]): {
  operator: "in";
  value: string[];
  type: "multiselect";
} {
  const normalized = extensions
    .map((e) => e.trim().replace(/^\./, "").toLowerCase())
    .filter(Boolean);
  return { operator: "in", value: normalized, type: "multiselect" };
}

export type TriBool = true | false | undefined;

export function triFromMutEx(yes: boolean, no: boolean, label: string): TriBool {
  if (yes && no) {
    throw new Error(`Conflicting flags for ${label}`);
  }
  if (yes) return true;
  if (no) return false;
  return undefined;
}

export type LocalFsFilterCliState = {
  manualIndexing: TriBool;
  indexFiles: TriBool;
  indexDocuments: TriBool;
  indexImages: TriBool;
  indexVideos: TriBool;
  indexAttachments: TriBool;
  /** When set, updates `file_extensions` sync filter. Use `clearExtensions` to clear. */
  extensions: string[] | undefined;
  /** When true, write empty `file_extensions` multiselect to clear any restriction. */
  clearExtensions?: boolean;
};

/** No filter mutations (sync path only). */
export const emptyLocalFsFilterCliState: LocalFsFilterCliState = {
  manualIndexing: undefined,
  indexFiles: undefined,
  indexDocuments: undefined,
  indexImages: undefined,
  indexVideos: undefined,
  indexAttachments: undefined,
  extensions: undefined,
};

export function indexingEntriesFromCliState(
  s: LocalFsFilterCliState
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (s.manualIndexing !== undefined) {
    out.enable_manual_sync = boolFilterEntry(s.manualIndexing);
  }
  if (s.indexFiles !== undefined) {
    out.files = boolFilterEntry(s.indexFiles);
  }
  if (s.indexDocuments !== undefined) {
    out.documents = boolFilterEntry(s.indexDocuments);
  }
  if (s.indexImages !== undefined) {
    out.images = boolFilterEntry(s.indexImages);
  }
  if (s.indexVideos !== undefined) {
    out.videos = boolFilterEntry(s.indexVideos);
  }
  if (s.indexAttachments !== undefined) {
    out.attachments = boolFilterEntry(s.indexAttachments);
  }
  return out;
}

export function syncFilterEntriesFromCliState(
  s: LocalFsFilterCliState
): Record<string, unknown> {
  if (s.clearExtensions) {
    return { file_extensions: fileExtensionsFilterEntry([]) };
  }
  if (s.extensions === undefined) {
    return {};
  }
  return { file_extensions: fileExtensionsFilterEntry(s.extensions) };
}

export function hasAnyFilterChange(s: LocalFsFilterCliState): boolean {
  return (
    s.manualIndexing !== undefined ||
    s.indexFiles !== undefined ||
    s.indexDocuments !== undefined ||
    s.indexImages !== undefined ||
    s.indexVideos !== undefined ||
    s.indexAttachments !== undefined ||
    s.extensions !== undefined ||
    s.clearExtensions === true
  );
}

/**
 * Merge top-level sync keys + optional filter sections, preserving existing `filters.*.schema` in etcd.
 * Throws if connector is active and filter sections would be updated.
 */
export async function applyLocalFsFiltersSync(
  api: BackendClient,
  connectorInstanceId: string,
  args: {
    syncTopLevel?: Record<string, unknown>;
    indexingEntries?: Record<string, unknown>;
    syncFilterEntries?: Record<string, unknown>;
  }
): Promise<void> {
  const hasIndexing =
    args.indexingEntries && Object.keys(args.indexingEntries).length > 0;
  const hasSyncFilters =
    args.syncFilterEntries && Object.keys(args.syncFilterEntries).length > 0;
  const touchesFilters = Boolean(hasIndexing || hasSyncFilters);

  if (!args.syncTopLevel && !touchesFilters) {
    return;
  }

  if (!touchesFilters) {
    if (args.syncTopLevel) {
      await api.updateConnectorFiltersSync(connectorInstanceId, {
        sync: args.syncTopLevel,
      });
    }
    return;
  }

  const { isActive, etcd } = await api.getConnectorConfig(connectorInstanceId);
  if (isActive) {
    throw new Error(
      "Connector is active. Disable this Local FS connector in the app before changing indexing or sync filters, then run again."
    );
  }

  const etcdFilters = etcd["filters"] as
    | {
        sync?: Record<string, unknown>;
        indexing?: Record<string, unknown>;
      }
    | undefined;

  const body: {
    sync?: Record<string, unknown>;
    filters?: Record<string, unknown>;
  } = {};

  if (args.syncTopLevel) {
    body.sync = args.syncTopLevel;
  }

  const filters: Record<string, unknown> = {};
  if (hasIndexing && args.indexingEntries) {
    const cur = etcdFilters?.indexing;
    const curValues = (cur?.values as Record<string, unknown> | undefined) ?? {};
    filters.indexing = {
      ...(cur ?? {}),
      values: { ...curValues, ...args.indexingEntries },
    };
  }
  if (hasSyncFilters && args.syncFilterEntries) {
    const cur = etcdFilters?.sync;
    const curValues = (cur?.values as Record<string, unknown> | undefined) ?? {};
    filters.sync = {
      ...(cur ?? {}),
      values: { ...curValues, ...args.syncFilterEntries },
    };
  }
  body.filters = filters;

  await api.updateConnectorFiltersSync(connectorInstanceId, body);
}

/** Push folder path (and optional filters). Path update uses the same API as the web “Local folder” sync settings. */
export async function applySetupSyncPathAndFilters(
  api: BackendClient,
  cid: string,
  rootPath: string,
  includeSubfolders: boolean,
  filterState: LocalFsFilterCliState
): Promise<void> {
  const syncTopLevel: Record<string, unknown> = {
    [LOCAL_FS_SYNC_ROOT_KEY]: rootPath,
    [LOCAL_FS_INCLUDE_SUBFOLDERS_KEY]: includeSubfolders,
  };
  const indexingEntries = indexingEntriesFromCliState(filterState);
  const syncFilterEntries = syncFilterEntriesFromCliState(filterState);
  await applyLocalFsFiltersSync(api, cid, {
    syncTopLevel,
    indexingEntries,
    syncFilterEntries,
  });
}

function parseFilterBooleanField(entry: unknown): boolean | undefined {
  if (entry === undefined || entry === null) {
    return undefined;
  }
  if (typeof entry === "boolean") {
    return entry;
  }
  if (typeof entry === "object" && entry !== null && "value" in entry) {
    const v = (entry as { value?: unknown }).value;
    if (typeof v === "boolean") {
      return v;
    }
    if (v === "true" || v === "false") {
      return v === "true";
    }
  }
  return undefined;
}

/** Read allowed file extensions from sync filter `file_extensions` in etcd (same shape as web app). */
export function readAllowedFileExtensionsFromEtcd(
  etcd: Record<string, unknown>
): string[] | undefined {
  const values = syncFilterValuesFromEtcd(etcd);
  if (!values) return undefined;
  const entry = values["file_extensions"] as
    | { value?: unknown }
    | undefined;
  if (!entry) return undefined;
  const raw = entry.value;
  if (!Array.isArray(raw)) return undefined;
  const exts = (raw as unknown[])
    .map((v) => String(v ?? "").trim().replace(/^\./, "").toLowerCase())
    .filter(Boolean);
  return exts.length > 0 ? exts : undefined;
}

/** Read indexing filter `enable_manual_sync` from connector etcd `config` blob (same shape as web app). */
export function readEnableManualSyncFromEtcd(
  etcd: Record<string, unknown>
): boolean | undefined {
  const filters = etcd["filters"] as Record<string, unknown> | undefined;
  const indexing = filters?.["indexing"] as Record<string, unknown> | undefined;
  if (!indexing) {
    return undefined;
  }
  const values = indexing["values"] as Record<string, unknown> | undefined;
  const fromValues = parseFilterBooleanField(values?.["enable_manual_sync"]);
  if (fromValues !== undefined) {
    return fromValues;
  }
  return parseFilterBooleanField(indexing["enable_manual_sync"]);
}
