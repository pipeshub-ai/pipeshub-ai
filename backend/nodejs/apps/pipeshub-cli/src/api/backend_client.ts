import { SocketIoRpcClient } from "../transport/socketio_rpc_client";

/** Web/registry name — Python `FOLDER_SYNC_CONNECTOR_NAME` in folder_sync/connector.py */
const FOLDER_SYNC_CONNECTOR_TYPE = "Folder Sync";
/** Stored on graph records (`connectorName`) — Python `Connectors.FOLDER_SYNC` value */
const FOLDER_SYNC_RECORD_CONNECTOR_NAME = "FOLDER_SYNC";

const FETCH_TIMEOUT_MS = 90_000;
const REINDEX_CONCURRENCY = 8;

/** How many times to retry after HTTP 429 (waits `retryAfter` seconds each time). */
const MAX_429_RETRIES = 8;

/** Sync config keys — must match Python `SYNC_ROOT_PATH_KEY` / `INCLUDE_SUBFOLDERS_KEY` */
export const FOLDER_SYNC_SYNC_ROOT_KEY = "sync_root_path";
export const FOLDER_SYNC_INCLUDE_SUBFOLDERS_KEY = "include_subfolders";

function errnoFromUnknown(e: unknown): string | null {
  if (e instanceof Error && "cause" in e && e.cause) {
    const c = e.cause as NodeJS.ErrnoException;
    if (c && typeof c.code === "string" && c.code) {
      return c.code;
    }
  }
  const msg = e instanceof Error ? e.message : String(e);
  const m = /\b(ECONNREFUSED|ENOTFOUND)\b/i.exec(msg);
  return m ? m[1]!.toUpperCase() : null;
}

function unreachableMessage(base: string, code: string): string {
  return (
    `Cannot reach Pipeshub API at ${base} (${code}). ` +
    `Start the Node gateway (e.g. http://localhost:3000) or set PIPESHUB_BACKEND_URL.`
  );
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function secondsUntil429Retry(resp: Response, jsonBody: unknown): number {
  const h = resp.headers.get("retry-after");
  if (h) {
    const n = parseInt(h, 10);
    if (Number.isFinite(n) && n >= 0) {
      return Math.min(Math.max(n, 1), 120);
    }
  }
  if (jsonBody && typeof jsonBody === "object" && jsonBody !== null) {
    const r = (jsonBody as { error?: { retryAfter?: number } }).error?.retryAfter;
    if (typeof r === "number" && Number.isFinite(r)) {
      return Math.min(Math.max(r, 1), 120);
    }
  }
  return 5;
}

function isFolderSyncInstanceType(instType: string): boolean {
  // DB/registry uses `FOLDER_SYNC`; UI may use "Folder Sync". Strip spaces and underscores.
  const n = instType
    .trim()
    .replace(/_/g, "")
    .replace(/\s+/g, "")
    .toLowerCase();
  return n === "foldersync" || n === "localfilesystem";
}

export class BackendClientError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = "BackendClientError";
    this.status = status;
  }
}

/**
 * Python connector service returns `{ success, data: { orgId, connectorId, stats, byRecordType } }`.
 * Normalize to the inner `data` object for callers.
 */
/** Primary document id for KB APIs (`_key` in Arango, `id` in Neo4j list projections). */
export function knowledgeBaseRecordDocumentKey(
  r: Record<string, unknown>
): string {
  return String(r._key ?? r.id ?? "").trim();
}

export function unwrapConnectorKnowledgeStats(
  raw: Record<string, unknown>
): Record<string, unknown> {
  if (
    raw["success"] === true &&
    raw["data"] !== undefined &&
    raw["data"] !== null &&
    typeof raw["data"] === "object"
  ) {
    return raw["data"] as Record<string, unknown>;
  }
  return raw;
}

/** Map Knowledge Hub `NodeItem` to a row shape compatible with `knowledgeBaseRecordDocumentKey` / CLI list. */
function knowledgeHubNodeToKbRecordRow(
  node: Record<string, unknown>,
  connectorInstanceId: string
): Record<string, unknown> {
  const id = String(node.id ?? "").trim();
  return {
    id,
    _key: id,
    recordName: node.name,
    indexingStatus: node.indexingStatus,
    connectorId: connectorInstanceId,
  };
}

