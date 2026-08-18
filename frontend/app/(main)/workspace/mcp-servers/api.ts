import { apiClient } from '@/lib/api';
import type {
  McpAuthenticatePayload,
  McpCatalogResponse,
  McpInstancesResponse,
  McpMyServerEntry,
  McpMyServersResponse,
  McpOAuthAuthorizationUrlResponse,
  McpOAuthCallbackResponse,
  McpOAuthConfigPayload,
  McpOAuthConfigResponse,
  McpOAuthDiscoveryResult,
  McpServerInstance,
  McpServerInstancePayload,
  McpServerTemplate,
  McpSuccessResponse,
  McpToolsResponse,
} from './types';

const BASE_URL = '/api/v1/mcp-servers';

export const McpServersApi = {
  // ── Catalog (read-only, in-memory templates) ──

  async getCatalog(params?: { page?: number; limit?: number; search?: string }): Promise<McpCatalogResponse> {
    const { data } = await apiClient.get(`${BASE_URL}/catalog`, { params });
    return data;
  },

  async getCatalogTemplate(typeId: string): Promise<McpServerTemplate> {
    const { data } = await apiClient.get(`${BASE_URL}/catalog/${encodeURIComponent(typeId)}`);
    return data;
  },

  // ── Instances (admin-managed, org-scoped) ──

  async listInstances(): Promise<McpInstancesResponse> {
    const { data } = await apiClient.get(`${BASE_URL}/instances`);
    return data;
  },

  async createInstance(payload: McpServerInstancePayload): Promise<McpServerInstance> {
    const { data } = await apiClient.post(`${BASE_URL}/instances`, payload);
    return data;
  },

  async getInstance(instanceId: string): Promise<McpServerInstance> {
    const { data } = await apiClient.get(`${BASE_URL}/instances/${encodeURIComponent(instanceId)}`);
    return data;
  },

  async updateInstance(instanceId: string, payload: McpServerInstancePayload): Promise<McpServerInstance> {
    const { data } = await apiClient.put(`${BASE_URL}/instances/${encodeURIComponent(instanceId)}`, payload);
    return data;
  },

  async deleteInstance(instanceId: string): Promise<McpSuccessResponse & { _id: string }> {
    const { data } = await apiClient.delete(`${BASE_URL}/instances/${encodeURIComponent(instanceId)}`);
    return data;
  },

  // ── Auth — API token / headers ──

  async authenticate(instanceId: string, payload: McpAuthenticatePayload): Promise<McpSuccessResponse> {
    const { data } = await apiClient.post(`${BASE_URL}/instances/${encodeURIComponent(instanceId)}/authenticate`, payload);
    return data;
  },

  async updateCredentials(instanceId: string, payload: McpAuthenticatePayload): Promise<McpSuccessResponse> {
    const { data } = await apiClient.put(`${BASE_URL}/instances/${encodeURIComponent(instanceId)}/credentials`, payload);
    return data;
  },

  async removeCredentials(instanceId: string): Promise<McpSuccessResponse> {
    const { data } = await apiClient.delete(`${BASE_URL}/instances/${encodeURIComponent(instanceId)}/credentials`);
    return data;
  },

  async autoAuthenticate(instanceId: string): Promise<McpSuccessResponse> {
    const { data } = await apiClient.post(`${BASE_URL}/instances/${encodeURIComponent(instanceId)}/auto-authenticate`);
    return data;
  },

  async reauthenticate(instanceId: string): Promise<McpSuccessResponse> {
    const { data } = await apiClient.post(`${BASE_URL}/instances/${encodeURIComponent(instanceId)}/reauthenticate`);
    return data;
  },

  // ── OAuth + DCR ──

  async getOAuthAuthorizationUrl(instanceId: string, baseUrl?: string): Promise<McpOAuthAuthorizationUrlResponse> {
    const { data } = await apiClient.get(
      `${BASE_URL}/instances/${encodeURIComponent(instanceId)}/oauth/authorize`,
      { params: baseUrl ? { baseUrl } : undefined }
    );
    return data;
  },

  async completeOAuthCallback(params: { code: string; state: string }): Promise<McpOAuthCallbackResponse> {
    const { data } = await apiClient.get(`${BASE_URL}/oauth/callback`, { params });
    return data;
  },

  async refreshOAuthToken(instanceId: string): Promise<McpSuccessResponse> {
    const { data } = await apiClient.post(`${BASE_URL}/instances/${encodeURIComponent(instanceId)}/oauth/refresh`);
    return data;
  },

  async getOAuthConfig(instanceId: string): Promise<McpOAuthConfigResponse> {
    const { data } = await apiClient.get(`${BASE_URL}/instances/${encodeURIComponent(instanceId)}/oauth-config`);
    return data;
  },

  async updateOAuthConfig(instanceId: string, payload: McpOAuthConfigPayload): Promise<McpSuccessResponse> {
    const { data } = await apiClient.put(`${BASE_URL}/instances/${encodeURIComponent(instanceId)}/oauth-config`, payload);
    return data;
  },

  /** Admin-only live probe — does this server URL support OAuth DCR, and what are its real endpoints? */
  async discoverOAuthMetadata(url: string): Promise<McpOAuthDiscoveryResult> {
    const { data } = await apiClient.post(`${BASE_URL}/oauth/discover`, { url });
    return data;
  },

  // ── Discovery / consumption ──

  async getMyMcpServers(includeTools = true): Promise<McpMyServersResponse> {
    const { data } = await apiClient.get(`${BASE_URL}/my-mcp-servers`, { params: { includeTools } });
    return data;
  },

  async getInstanceTools(instanceId: string): Promise<McpToolsResponse> {
    const { data } = await apiClient.get(`${BASE_URL}/instances/${encodeURIComponent(instanceId)}/tools`);
    return data;
  },

  // ── Agent-scoped (service-account agent) credentials ──
  // Mirrors the personal-scoped methods above one-to-one; Node forwards these to
  // Python's `/agents/{agentKey}/...` routes (`mcp_servers.py`), which independently
  // re-checks agent edit access regardless of the scope gate applied at the route layer.

  async getAgentMcpServers(agentKey: string, includeTools = true): Promise<McpMyServersResponse> {
    const { data } = await apiClient.get(`${BASE_URL}/agents/${encodeURIComponent(agentKey)}`, {
      params: { includeTools },
    });
    return data;
  },

  async authenticateAgentInstance(
    agentKey: string,
    instanceId: string,
    payload: McpAuthenticatePayload
  ): Promise<McpSuccessResponse> {
    const { data } = await apiClient.post(
      `${BASE_URL}/agents/${encodeURIComponent(agentKey)}/instances/${encodeURIComponent(instanceId)}/authenticate`,
      payload
    );
    return data;
  },

  async updateAgentInstanceCredentials(
    agentKey: string,
    instanceId: string,
    payload: McpAuthenticatePayload
  ): Promise<McpSuccessResponse> {
    const { data } = await apiClient.put(
      `${BASE_URL}/agents/${encodeURIComponent(agentKey)}/instances/${encodeURIComponent(instanceId)}/credentials`,
      payload
    );
    return data;
  },

  async removeAgentInstanceCredentials(agentKey: string, instanceId: string): Promise<McpSuccessResponse> {
    const { data } = await apiClient.delete(
      `${BASE_URL}/agents/${encodeURIComponent(agentKey)}/instances/${encodeURIComponent(instanceId)}/credentials`
    );
    return data;
  },

  async reauthenticateAgentInstance(agentKey: string, instanceId: string): Promise<McpSuccessResponse> {
    const { data } = await apiClient.post(
      `${BASE_URL}/agents/${encodeURIComponent(agentKey)}/instances/${encodeURIComponent(instanceId)}/reauthenticate`
    );
    return data;
  },

  async getAgentOAuthAuthorizationUrl(
    agentKey: string,
    instanceId: string,
    baseUrl?: string
  ): Promise<McpOAuthAuthorizationUrlResponse> {
    const { data } = await apiClient.get(
      `${BASE_URL}/agents/${encodeURIComponent(agentKey)}/instances/${encodeURIComponent(instanceId)}/oauth/authorize`,
      { params: baseUrl ? { baseUrl } : undefined }
    );
    return data;
  },

  /** Pages are not needed here — agent MCP server lists are org-instance-bounded (small); find by id. */
  async findAgentMcpServerByInstanceId(agentKey: string, instanceId: string): Promise<McpMyServerEntry | undefined> {
    const { instances } = await this.getAgentMcpServers(agentKey, false);
    return instances.find((i) => i._id === instanceId);
  },
};
