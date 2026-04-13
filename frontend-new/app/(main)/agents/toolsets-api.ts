import { apiClient } from '@/lib/api';

const PAGE_SIZE = 20;

/** Backend caps `limit` on my-toolsets and agents toolsets list routes (e.g. ≤ 200). */
export const MAX_TOOLSETS_LIST_LIMIT = 100;

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
}

export interface ToolsetsPageResponse {
  toolsets: BuilderSidebarToolset[];
  hasNext: boolean;
}

function mapToSidebar(inst: Record<string, unknown>): BuilderSidebarToolset {
  const toolsRaw = (inst.tools as Record<string, unknown>[]) || [];
  const toolsetType = (inst.toolsetType as string) || (inst.instanceName as string) || '';
  return {
    name: toolsetType || (inst.instanceName as string) || '',
    normalized_name: toolsetType,
    displayName: (inst.instanceName as string) || (inst.displayName as string) || toolsetType || '',
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
    isConfigured: Boolean(inst.isConfigured),
    isAuthenticated: Boolean(inst.isAuthenticated ?? false),
    isFromRegistry: Boolean(inst.isFromRegistry),
    instanceId: inst.instanceId as string | undefined,
    instanceName: inst.instanceName as string | undefined,
    toolsetType,
    authType: (inst.authType as string) || 'NONE',
    auth: (inst.auth && typeof inst.auth === 'object' ? inst.auth : {}) as Record<string, unknown>,
  };
}

async function fetchAllToolsetPages(
  fetchPage: (page: number) => Promise<ToolsetsPageResponse>
): Promise<BuilderSidebarToolset[]> {
  const all: BuilderSidebarToolset[] = [];
  let page = 1;
  for (;;) {
    const res = await fetchPage(page);
    all.push(...res.toolsets);
    if (!res.hasNext) break;
    page += 1;
    if (page > 500) break;
  }
  return all;
}

export const ToolsetsApi = {
  /** All pages — for agent builder catalog (replaces /agents/tools/list). */
  async getAllMyToolsets(params?: {
    search?: string;
    includeRegistry?: boolean;
    limitPerPage?: number;
  }): Promise<BuilderSidebarToolset[]> {
    const limit = params?.limitPerPage ?? PAGE_SIZE;
    return fetchAllToolsetPages((page) =>
      this.getMyToolsets({
        page,
        limit,
        search: params?.search,
        includeRegistry: params?.includeRegistry ?? true,
      })
    );
  },

  async getAllAgentToolsets(
    agentKey: string,
    params?: { search?: string; includeRegistry?: boolean; limitPerPage?: number }
  ): Promise<BuilderSidebarToolset[]> {
    const limit = params?.limitPerPage ?? PAGE_SIZE;
    return fetchAllToolsetPages((page) =>
      this.getAgentToolsets(agentKey, {
        page,
        limit,
        search: params?.search,
        includeRegistry: params?.includeRegistry ?? true,
      })
    );
  },

  async getMyToolsets(params: {
    page?: number;
    limit?: number;
    search?: string;
    includeRegistry?: boolean;
  }): Promise<ToolsetsPageResponse> {
    const { data } = await apiClient.get<{
      toolsets?: Record<string, unknown>[];
      pagination?: { hasNext?: boolean };
    }>('/api/v1/toolsets/my-toolsets', {
      params: {
        page: params.page ?? 1,
        limit: Math.min(params.limit ?? PAGE_SIZE, MAX_TOOLSETS_LIST_LIMIT),
        search: params.search || undefined,
        includeRegistry: params.includeRegistry ?? true,
      },
    });
    const rows = data?.toolsets ?? [];
    return {
      toolsets: rows.map(mapToSidebar),
      hasNext: data?.pagination?.hasNext ?? false,
    };
  },

  async getAgentToolsets(
    agentKey: string,
    params: { page?: number; limit?: number; search?: string; includeRegistry?: boolean }
  ): Promise<ToolsetsPageResponse> {
    const { data } = await apiClient.get<{
      toolsets?: Record<string, unknown>[];
      pagination?: { hasNext?: boolean };
    }>(`/api/v1/toolsets/agents/${agentKey}`, {
      params: {
        page: params.page ?? 1,
        limit: Math.min(params.limit ?? PAGE_SIZE, MAX_TOOLSETS_LIST_LIMIT),
        search: params.search || undefined,
        includeRegistry: params.includeRegistry ?? true,
      },
    });
    const rows = data?.toolsets ?? [];
    return {
      toolsets: rows.map(mapToSidebar),
      hasNext: data?.pagination?.hasNext ?? false,
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
};