type KnowledgeHubNodesPagination = {
  page: number;
  limit: number;
  totalItems: number;
  totalPages: number;
  hasNext: boolean;
  hasPrev: boolean;
};

/** Node gateway may return the hub payload at top level or under `data`. */
function knowledgeHubResponsePayload(data: unknown): Record<string, unknown> {
  const root =
    typeof data === "object" && data !== null && !Array.isArray(data)
      ? (data as Record<string, unknown>)
      : {};
  const inner = root["data"];
  if (
    inner &&
    typeof inner === "object" &&
    !Array.isArray(inner) &&
    ("items" in inner ||
      "success" in inner ||
      "pagination" in inner ||
      "error" in inner)
  ) {
    return inner as Record<string, unknown>;
  }
  return root;
}

export class BackendClient {
  private readonly base: string;
  private readonly token: string;
  private readonly rpc: SocketIoRpcClient;

  constructor(baseUrl: string, accessToken: string) {
    this.base = baseUrl.replace(/\/$/, "");
    this.token = accessToken;
    this.rpc = new SocketIoRpcClient(this.base, this.token);
  }

  get apiBase(): string {
    return this.base;
  }

  private headers(): Record<string, string> {
    return {
      Authorization: `Bearer ${this.token}`,
      "Content-Type": "application/json",
      Accept: "application/json",
    };
  }

  /** Low-level fetch: headers, timeout, clear error if the gateway is down. */
  private async fetchOnce(url: string, init: RequestInit = {}): Promise<Response> {
    try {
      const parsed = new URL(url);
      const query: Record<string, string> = {};
      parsed.searchParams.forEach((value, key) => {
        query[key] = value;
      });
      let body: unknown = undefined;
      if (typeof init.body === "string" && init.body.trim()) {
        body = JSON.parse(init.body);
      } else if (init.body && typeof init.body !== "string") {
        body = init.body;
      }
      const rpcResp = await this.rpc.request(
        {
          method: String(init.method || "GET").toUpperCase(),
          path: parsed.pathname,
          query,
          body,
        },
        FETCH_TIMEOUT_MS
      );
      return new Response(JSON.stringify(rpcResp.body), {
        status: rpcResp.status,
        headers: { "content-type": "application/json" },
      });
    } catch (e) {
      const code = errnoFromUnknown(e);
      if (code === "ECONNREFUSED" || code === "ENOTFOUND") {
        throw new BackendClientError(unreachableMessage(this.base, code), null);
      }
      const msg = e instanceof Error ? e.message : String(e);
      throw new BackendClientError(msg, null);
    }
  }

  /**
   * `fetchOnce` plus automatic wait/retry on HTTP 429 (uses `Retry-After` or JSON `error.retryAfter`).
   */
  private async request(url: string, init: RequestInit = {}): Promise<Response> {
    for (let attempt = 0; ; attempt++) {
      const resp = await this.fetchOnce(url, init);
      if (resp.status !== 429) {
        return resp;
      }
      if (attempt >= MAX_429_RETRIES - 1) {
        return resp;
      }
      let jsonBody: unknown = null;
      try {
        jsonBody = await resp.clone().json();
      } catch {
        /* ignore */
      }
      const waitSec = secondsUntil429Retry(resp, jsonBody);
      console.log(
        `Rate limited (429). Waiting ${waitSec}s before retry (${attempt + 1}/${MAX_429_RETRIES})…`
      );
      await sleep(waitSec * 1000);
    }
  }

