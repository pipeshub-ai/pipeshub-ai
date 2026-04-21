import axios from 'src/utils/axios';
import type {
  MCPServerTemplate,
  MCPServerInstance,
  MCPServerTool,
} from '../types/agent';

const BASE = '/api/v1/mcp-servers';

// ============================================================================
// Pagination
// ============================================================================

export interface PaginationInfo {
  page: number;
  limit: number;
  total: number;
  totalPages: number;
  hasNext: boolean;
  hasPrev: boolean;
}

// ============================================================================
// Catalog
// ============================================================================

export async function getCatalog(params?: {
  page?: number;
  limit?: number;
  search?: string;
}): Promise<{ items: MCPServerTemplate[]; total: number; page: number; limit: number; totalPages: number }> {
  const { data } = await axios.get(`${BASE}/catalog`, { params });
  return data;
}

export async function getCatalogItem(typeId: string): Promise<{
  template: MCPServerTemplate & { configSchema?: Record<string, any>; authConfig?: Record<string, any> };
}> {
  const { data } = await axios.get(`${BASE}/catalog/${typeId}`);
  return data;
}

// ============================================================================
// Instance CRUD
// ============================================================================

export async function getInstances(): Promise<{ instances: MCPServerInstance[] }> {
  const { data } = await axios.get(`${BASE}/instances`);
  return data;
}

export type CreateCatalogInstanceBody = {
  instanceName: string;
  /** Any non-'custom' typeId from the catalog. */
  serverType: string;
  displayName?: string;
  description?: string;
  iconPath?: string;
  authMode?: string;
  clientId?: string;
  clientSecret?: string;
};

export type CreateCustomInstanceBody = {
  instanceName: string;
  serverType: 'custom';
  displayName?: string;
  description?: string;
  iconPath?: string;
  authMode?: string;
  transport?: string;
  command?: string;
  args?: string[];
  url?: string;
  supportedAuthTypes?: string[];
  requiredEnv?: string[];
  authHeaderMapping?: Record<string, string>;
  clientId?: string;
  clientSecret?: string;
};

export async function createInstance(
  body: CreateCatalogInstanceBody | CreateCustomInstanceBody
): Promise<{ instance: MCPServerInstance; message: string }> {
  const { data } = await axios.post(`${BASE}/instances`, body);
  return data;
}

export async function getInstance(instanceId: string): Promise<{ instance: MCPServerInstance }> {
  const { data } = await axios.get(`${BASE}/instances/${instanceId}`);
  return data;
}

export async function updateInstance(
  instanceId: string,
  body: Partial<MCPServerInstance>
): Promise<{ instance: MCPServerInstance; message: string }> {
  const { data } = await axios.put(`${BASE}/instances/${instanceId}`, body);
  return data;
}

export async function deleteInstance(instanceId: string): Promise<{ message: string }> {
  const { data } = await axios.delete(`${BASE}/instances/${instanceId}`);
  return data;
}

// ============================================================================
// User Authentication — API Token
// ============================================================================

export async function authenticateInstance(
  instanceId: string,
  auth: Record<string, any>
): Promise<{ isAuthenticated: boolean; message: string }> {
  const { data } = await axios.post(`${BASE}/instances/${instanceId}/authenticate`, { auth });
  return data;
}

export async function updateCredentials(
  instanceId: string,
  auth: Record<string, any>
): Promise<{ message: string }> {
  const { data } = await axios.put(`${BASE}/instances/${instanceId}/credentials`, { auth });
  return data;
}

export async function removeCredentials(instanceId: string): Promise<{ message: string }> {
  const { data } = await axios.delete(`${BASE}/instances/${instanceId}/credentials`);
  return data;
}

export async function reauthenticateInstance(instanceId: string): Promise<{ message: string }> {
  const { data } = await axios.post(`${BASE}/instances/${instanceId}/reauthenticate`);
  return data;
}

// ============================================================================
// OAuth Flow
// ============================================================================

export async function oauthAuthorize(
  instanceId: string
): Promise<{ authorizationUrl: string; state: string }> {
  const baseUrl = window.location.origin;
  const { data } = await axios.get(`${BASE}/instances/${instanceId}/oauth/authorize`, {
    params: { baseUrl },
  });
  return data;
}

export async function oauthCallbackGlobal(params: {
  code: string;
  state: string;
  error?: string;
}): Promise<{ success: boolean; redirectUrl?: string; message: string }> {
  const { data } = await axios.get(`${BASE}/oauth/callback`, {
    params: { ...params, baseUrl: window.location.origin },
  });
  return data;
}

export async function oauthCallback(
  instanceId: string,
  body: { code: string; state: string; redirectUri?: string }
): Promise<{ isAuthenticated: boolean; message: string; expiresAt?: number; scope?: string }> {
  const { data } = await axios.post(`${BASE}/instances/${instanceId}/oauth/callback`, body);
  return data;
}

export async function oauthRefresh(
  instanceId: string
): Promise<{ isAuthenticated: boolean; message: string; expiresAt?: number }> {
  const { data } = await axios.post(`${BASE}/instances/${instanceId}/oauth/refresh`);
  return data;
}

// ============================================================================
// OAuth Client Configuration (Admin — per instance)
// ============================================================================

export async function getInstanceOauthConfig(
  instanceId: string
): Promise<{ configured: boolean; config: Record<string, any> | null }> {
  const { data } = await axios.get(`${BASE}/instances/${instanceId}/oauth-config`);
  return data;
}

export async function setInstanceOauthConfig(
  instanceId: string,
  body: { clientId: string; clientSecret?: string }
): Promise<{ message: string }> {
  const { data } = await axios.put(`${BASE}/instances/${instanceId}/oauth-config`, body);
  return data;
}

// ============================================================================
// User Views
// ============================================================================

export async function getMyMcpServers(params?: {
  page?: number;
  limit?: number;
  search?: string;
  includeRegistry?: boolean;
  authStatus?: string;
}): Promise<{
  mcpServers: MCPServerInstance[];
  pagination: PaginationInfo;
}> {
  const { data } = await axios.get(`${BASE}/my-mcp-servers`, { params });
  return data;
}

// ============================================================================
// Tool Discovery
// ============================================================================

export async function discoverInstanceTools(instanceId: string): Promise<{
  tools: MCPServerTool[];
  toolCount: number;
}> {
  const { data } = await axios.get(`${BASE}/instances/${instanceId}/tools`);
  return data;
}

// ============================================================================
// Agent-Scoped
// ============================================================================

export async function getAgentMcpServers(
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
  const { data } = await axios.get(`${BASE}/agents/${agentKey}`, { params });
  return data;
}

export async function authenticateAgentInstance(
  agentKey: string,
  instanceId: string,
  auth: Record<string, any>
): Promise<{ isAuthenticated: boolean; message: string }> {
  const { data } = await axios.post(
    `${BASE}/agents/${agentKey}/instances/${instanceId}/authenticate`,
    { auth }
  );
  return data;
}

export async function updateAgentCredentials(
  agentKey: string,
  instanceId: string,
  auth: Record<string, any>
): Promise<{ message: string }> {
  const { data } = await axios.put(
    `${BASE}/agents/${agentKey}/instances/${instanceId}/credentials`,
    { auth }
  );
  return data;
}

export async function removeAgentCredentials(
  agentKey: string,
  instanceId: string
): Promise<{ message: string }> {
  const { data } = await axios.delete(
    `${BASE}/agents/${agentKey}/instances/${instanceId}/credentials`
  );
  return data;
}
