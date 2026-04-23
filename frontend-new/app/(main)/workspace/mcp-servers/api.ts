import { apiClient } from '@/lib/api';
import type {
  MCPServerTemplate,
  MCPServerInstance,
  MCPServerTool,
  PaginationInfo,
} from './types';

const BASE = '/api/v1/mcp-servers';

// ============================================================================
// Catalog
// ============================================================================

export const McpServersApi = {
  async getCatalog(params?: {
    page?: number;
    limit?: number;
    search?: string;
  }): Promise<{
    items: MCPServerTemplate[];
    total: number;
    page: number;
    limit: number;
    totalPages: number;
  }> {
    const { data } = await apiClient.get(`${BASE}/catalog`, { params });
    return data;
  },

  async getCatalogItem(typeId: string): Promise<{
    template: MCPServerTemplate & {
      configSchema?: Record<string, unknown>;
      authConfig?: Record<string, unknown>;
    };
  }> {
    const { data } = await apiClient.get(`${BASE}/catalog/${typeId}`);
    return data;
  },

  // ============================================================================
  // Instance CRUD
  // ============================================================================

  async getInstances(): Promise<{ instances: MCPServerInstance[] }> {
    const { data } = await apiClient.get(`${BASE}/instances`);
    return data;
  },

  async createInstance(body: {
    instanceName: string;
    serverType?: string;
    displayName?: string;
    description?: string;
    transport?: string;
    command?: string;
    args?: string[];
    url?: string;
    authMode?: string;
    supportedAuthTypes?: string[];
    requiredEnv?: string[];
    iconPath?: string;
    clientId?: string;
    clientSecret?: string;
    apiToken?: string;
    useAdminAuth?: boolean;
    /** For headers auth mode: the name of the HTTP header (e.g. "Authorization") */
    headerName?: string;
    /** OAuth provider endpoints for custom servers (no registry template) */
    authorizationUrl?: string;
    tokenUrl?: string;
    scopes?: string[];
  }): Promise<{ instance: MCPServerInstance; message: string }> {
    const { data } = await apiClient.post(`${BASE}/instances`, body);
    // Normalize _id -> instanceId so the shape is consistent with other endpoints
    if (data.instance?._id && !data.instance.instanceId) {
      data.instance.instanceId = data.instance._id;
    }
    return data;
  },

  async getInstance(
    instanceId: string
  ): Promise<{ instance: MCPServerInstance }> {
    const { data } = await apiClient.get(`${BASE}/instances/${instanceId}`);
    return data;
  },

  async updateInstance(
    instanceId: string,
    body: Partial<MCPServerInstance>
  ): Promise<{ instance: MCPServerInstance; message: string }> {
    const { data } = await apiClient.put(
      `${BASE}/instances/${instanceId}`,
      body
    );
    return data;
  },

  async deleteInstance(instanceId: string): Promise<{ message: string }> {
    const { data } = await apiClient.delete(`${BASE}/instances/${instanceId}`);
    return data;
  },

  // ============================================================================
  // User Authentication — API Token / Credentials
  // ============================================================================

  async authenticateInstance(
    instanceId: string,
    auth: Record<string, unknown>
  ): Promise<{ isAuthenticated: boolean; message: string }> {
    const { data } = await apiClient.post(
      `${BASE}/instances/${instanceId}/authenticate`,
      { auth }
    );
    return data;
  },

  async autoAuthenticate(
    instanceId: string
  ): Promise<{ isAuthenticated: boolean; message: string }> {
    const { data } = await apiClient.post(
      `${BASE}/instances/${instanceId}/auto-authenticate`
    );
    return data;
  },

  async updateCredentials(
    instanceId: string,
    auth: Record<string, unknown>
  ): Promise<{ message: string }> {
    const { data } = await apiClient.put(
      `${BASE}/instances/${instanceId}/credentials`,
      { auth }
    );
    return data;
  },

  async removeCredentials(instanceId: string): Promise<{ message: string }> {
    const { data } = await apiClient.delete(
      `${BASE}/instances/${instanceId}/credentials`
    );
    return data;
  },

  // ============================================================================
  // OAuth Flow
  // ============================================================================

  async oauthAuthorize(
    instanceId: string
  ): Promise<{ authorizationUrl: string; state: string }> {
    const baseUrl = window.location.origin;
    const { data } = await apiClient.get(
      `${BASE}/instances/${instanceId}/oauth/authorize`,
      { params: { baseUrl } }
    );
    return data;
  },

  async oauthRefresh(
    instanceId: string
  ): Promise<{ isAuthenticated: boolean; message: string; expiresAt?: number }> {
    const { data } = await apiClient.post(
      `${BASE}/instances/${instanceId}/oauth/refresh`
    );
    return data;
  },

  async oauthCallbackGlobal(params: {
    code: string;
    state: string;
    error?: string;
  }): Promise<{ success: boolean; redirectUrl?: string; message: string }> {
    const { data } = await apiClient.get(`${BASE}/oauth/callback`, {
      params: { ...params, baseUrl: window.location.origin },
    });
    return data;
  },

  // ============================================================================
  // OAuth Client Configuration (Admin — per instance)
  // ============================================================================

  async getInstanceOauthConfig(instanceId: string): Promise<{
    configured: boolean;
    config: Record<string, unknown> | null;
  }> {
    const { data } = await apiClient.get(
      `${BASE}/instances/${instanceId}/oauth-config`
    );
    return data;
  },

  async setInstanceOauthConfig(
    instanceId: string,
    body: { clientId: string; clientSecret?: string }
  ): Promise<{ message: string }> {
    const { data } = await apiClient.put(
      `${BASE}/instances/${instanceId}/oauth-config`,
      body
    );
    return data;
  },

  // ============================================================================
  // User Views
  // ============================================================================

  async getMyMcpServers(params?: {
    page?: number;
    limit?: number;
    search?: string;
    includeRegistry?: boolean;
    authStatus?: string;
  }): Promise<{
    mcpServers: MCPServerInstance[];
    pagination: PaginationInfo;
  }> {
    const { data } = await apiClient.get(`${BASE}/my-mcp-servers`, { params });
    return data;
  },

  // ============================================================================
  // Tool Discovery
  // ============================================================================

  async discoverInstanceTools(instanceId: string): Promise<{
    tools: MCPServerTool[];
    toolCount: number;
  }> {
    const { data } = await apiClient.get(
      `${BASE}/instances/${instanceId}/tools`
    );
    return data;
  },

  // ============================================================================
  // Agent-Scoped MCP
  // ============================================================================

  async getAgentMcpServers(
    agentKey: string,
    params?: {
      page?: number;
      limit?: number;
      search?: string;
      includeRegistry?: boolean;
    }
  ): Promise<{
    mcpServers: MCPServerInstance[];
    pagination: PaginationInfo;
  }> {
    const { data } = await apiClient.get(`${BASE}/agents/${agentKey}`, { params });
    return data;
  },

  async authenticateAgentInstance(
    agentKey: string,
    instanceId: string,
    auth: Record<string, unknown>
  ): Promise<{ isAuthenticated: boolean; message: string }> {
    const { data } = await apiClient.post(
      `${BASE}/agents/${agentKey}/instances/${instanceId}/authenticate`,
      { auth }
    );
    return data;
  },

  async updateAgentCredentials(
    agentKey: string,
    instanceId: string,
    auth: Record<string, unknown>
  ): Promise<{ message: string }> {
    const { data } = await apiClient.put(
      `${BASE}/agents/${agentKey}/instances/${instanceId}/credentials`,
      { auth }
    );
    return data;
  },

  async removeAgentCredentials(
    agentKey: string,
    instanceId: string
  ): Promise<{ message: string }> {
    const { data } = await apiClient.delete(
      `${BASE}/agents/${agentKey}/instances/${instanceId}/credentials`
    );
    return data;
  },
};