  async listPersonalConnectorInstances(
    pageSize = 200
  ): Promise<Record<string, unknown>[]> {
    const all: Record<string, unknown>[] = [];
    let page = 1;

    for (;;) {
      const url = new URL(`${this.base}/api/v1/connectors/`);
      url.searchParams.set("scope", "personal");
      url.searchParams.set("limit", String(pageSize));
      url.searchParams.set("page", String(page));

      const resp = await this.request(url.toString(), { method: "GET" });
      let data: unknown;
      try {
        data = await resp.json();
      } catch (e) {
        throw new BackendClientError(
          `Invalid JSON from list connectors: ${e}`,
          resp.status
        );
      }
      if (resp.status >= 400) {
        throw new BackendClientError(
          `List connectors failed (${resp.status}): ${JSON.stringify(data)}`,
          resp.status
        );
      }

      const body =
        typeof data === "object" && data !== null
          ? (data as {
              connectors?: unknown;
              pagination?: {
                hasNext?: boolean;
                nextPage?: number | null;
              };
            })
          : {};

      const raw = body.connectors;
      if (Array.isArray(raw)) {
        all.push(...(raw as Record<string, unknown>[]));
      }

      const pag = body.pagination;
      // If the API sets hasNext=false, stop even when nextPage is stale (e.g. nextPage=2 with hasNext=false).
      const hasNext =
        pag?.hasNext === false
          ? false
          : pag?.hasNext === true ||
            (pag?.nextPage != null &&
              typeof pag.nextPage === "number" &&
              pag.nextPage > page);

      if (!hasNext) {
        break;
      }
      page =
        typeof pag?.nextPage === "number" && pag.nextPage > page
          ? pag.nextPage
          : page + 1;
      await sleep(200);
    }

    return all;
  }

  async listFolderSyncInstances(): Promise<Record<string, unknown>[]> {
    const out: Record<string, unknown>[] = [];
    for (const inst of await this.listPersonalConnectorInstances()) {
      if (isFolderSyncInstanceType(String(inst.type || ""))) {
        out.push(inst);
      }
    }
    return out;
  }

  async createFolderSyncInstance(instanceName: string): Promise<string> {
    const url = `${this.base}/api/v1/connectors/`;
    const body = {
      connectorType: FOLDER_SYNC_CONNECTOR_TYPE,
      instanceName,
      scope: "personal",
      authType: "NONE",
      config: {},
    };
    const resp = await this.request(url, {
      method: "POST",
      body: JSON.stringify(body),
    });
    let data: unknown;
    try {
      data = await resp.json();
    } catch (e) {
      throw new BackendClientError(
        `Invalid JSON from create connector: ${e}`,
        resp.status
      );
    }
    if (resp.status >= 400) {
      const detail = typeof data === "object" ? data : String(data);
      throw new BackendClientError(
        `Create connector failed (${resp.status}): ${JSON.stringify(detail)}`,
        resp.status
      );
    }
    const conn =
      typeof data === "object" && data !== null
        ? (data as { connector?: { connectorId?: string } }).connector
        : undefined;
    const cid = conn?.connectorId;
    if (!cid) {
      throw new BackendClientError(
        "Create connector response missing connectorId"
      );
    }
    return String(cid);
  }

  async updateConnectorFiltersSync(
    connectorInstanceId: string,
    body: { sync?: Record<string, unknown>; filters?: Record<string, unknown> }
  ): Promise<void> {
    const url = `${this.base}/api/v1/connectors/${encodeURIComponent(
      connectorInstanceId
    )}/config/filters-sync`;
    const resp = await this.request(url, {
      method: "PUT",
      body: JSON.stringify(body),
    });
    let data: unknown;
    try {
      data = await resp.json();
    } catch {
      data = null;
    }
    if (resp.status >= 400) {
      throw new BackendClientError(
        `Update connector sync failed (${resp.status}): ${JSON.stringify(data)}`,
        resp.status
      );
    }
  }

