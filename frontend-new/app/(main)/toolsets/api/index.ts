import { apiClient } from '@/lib/api';

const PAGE_SIZE = 20;

/** Backend caps `limit` on my-toolsets and agents toolsets list routes (`le=200` on connector). */
export const MAX_TOOLSETS_LIST_LIMIT = 200;

export interface BuilderToolsetTool {
  name: string;
  fullName: string;
  description: string;
  appName?: string;
}

/** Normalized row for the agent builder sidebar */
export interface BuilderSidebarToolset {
  name: string;
  normalized_name: string;
  displayName: string;
  description: string;
  iconPath: string;
  category: string;
  toolCount: number;
  tools: BuilderToolsetTool[];
  isConfigured: boolean;
  isAuthenticated: boolean;
  isFromRegistry: boolean;
  instanceId?: string;
  instanceName?: string;
  toolsetType?: string;
  /** From API — drives credential dialog (service account). */
  authType?: string;
  /** Existing auth values (non-OAuth) for hydrate form. */
  auth?: Record<string, unknown>;
  /** Org instance creator (admin list). */
  createdBy?: string;
  createdAtTimestamp?: number;
  updatedAtTimestamp?: number;
  oauthConfigId?: string;
  /** Present on registry-backed / synthetic rows from my-toolsets when `includeRegistry` is true. */
  supportedAuthTypes?: string[];
}

/** Mirrors Python list responses `pagination` object. */
export interface ToolsetsListPagination {
  page: number;
  limit: number;
  total: number;
  totalPages: number;
  hasNext: boolean;
  hasPrev: boolean;
}

export interface ToolsetsPageResponse {
  toolsets: BuilderSidebarToolset[];
  hasNext: boolean;
  pagination?: ToolsetsListPagination;
  /** Present when `authStatus` is omitted (GET /my-toolsets). */
  filterCounts?: ToolsetsFilterCounts;
}

export interface ToolsetsFilterCounts {
  all: number;
  authenticated: number;
  notAuthenticated: number;
}

/** One row from GET /api/v1/toolsets/registry */
export interface RegistryToolsetRow {
  name: string;
  displayName: string;
  description: string;
  category: string;
  appGroup: string;
  iconPath: string;
  supportedAuthTypes: string[];
  toolCount: number;
}

/** Normalized org OAuth app row from GET /api/v1/toolsets/oauth-configs/:toolsetType (admin). */
export interface ToolsetOauthConfigListRow {
  _id: string;
  oauthInstanceName?: string;
  clientId?: string;
  /** Returned for org admins when the API includes the stored secret. */
  clientSecret?: string;
  clientSecretSet?: boolean;
  /**
   * Other string-safe values from the merged OAuth config document (e.g. baseUrl, tenantId),
   * excluding secrets, for read-only admin summaries.
   */
  extraConfig?: Record<string, string>;
}

function oauthConfigStr(v: unknown): string {
  if (typeof v === 'string') return v;
  if (v == null) return '';
  return String(v);
}

const OAUTH_LIST_ROW_TOP_KEYS = new Set([
  '_id',
  'id',
  'config',
  'orgId',
  'org_id',
  'toolsetType',
  'toolset_type',
  'type',
  'oauthInstanceName',
  'oauth_instance_name',
  'clientId',
  'client_id',
  'clientSecret',
  'client_secret',
  'clientSecretSet',
  'client_secret_set',
]);

function isSensitiveOauthListKey(key: string): boolean {
  const lower = key.toLowerCase();
  return (
    lower.includes('secret') ||
    lower.includes('password') ||
    lower === 'token' ||
    lower.endsWith('_token') ||
    lower === 'accesstoken' ||
    lower === 'refreshtoken'
  );
}