  /**
   * Full connector instance config from etcd (GET /config). Used to merge filter `values`
   * without dropping `schema`, and to read `isActive`.
   */
  async getConnectorConfig(connectorInstanceId: string): Promise<{
    isActive: boolean;
    etcd: Record<string, unknown>;
    /** GET /config envelope minus nested `config` (etcd) — instance metadata from CM. */
    instanceEnvelope: Record<string, unknown>;
  }> {
    const url = `${this.base}/api/v1/connectors/${encodeURIComponent(
      connectorInstanceId
    )}/config`;
    const resp = await this.request(url, { method: "GET" });
    let data: unknown;
    try {
      data = await resp.json();
    } catch (e) {
      throw new BackendClientError(
        `Invalid JSON from get connector config: ${e}`,
        resp.status
      );
    }
    if (resp.status >= 400) {
      throw new BackendClientError(
        `Get connector config failed (${resp.status}): ${JSON.stringify(data)}`,
        resp.status
      );
    }
    const root =
      typeof data === "object" && data !== null
        ? (data as { config?: Record<string, unknown> })
        : {};
    const wrap = root.config ?? {};
    const isActive = Boolean(wrap["isActive"]);
    const etcdRaw = wrap["config"];
    const etcd =
      etcdRaw && typeof etcdRaw === "object" && etcdRaw !== null
        ? (etcdRaw as Record<string, unknown>)
        : {};
    const instanceEnvelope: Record<string, unknown> = { ...wrap };
    delete instanceEnvelope["config"];
    return { isActive, etcd, instanceEnvelope };
  }

  /** Full connector registry document (GET /connectors/:id). */
  async getConnectorInstanceRecord(
    connectorInstanceId: string
  ): Promise<Record<string, unknown>> {
    const url = `${this.base}/api/v1/connectors/${encodeURIComponent(
      connectorInstanceId
    )}`;
    const resp = await this.request(url, { method: "GET" });
    let data: unknown;
    try {
      data = await resp.json();
    } catch (e) {
      throw new BackendClientError(
        `Invalid JSON from get connector: ${e}`,
        resp.status
      );
    }
    if (resp.status >= 400) {
      throw new BackendClientError(
        `Get connector failed (${resp.status}): ${JSON.stringify(data)}`,
        resp.status
      );
    }
    const body =
      typeof data === "object" && data !== null
        ? (data as { connector?: Record<string, unknown> })
        : {};
    const c = body.connector;
    return c && typeof c === "object" ? { ...c } : {};
  }

  async getConnectorInstance(
    connectorInstanceId: string
  ): Promise<{ isActive: boolean; type?: string; name?: string }> {
    const c = await this.getConnectorInstanceRecord(connectorInstanceId);
    return {
      isActive: Boolean(c.isActive),
      type: c.type != null ? String(c.type) : undefined,
      name: c.name != null ? String(c.name) : undefined,
    };
  }

  async toggleConnectorSync(
    connectorInstanceId: string,
    opts: { fullSync?: boolean }
  ): Promise<void> {
    const url = `${this.base}/api/v1/connectors/${encodeURIComponent(
      connectorInstanceId
    )}/toggle`;
    const resp = await this.request(url, {
      method: "POST",
      body: JSON.stringify({
        type: "sync",
        fullSync: opts.fullSync ?? true,
      }),
    });
    let data: unknown;
    try {
      data = await resp.json();
    } catch {
      data = null;
    }
    if (resp.status >= 400) {
      throw new BackendClientError(
        `Toggle connector sync failed (${resp.status}): ${JSON.stringify(data)}`,
        resp.status
      );
    }
  }

  async renameConnectorInstance(
    connectorInstanceId: string,
    newName: string
  ): Promise<void> {
    const url = `${this.base}/api/v1/connectors/${encodeURIComponent(
      connectorInstanceId
    )}/name`;
    const resp = await this.request(url, {
      method: "PATCH",
      body: JSON.stringify({ name: newName }),
    });
    let data: unknown;
    try {
      data = await resp.json();
    } catch {
      data = null;
    }
    if (resp.status >= 400) {
      throw new BackendClientError(
        `Rename connector failed (${resp.status}): ${JSON.stringify(data)}`,
        resp.status
      );
    }
  }

  async deleteConnectorInstance(connectorInstanceId: string): Promise<void> {
    const url = `${this.base}/api/v1/connectors/${encodeURIComponent(
      connectorInstanceId
    )}`;
    const resp = await this.request(url, { method: "DELETE" });
    let data: unknown;
    try {
      data = await resp.json();
    } catch {
      data = null;
    }
    if (resp.status >= 400) {
      throw new BackendClientError(
        `Delete connector failed (${resp.status}): ${JSON.stringify(data)}`,
        resp.status
      );
    }
  }