function normalizeToolsetOauthConfigListRow(raw: Record<string, unknown>): ToolsetOauthConfigListRow {
  const _id = oauthConfigStr(raw._id ?? raw.id).trim();
  const clientId = oauthConfigStr(raw.clientId ?? raw.client_id).trim();
  const clientSecret = oauthConfigStr(raw.clientSecret ?? raw.client_secret).trim() || undefined;
  const explicitSet = raw.clientSecretSet ?? raw.client_secret_set;
  const clientSecretSet =
    typeof explicitSet === 'boolean' ? explicitSet : Boolean(clientSecret);

  const extraConfig: Record<string, string> = {};
  for (const [k, v] of Object.entries(raw)) {
    if (OAUTH_LIST_ROW_TOP_KEYS.has(k)) continue;
    if (isSensitiveOauthListKey(k)) continue;
    if (v === null || v === undefined) continue;
    if (typeof v === 'object') continue;
    const s = oauthConfigStr(v).trim();
    if (!s) continue;
    extraConfig[k] = s;
  }

  return {
    _id,
    oauthInstanceName:
      oauthConfigStr(raw.oauthInstanceName ?? raw.oauth_instance_name).trim() || undefined,
    clientId: clientId || undefined,
    clientSecret,
    clientSecretSet,
    ...(Object.keys(extraConfig).length > 0 ? { extraConfig } : {}),
  };
}

function mapToSidebar(inst: Record<string, unknown>): BuilderSidebarToolset {
  const toolsRaw = (inst.tools as Record<string, unknown>[]) || [];
  const toolsetType = (inst.toolsetType as string) || (inst.instanceName as string) || '';
  /** Registry / toolset human name from API — never the per-instance label (use `instanceName` for that). */
  const toolsetDisplayName =
    (inst.displayName as string) || (inst.display_name as string) || toolsetType || '';
  const instanceLabel = (inst.instanceName as string) || (inst.instance_name as string) || '';
  return {
    name: toolsetType || instanceLabel || '',
    normalized_name: toolsetType,
    /** Integration title for catalog cards; omit instanceName so grouped cards stay type-named. */
    displayName: toolsetDisplayName || toolsetType || '',
    description: (inst.description as string) || '',
    iconPath: (inst.iconPath as string) || '',
    category: (inst.category as string) || 'app',
    toolCount: (inst.toolCount as number) ?? toolsRaw.length,
    tools: toolsRaw.map((t) => ({
      name: (t.name as string) || '',
      fullName: (t.fullName as string) || '',
      description: (t.description as string) || '',
      appName: toolsetType,
    })),
    isConfigured: Boolean(inst.isConfigured ?? inst.is_configured),
    isAuthenticated: Boolean(inst.isAuthenticated ?? inst.is_authenticated ?? false),
    isFromRegistry: Boolean(inst.isFromRegistry),
    instanceId: inst.instanceId as string | undefined,
    instanceName: inst.instanceName as string | undefined,
    toolsetType,
    authType: (inst.authType as string) || 'NONE',
    ...(inst.auth != null && typeof inst.auth === 'object'
      ? { auth: inst.auth as Record<string, unknown> }
      : {}),
    createdBy: inst.createdBy as string | undefined,
    createdAtTimestamp: inst.createdAtTimestamp as number | undefined,
    updatedAtTimestamp: inst.updatedAtTimestamp as number | undefined,
    oauthConfigId:
      (inst.oauthConfigId as string | undefined) ||
      (inst.oauth_config_id as string | undefined),
    supportedAuthTypes: (
      (inst.supportedAuthTypes as string[] | undefined) ??
      (inst.supported_auth_types as string[] | undefined)
    )?.filter(Boolean),
  };
}

function mapRegistryRow(row: Record<string, unknown>): RegistryToolsetRow {
  return {
    name: (row.name as string) || '',
    displayName: (row.displayName as string) || (row.name as string) || '',
    description: (row.description as string) || '',
    category: (row.category as string) || 'app',
    appGroup: (row.group as string) || (row.category as string) || '',
    iconPath: (row.iconPath as string) || '',
    supportedAuthTypes:
      (row.supportedAuthTypes as string[] | undefined) ??
      (row.supported_auth_types as string[] | undefined) ??
      [],
    toolCount: (row.toolCount as number) ?? 0,
  };
}