  async resyncConnectorRecords(
    connectorInstanceId: string,
    opts: { fullSync?: boolean }
  ): Promise<void> {
    const url = `${this.base}/api/v1/knowledgeBase/resync/connector`;
    const resp = await this.request(url, {
      method: "POST",
      body: JSON.stringify({
        connectorName: FOLDER_SYNC_CONNECTOR_TYPE,
        connectorId: connectorInstanceId,
        fullSync: opts.fullSync ?? true,
      }),
    });
    let data: unknown;
    try {
      data = await resp.json();
    } catch {
      data = null;
    }
    if (resp.status >= 400) {
      throw new BackendClientError(
        `Resync connector failed (${resp.status}): ${JSON.stringify(data)}`,
        resp.status
      );
    }
  }

  async listKnowledgeBaseRecords(params: {
    page?: number;
    limit?: number;
    connectors?: string[];
    indexingStatus?: string[];
    search?: string;
    source?: "all" | "local" | "connector";
    sortBy?: string;
    sortOrder?: "asc" | "desc";
  }): Promise<{
    records: Record<string, unknown>[];
    pagination: {
      page: number;
      limit: number;
      totalCount: number;
      totalPages: number;
    };
  }> {
    const url = new URL(`${this.base}/api/v1/knowledgeBase/records`);
    const page = params.page ?? 1;
    const limit = params.limit ?? 50;
    url.searchParams.set("page", String(page));
    url.searchParams.set("limit", String(Math.min(100, Math.max(1, limit))));
    if (params.search?.trim()) {
      url.searchParams.set("search", params.search.trim());
    }
    if (params.connectors?.length) {
      url.searchParams.set("connectors", params.connectors.join(","));
    }
    if (params.indexingStatus?.length) {
      url.searchParams.set("indexingStatus", params.indexingStatus.join(","));
    }
    if (params.source) {
      url.searchParams.set("source", params.source);
    }
    if (params.sortBy) {
      url.searchParams.set("sortBy", params.sortBy);
    }
    if (params.sortOrder) {
      url.searchParams.set("sortOrder", params.sortOrder);
    }
    const resp = await this.request(url.toString(), { method: "GET" });
    let data: unknown;
    try {
      data = await resp.json();
    } catch (e) {
      throw new BackendClientError(
        `Invalid JSON from list records: ${e}`,
        resp.status
      );
    }
    if (resp.status >= 400) {
      throw new BackendClientError(
        `List records failed (${resp.status}): ${JSON.stringify(data)}`,
        resp.status
      );
    }
    const body =
      typeof data === "object" && data !== null
        ? (data as {
            records?: Record<string, unknown>[];
            pagination?: Record<string, unknown>;
          })
        : {};
    const pag = body.pagination ?? {};
    return {
      records: Array.isArray(body.records) ? body.records : [],
      pagination: {
        page: Number(pag.page) || page,
        limit: Number(pag.limit) || limit,
        totalCount: Number(pag.totalCount) || 0,
        totalPages: Number(pag.totalPages) || 0,
      },
    };
  }

  /**
   * Knowledge Hub browse/search (same API as the web app).
   * Node gateway: GET /api/v1/knowledgeBase/knowledge-hub/nodes[/parentType/parentId]
   */
  private async getKnowledgeHubNodes(opts: {
    parentType?: "app" | "recordGroup" | "folder" | "record";
    parentId?: string;
    page: number;
    limit: number;
    sortBy?: string;
    sortOrder?: "asc" | "desc";
    /** Comma-separated (API); use `record` for file rows only. */
    nodeTypes?: string;
    indexingStatus?: string;
    connectorIds?: string;
    q?: string;
    include?: string;
  }): Promise<{
    success: boolean;
    items: Record<string, unknown>[];
    pagination?: KnowledgeHubNodesPagination;
    error?: string;
  }> {
    const limit = Math.min(200, Math.max(1, opts.limit));
    const page = Math.max(1, opts.page);
    let path = `${this.base}/api/v1/knowledgeBase/knowledge-hub/nodes`;
    if (opts.parentType && opts.parentId) {
      path += `/${encodeURIComponent(opts.parentType)}/${encodeURIComponent(
        opts.parentId
      )}`;
    }
    const url = new URL(path);
    url.searchParams.set("page", String(page));
    url.searchParams.set("limit", String(limit));
    if (opts.sortBy) {
      url.searchParams.set("sortBy", opts.sortBy);
    }
    if (opts.sortOrder) {
      url.searchParams.set("sortOrder", opts.sortOrder);
    }
    if (opts.nodeTypes) {
      url.searchParams.set("nodeTypes", opts.nodeTypes);
    }
    if (opts.indexingStatus) {
      url.searchParams.set("indexingStatus", opts.indexingStatus);
    }
    if (opts.connectorIds) {
      url.searchParams.set("connectorIds", opts.connectorIds);
    }
    if (opts.q) {
      url.searchParams.set("q", opts.q);
    }
    if (opts.include) {
      url.searchParams.set("include", opts.include);
    }

    const resp = await this.request(url.toString(), { method: "GET" });
    let data: unknown;
    try {
      data = await resp.json();
    } catch (e) {
      throw new BackendClientError(
        `Invalid JSON from knowledge hub nodes: ${e}`,
        resp.status
      );
    }
    if (resp.status >= 400) {
      throw new BackendClientError(
        `Knowledge hub nodes failed (${resp.status}): ${JSON.stringify(data)}`,
        resp.status
      );
    }
    const body = knowledgeHubResponsePayload(data);
    const success = body.success !== false;
    const itemsRaw = body.items;
    const items = Array.isArray(itemsRaw)
      ? (itemsRaw as Record<string, unknown>[])
      : [];
    const pag = body.pagination as Record<string, unknown> | undefined;
    const pagination: KnowledgeHubNodesPagination | undefined = pag
      ? {
          page: Number(pag.page) || page,
          limit: Number(pag.limit) || limit,
          totalItems: Number(pag.totalItems) || 0,
          totalPages: Number(pag.totalPages) || 0,
          hasNext: Boolean(pag.hasNext),
          hasPrev: Boolean(pag.hasPrev),
        }
      : undefined;
    return {
      success,
      items,
      pagination,
      error:
        typeof body.error === "string" ? body.error : undefined,
    };
  }