type ListPaginationPayload = {
  hasNext?: boolean;
  hasPrev?: boolean;
  page?: number;
  limit?: number;
  total?: number;
  totalPages?: number;
};

function paginationFromListPayload(
  pag: ListPaginationPayload | undefined,
  limit: number,
  rowCount: number
): { pagination: ToolsetsListPagination | undefined; hasNext: boolean } {
  const pagination: ToolsetsListPagination | undefined =
    pag && typeof pag.page === 'number'
      ? {
          page: pag.page,
          limit: typeof pag.limit === 'number' ? pag.limit : limit,
          total: typeof pag.total === 'number' ? pag.total : rowCount,
          totalPages: typeof pag.totalPages === 'number' ? pag.totalPages : 1,
          hasNext: Boolean(pag.hasNext),
          hasPrev: Boolean(pag.hasPrev),
        }
      : undefined;
  return {
    pagination,
    hasNext: pagination?.hasNext ?? Boolean(pag?.hasNext),
  };
}

async function fetchAllToolsetPages(
  fetchPage: (page: number) => Promise<ToolsetsPageResponse>
): Promise<{ toolsets: BuilderSidebarToolset[]; filterCounts?: ToolsetsFilterCounts }> {
  const all: BuilderSidebarToolset[] = [];
  let filterCounts: ToolsetsFilterCounts | undefined;
  let page = 1;
  for (;;) {
    const res = await fetchPage(page);
    if (page === 1 && res.filterCounts) {
      filterCounts = res.filterCounts;
    }
    all.push(...res.toolsets);
    if (!res.hasNext) break;
    page += 1;
    if (page > 500) break;
  }
  return { toolsets: all, filterCounts };
}