  /**
   * Records for one connector **instance** (daemon / etcd id).
   *
   * Uses the same Knowledge Hub API as the web UI (`/knowledge-hub/nodes/...`), not
   * `GET /knowledgeBase/records` — the latter omits Neo4j connector files that live under
   * `App → RecordGroup → Record` without direct user→record permission edges.
   */
  async listKnowledgeBaseRecordsForConnectorInstance(
    connectorInstanceId: string,
    params?: {
      indexingStatus?: string[];
      search?: string;
      /** Stop after this many matching records (default 500). */
      maxRecords?: number;
    }
  ): Promise<Record<string, unknown>[]> {
    const maxRecords = params?.maxRecords ?? 500;
    const searchRaw = params?.search?.trim() ?? "";
    const search = searchRaw.toLowerCase();
    const indexingCsv = params?.indexingStatus?.length
      ? params.indexingStatus.join(",")
      : undefined;

    const pushRecords = (
      acc: Record<string, unknown>[],
      items: Record<string, unknown>[],
      seen: Set<string>
    ): void => {
      for (const node of items) {
        if (String(node.nodeType ?? "").toLowerCase() !== "record") {
          continue;
        }
        const id = String(node.id ?? "").trim();
        if (!id || seen.has(id)) {
          continue;
        }
        if (
          search.length > 0 &&
          !String(node.name ?? "")
            .toLowerCase()
            .includes(search)
        ) {
          continue;
        }
        seen.add(id);
        acc.push(knowledgeHubNodeToKbRecordRow(node, connectorInstanceId));
      }
    };

    // Global search (Python requires q length >= 2)
    if (searchRaw.length >= 2) {
      const out: Record<string, unknown>[] = [];
      const seen = new Set<string>();
      let page = 1;
      const pageLimit = 100;
      const maxPages = 40;
      while (out.length < maxRecords && page <= maxPages) {
        const kh = await this.getKnowledgeHubNodes({
          page,
          limit: pageLimit,
          sortBy: "name",
          sortOrder: "asc",
          nodeTypes: "record",
          indexingStatus: indexingCsv,
          connectorIds: connectorInstanceId,
          q: searchRaw,
        });
        if (!kh.success) {
          throw new BackendClientError(
            kh.error || "Knowledge hub search failed",
            null
          );
        }
        pushRecords(out, kh.items, seen);
        if (!kh.pagination?.hasNext) {
          break;
        }
        page += 1;
      }
      return out.slice(0, maxRecords);
    }

    const out: Record<string, unknown>[] = [];
    const seen = new Set<string>();

    /** Global search scoped by connector instance (same path the web app uses for filters). */
    const collectGlobalFromConnector = async () => {
      let page = 1;
      const pageLimit = 100;
      const maxPages = 40;
      while (out.length < maxRecords && page <= maxPages) {
        const kh = await this.getKnowledgeHubNodes({
          page,
          limit: pageLimit,
          sortBy: "name",
          sortOrder: "asc",
          nodeTypes: "record",
          indexingStatus: indexingCsv,
          connectorIds: connectorInstanceId,
        });
        if (!kh.success) {
          throw new BackendClientError(
            kh.error || "Knowledge hub connector search failed",
            null
          );
        }
        pushRecords(out, kh.items, seen);
        if (!kh.pagination?.hasNext) {
          break;
        }
        page += 1;
      }
    };

    await collectGlobalFromConnector();
    if (out.length > 0) {
      return out.slice(0, maxRecords);
    }

    const collectFromRecordGroup = async (recordGroupId: string) => {
      let page = 1;
      const pageLimit = 100;
      const maxPages = 40;
      while (out.length < maxRecords && page <= maxPages) {
        const kh = await this.getKnowledgeHubNodes({
          parentType: "recordGroup",
          parentId: recordGroupId,
          page,
          limit: pageLimit,
          sortBy: "name",
          sortOrder: "asc",
          nodeTypes: "record",
          indexingStatus: indexingCsv,
        });
        if (!kh.success) {
          throw new BackendClientError(
            kh.error || "Knowledge hub nodes failed",
            null
          );
        }
        pushRecords(out, kh.items, seen);
        if (!kh.pagination?.hasNext) {
          break;
        }
        page += 1;
      }
    };

    const appKh = await this.getKnowledgeHubNodes({
      parentType: "app",
      parentId: connectorInstanceId,
      page: 1,
      limit: 100,
      sortBy: "name",
      sortOrder: "asc",
    });
    if (!appKh.success) {
      throw new BackendClientError(
        appKh.error || "Knowledge hub app children failed",
        null
      );
    }

    const groups = appKh.items.filter(
      (n) => String(n.nodeType ?? "").toLowerCase() === "recordgroup"
    );

    if (groups.length > 0) {
      for (const g of groups) {
        const gid = String(g.id ?? "").trim();
        if (!gid) {
          continue;
        }
        await collectFromRecordGroup(gid);
        if (out.length >= maxRecords) {
          break;
        }
      }
    } else {
      await collectFromRecordGroup(connectorInstanceId);
    }

    return out.slice(0, maxRecords);
  }

  async getConnectorKnowledgeStats(
    connectorInstanceId: string
  ): Promise<Record<string, unknown>> {
    const url = `${this.base}/api/v1/knowledgeBase/stats/${encodeURIComponent(
      connectorInstanceId
    )}`;
    const resp = await this.request(url, { method: "GET" });
    let data: unknown;
    try {
      data = await resp.json();
    } catch (e) {
      throw new BackendClientError(
        `Invalid JSON from connector stats: ${e}`,
        resp.status
      );
    }
    if (resp.status >= 400) {
      throw new BackendClientError(
        `Connector stats failed (${resp.status}): ${JSON.stringify(data)}`,
        resp.status
      );
    }
    const raw =
      typeof data === "object" && data !== null
        ? (data as Record<string, unknown>)
        : {};
    return unwrapConnectorKnowledgeStats(raw);
  }

  async reindexKnowledgeBaseRecord(
    recordId: string,
    body?: { depth?: number }
  ): Promise<void> {
    const url = `${this.base}/api/v1/knowledgeBase/reindex/record/${encodeURIComponent(
      recordId
    )}`;
    const resp = await this.request(url, {
      method: "POST",
      body: JSON.stringify(body && Object.keys(body).length ? body : {}),
    });
    let data: unknown;
    try {
      data = await resp.json();
    } catch {
      data = null;
    }
    if (resp.status >= 400) {
      throw new BackendClientError(
        `Reindex record failed (${resp.status}): ${JSON.stringify(data)}`,
        resp.status
      );
    }
  }

  /**
   * POST reindex for each record id (same path as the web app per file).
   * Use for bulk manual indexing when {@link reindexConnectorRecordsByStatus} matches
   * no rows (Neo4j `get_records_by_status` requires `Record.connectorId`; Folder Sync
   * files under a record group may not have it set).
   */
  async queueKnowledgeBaseReindexForRecordIds(recordIds: string[]): Promise<void> {
    const ids = recordIds
      .map((id) => String(id ?? "").trim())
      .filter(Boolean);
    if (ids.length === 0) {
      return;
    }
    for (let i = 0; i < ids.length; i += REINDEX_CONCURRENCY) {
      const batch = ids.slice(i, i + REINDEX_CONCURRENCY);
      await Promise.all(batch.map((rid) => this.reindexKnowledgeBaseRecord(rid)));
    }
  }

  /**
   * Publish connector bulk reindex-by-status (Kafka). May not find Neo4j connector
   * records without `connectorId` on the `Record` node — prefer
   * {@link queueKnowledgeBaseReindexForRecordIds} with ids from Knowledge Hub list.
   */
  /**
   * Submit a batch of file change events (CREATED, MODIFIED, DELETED, RENAMED, MOVED, etc.)
   * to the backend for incremental sync processing.
   */
  async notifyFileChanges(
    connectorInstanceId: string,
    events: {
      type: string;
      path: string;
      oldPath?: string;
      timestamp: number;
      size?: number;
      isDirectory: boolean;
    }[],
    batchId?: string
  ): Promise<void> {
    if (events.length === 0) return;
    const id =
      batchId?.trim() ||
      `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const url = `${this.base}/api/v1/connectors/${encodeURIComponent(
      connectorInstanceId
    )}/file-events`;
    const resp = await this.request(url, {
      method: "POST",
      body: JSON.stringify({
        batchId: id,
        events,
        timestamp: Date.now(),
      }),
    });
    let data: unknown;
    try {
      data = await resp.json();
    } catch {
      data = null;
    }
    if (resp.status >= 400) {
      throw new BackendClientError(
        `File events submission failed (${resp.status}): ${JSON.stringify(data)}`,
        resp.status
      );
    }
  }

  async reindexConnectorRecordsByStatus(
    connectorInstanceId: string,
    statusFilters: string[]
  ): Promise<void> {
    const url = `${this.base}/api/v1/knowledgeBase/reindex-failed/connector`;
    const resp = await this.request(url, {
      method: "POST",
      body: JSON.stringify({
        app: FOLDER_SYNC_CONNECTOR_TYPE,
        connectorId: connectorInstanceId,
        statusFilters,
      }),
    });
    let data: unknown;
    try {
      data = await resp.json();
    } catch {
      data = null;
    }
    if (resp.status >= 400) {
      throw new BackendClientError(
        `Queue reindex by status failed (${resp.status}): ${JSON.stringify(
          data
        )}`,
        resp.status
      );
    }
  }
}