export const ToolsetsApi = {
  /**
   * All pages — for agent builder catalog (replaces /agents/tools/list).
   * `filterCounts` is taken from the first page only (same totals the list API returns regardless of `limit`).
   */
  async getAllMyToolsets(params?: {
    search?: string;
    includeRegistry?: boolean;
    limitPerPage?: number;
    authStatus?: 'authenticated' | 'not-authenticated';
  }): Promise<{ toolsets: BuilderSidebarToolset[]; filterCounts?: ToolsetsFilterCounts }> {
    const limit = params?.limitPerPage ?? PAGE_SIZE;
    return fetchAllToolsetPages((page) =>
      this.getMyToolsets({
        page,
        limit,
        search: params?.search,
        includeRegistry: params?.includeRegistry ?? false,
        authStatus: params?.authStatus,
      })
    );
  },

  async getAllAgentToolsets(
    agentKey: string,
    params?: { search?: string; includeRegistry?: boolean; limitPerPage?: number }
  ): Promise<BuilderSidebarToolset[]> {
    const limit = params?.limitPerPage ?? PAGE_SIZE;
    const { toolsets } = await fetchAllToolsetPages((page) =>
      this.getAgentToolsets(agentKey, {
        page,
        limit,
        search: params?.search,
        includeRegistry: params?.includeRegistry ?? true,
      })
    );
    return toolsets;
  },

  async getMyToolsets(params: {
    page?: number;
    limit?: number;
    search?: string;
    includeRegistry?: boolean;
    /** Server-side filter; omit for all. */
    authStatus?: 'authenticated' | 'not-authenticated';
    /** Scope list to one toolset type (instances for that type only). */
    toolsetType?: string;
  }): Promise<ToolsetsPageResponse> {
    const limit = Math.min(params.limit ?? PAGE_SIZE, MAX_TOOLSETS_LIST_LIMIT);
    const { data } = await apiClient.get<{
      toolsets?: Record<string, unknown>[];
      pagination?: {
        hasNext?: boolean;
        hasPrev?: boolean;
        page?: number;
        limit?: number;
        total?: number;
        totalPages?: number;
      };
      filterCounts?: { all?: number; authenticated?: number; notAuthenticated?: number };
    }>('/api/v1/toolsets/my-toolsets', {
      params: {
        page: params.page ?? 1,
        limit,
        search: params.search || undefined,
        includeRegistry: params.includeRegistry ?? false,
        authStatus: params.authStatus,
        toolsetType: params.toolsetType?.trim() || undefined,
      },
    });
    const rows = data?.toolsets ?? [];
    const fc = data?.filterCounts;
    const { pagination, hasNext } = paginationFromListPayload(data?.pagination, limit, rows.length);
    return {
      toolsets: rows.map(mapToSidebar),
      hasNext,
      pagination,
      filterCounts:
        fc && typeof fc.all === 'number'
          ? {
              all: fc.all,
              authenticated: fc.authenticated ?? 0,
              notAuthenticated: fc.notAuthenticated ?? 0,
            }
          : undefined,
    };
  },

  async getAgentToolsets(
    agentKey: string,
    params: {
      page?: number;
      limit?: number;
      search?: string;
      includeRegistry?: boolean;
      toolsetType?: string;
    }
  ): Promise<ToolsetsPageResponse> {
    const limit = Math.min(params.limit ?? PAGE_SIZE, MAX_TOOLSETS_LIST_LIMIT);
    const { data } = await apiClient.get<{
      toolsets?: Record<string, unknown>[];
      pagination?: {
        hasNext?: boolean;
        hasPrev?: boolean;
        page?: number;
        limit?: number;
        total?: number;
        totalPages?: number;
      };
    }>(`/api/v1/toolsets/agents/${agentKey}`, {
      params: {
        page: params.page ?? 1,
        limit,
        search: params.search || undefined,
        includeRegistry: params.includeRegistry ?? true,
        toolsetType: params.toolsetType?.trim() || undefined,
      },
    });
    const rows = data?.toolsets ?? [];
    const { pagination, hasNext } = paginationFromListPayload(data?.pagination, limit, rows.length);
    return {
      toolsets: rows.map(mapToSidebar),
      hasNext,
      pagination,
    };
  },

  /** Pages through my-toolsets until `instanceId` is found (OAuth verify after popup). */
  async findMyToolsetByInstanceId(instanceId: string): Promise<BuilderSidebarToolset | undefined> {
    let page = 1;
    for (let guard = 0; guard < 100; guard += 1) {
      const res = await this.getMyToolsets({
        page,
        limit: MAX_TOOLSETS_LIST_LIMIT,
        includeRegistry: false,
      });
      const hit = res.toolsets.find((t) => t.instanceId === instanceId);
      if (hit) return hit;
      if (!res.hasNext) return undefined;
      page += 1;
    }
    return undefined;
  },

  /** Pages through agent toolsets until `instanceId` is found (OAuth verify after popup). */
  async findAgentToolsetByInstanceId(
    agentKey: string,
    instanceId: string
  ): Promise<BuilderSidebarToolset | undefined> {
    let page = 1;
    for (let guard = 0; guard < 100; guard += 1) {
      const res = await this.getAgentToolsets(agentKey, {
        page,
        limit: MAX_TOOLSETS_LIST_LIMIT,
        includeRegistry: false,
      });
      const hit = res.toolsets.find((t) => t.instanceId === instanceId);
      if (hit) return hit;
      if (!res.hasNext) return undefined;
      page += 1;
    }
    return undefined;
  },

  /** GET /api/v1/toolsets/registry/:toolsetType/schema */
  async getToolsetRegistrySchema(toolsetType: string): Promise<unknown> {
    const { data } = await apiClient.get<unknown>(
      `/api/v1/toolsets/registry/${encodeURIComponent(toolsetType)}/schema`
    );
    return data;
  },

  /**
   * GET /api/v1/toolsets/registry — paginated toolset types from the Python registry.
   * Use `group_by_category: false` so results respect `page` / `limit`.
   */
  async getRegistryToolsets(params?: {
    page?: number;
    limit?: number;
    search?: string;
    includeTools?: boolean;
    includeToolCount?: boolean;
    groupByCategory?: boolean;
  }): Promise<{ toolsets: RegistryToolsetRow[]; hasNext: boolean; total: number }> {
    const page = params?.page ?? 1;
    const limit = Math.min(params?.limit ?? PAGE_SIZE, MAX_TOOLSETS_LIST_LIMIT);
    const { data } = await apiClient.get<{
      toolsets?: Record<string, unknown>[];
      pagination?: { total?: number; totalPages?: number; page?: number };
    }>('/api/v1/toolsets/registry', {
      params: {
        page,
        limit,
        search: params?.search || undefined,
        include_tools: params?.includeTools ?? false,
        include_tool_count: params?.includeToolCount ?? true,
        group_by_category: params?.groupByCategory ?? false,
      },
    });
    const rows = data?.toolsets ?? [];
    const total = data?.pagination?.total ?? rows.length;
    const totalPages = data?.pagination?.totalPages ?? 1;
    const hasNext = page < totalPages;
    return {
      toolsets: rows.map(mapRegistryRow),
      hasNext,
      total,
    };
  },

  /** Admin: GET /api/v1/toolsets/oauth-configs/:toolsetType — org-scoped OAuth apps for this toolset type. */
  async listToolsetOAuthConfigs(toolsetType: string): Promise<ToolsetOauthConfigListRow[]> {
    const { data } = await apiClient.get<{
      oauthConfigs?: Record<string, unknown>[];
    }>(`/api/v1/toolsets/oauth-configs/${encodeURIComponent(toolsetType)}`);
    const raw = data?.oauthConfigs ?? [];
    return raw.map(normalizeToolsetOauthConfigListRow);
  },

  /**
   * GET /api/v1/toolsets/instances/:id — full org instance (includes `auth` / authConfig stored on instance).
   * Use for admin hydrate; list rows from my-toolsets may omit or null `auth` for non-OAuth.
   */
  async getToolsetInstance(instanceId: string): Promise<Record<string, unknown>> {
    const { data } = await apiClient.get<{
      status?: string;
      instance?: Record<string, unknown>;
    }>(`/api/v1/toolsets/instances/${encodeURIComponent(instanceId)}`);
    const body = data ?? {};
    const inst = body.instance;
    if (inst && typeof inst === 'object' && !Array.isArray(inst)) {
      return inst;
    }
    /* Legacy / alternate: flat instance document */
    if ('toolsetType' in body || '_id' in body) {
      return body as Record<string, unknown>;
    }
    return {};
  },

  /** Admin: POST /api/v1/toolsets/instances */
  async createToolsetInstance(body: {
    instanceName: string;
    toolsetType: string;
    authType: string;
    baseUrl?: string;
    authConfig?: Record<string, unknown>;
    oauthConfigId?: string;
    oauthInstanceName?: string;
  }): Promise<Record<string, unknown>> {
    const { data } = await apiClient.post<{ instance?: Record<string, unknown> }>(
      '/api/v1/toolsets/instances',
      body
    );
    return data?.instance ?? {};
  },

  /** Admin: PUT /api/v1/toolsets/instances/:id — rename instance and/or update OAuth app credentials. */
  async updateToolsetInstance(
    instanceId: string,
    body: {
      instanceName?: string;
      baseUrl?: string;
      oauthConfigId?: string;
      authConfig?: Record<string, unknown>;
    }
  ): Promise<{
    status?: string;
    message?: string;
    deauthenticatedUserCount?: number;
    instance?: Record<string, unknown>;
  }> {
    const { data } = await apiClient.put<{
      status?: string;
      message?: string;
      deauthenticatedUserCount?: number;
      instance?: Record<string, unknown>;
    }>(`/api/v1/toolsets/instances/${encodeURIComponent(instanceId)}`, body);
    return data ?? {};
  },

  /** Admin: DELETE /api/v1/toolsets/instances/:id */
  async deleteToolsetInstance(instanceId: string): Promise<{
    status?: string;
    message?: string;
    deletedCredentialsCount?: number;
  }> {
    const { data } = await apiClient.delete<{
      status?: string;
      message?: string;
      deletedCredentialsCount?: number;
    }>(`/api/v1/toolsets/instances/${encodeURIComponent(instanceId)}`);
    return data ?? {};
  },

  async authenticateAgentToolset(
    agentKey: string,
    instanceId: string,
    auth: Record<string, unknown>
  ): Promise<void> {
    await apiClient.post(`/api/v1/toolsets/agents/${agentKey}/instances/${instanceId}/authenticate`, {
      auth,
    });
  },

  async updateAgentToolsetCredentials(
    agentKey: string,
    instanceId: string,
    auth: Record<string, unknown>
  ): Promise<void> {
    await apiClient.put(`/api/v1/toolsets/agents/${agentKey}/instances/${instanceId}/credentials`, {
      auth,
    });
  },

  async removeAgentToolsetCredentials(agentKey: string, instanceId: string): Promise<void> {
    await apiClient.delete(`/api/v1/toolsets/agents/${agentKey}/instances/${instanceId}/credentials`);
  },

  async reauthenticateAgentToolset(agentKey: string, instanceId: string): Promise<void> {
    await apiClient.post(`/api/v1/toolsets/agents/${agentKey}/instances/${instanceId}/reauthenticate`);
  },

  async getAgentToolsetOAuthUrl(
    agentKey: string,
    instanceId: string,
    baseUrl?: string
  ): Promise<{ authorizationUrl?: string; success?: boolean }> {
    const { data } = await apiClient.get<{ authorizationUrl?: string; success?: boolean }>(
      `/api/v1/toolsets/agents/${agentKey}/instances/${instanceId}/oauth/authorize`,
      { params: baseUrl ? { base_url: baseUrl } : undefined }
    );
    return data ?? {};
  },

  /** User-scoped toolset instance OAuth (builder sidebar). */
  async getInstanceOAuthAuthorizationUrl(
    instanceId: string,
    baseUrl?: string
  ): Promise<{ authorizationUrl?: string; success?: boolean }> {
    const { data } = await apiClient.get<{ authorizationUrl?: string; success?: boolean }>(
      `/api/v1/toolsets/instances/${encodeURIComponent(instanceId)}/oauth/authorize`,
      { params: baseUrl ? { base_url: baseUrl } : undefined }
    );
    return data ?? {};
  },

  /** Current user authenticates a toolset instance (non-OAuth credentials). */
  async authenticateMyToolsetInstance(
    instanceId: string,
    auth: Record<string, unknown>
  ): Promise<void> {
    await apiClient.post(
      `/api/v1/toolsets/instances/${encodeURIComponent(instanceId)}/authenticate`,
      { auth }
    );
  },

  async updateMyToolsetCredentials(
    instanceId: string,
    auth: Record<string, unknown>
  ): Promise<void> {
    await apiClient.put(
      `/api/v1/toolsets/instances/${encodeURIComponent(instanceId)}/credentials`,
      { auth }
    );
  },

  async removeMyToolsetCredentials(instanceId: string): Promise<void> {
    await apiClient.delete(`/api/v1/toolsets/instances/${encodeURIComponent(instanceId)}/credentials`);
  },

  async reauthenticateMyToolsetInstance(instanceId: string): Promise<void> {
    await apiClient.post(`/api/v1/toolsets/instances/${encodeURIComponent(instanceId)}/reauthenticate`);
  },

  /**
   * GET /api/v1/toolsets/oauth/callback — completes OAuth in the browser (popup flow).
   * Forwards to the connector service; requires an authenticated session (Bearer / cookies).
   */
  async completeToolsetOAuthCallback(params: {
    code: string;
    state: string;
    oauthError: string | null;
    baseUrl: string;
  }): Promise<{ success?: boolean; redirectUrl?: string; redirect_url?: string }> {
    const query: Record<string, string> = {
      code: params.code,
      state: params.state,
      base_url: params.baseUrl,
    };
    if (params.oauthError) {
      query.error = params.oauthError;
    }
    const { data } = await apiClient.get<{
      success?: boolean;
      redirectUrl?: string;
      redirect_url?: string;
    }>('/api/v1/toolsets/oauth/callback', {
      params: query,
      suppressErrorToast: true,
    });
    return data ?? {};
  },
};
