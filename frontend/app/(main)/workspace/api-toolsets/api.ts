import { apiClient } from '@/lib/api';
import { desktopAuthorizeParams } from '@/lib/electron';
import type {
  ApiCompiledSpec,
  ApiToolset,
  ApiToolsetConfig,
  ApiToolsetCredentials,
  ApiToolsetPayload,
  ApiToolsetTestResult,
} from './types';

const BASE_URL = '/api/v1/api-toolsets';

// The page shows the backend's own reason (a bad spec, a refused URL) instead of the generic toast.
const QUIET = { suppressErrorToast: true } as const;

const instanceUrl = (id: string, suffix = '') => `${BASE_URL}/instances/${encodeURIComponent(id)}${suffix}`;

/** Where the credential lives: the caller's own, or the one an admin shares with everyone. */
const credentialsUrl = (id: string, shared: boolean) =>
  instanceUrl(id, shared ? '/shared-credentials' : '/credentials');

export const ApiToolsetsApi = {
  /** Admin only: compiles a spec without saving it. */
  async validateSpec(spec: string): Promise<ApiCompiledSpec> {
    const { data } = await apiClient.post(`${BASE_URL}/validate`, { spec }, QUIET);
    return data;
  },

  async listInstances(): Promise<ApiToolset[]> {
    const { data } = await apiClient.get<{ instances: ApiToolset[] }>(`${BASE_URL}/instances`);
    return data?.instances ?? [];
  },

  /** Admin only. */
  async getConfig(id: string): Promise<ApiToolsetConfig> {
    const { data } = await apiClient.get(instanceUrl(id, '/config'));
    return data;
  },

  async createInstance(payload: ApiToolsetPayload): Promise<ApiToolsetConfig> {
    const { data } = await apiClient.post(`${BASE_URL}/instances`, payload, QUIET);
    return data;
  },

  async updateInstance(id: string, payload: ApiToolsetPayload): Promise<ApiToolsetConfig> {
    const { data } = await apiClient.put(instanceUrl(id), payload, QUIET);
    return data;
  },

  async deleteInstance(id: string): Promise<void> {
    await apiClient.delete(instanceUrl(id), QUIET);
  },

  async saveCredentials(id: string, credentials: ApiToolsetCredentials, shared: boolean): Promise<void> {
    await apiClient.put(credentialsUrl(id, shared), credentials, QUIET);
  },

  async deleteCredentials(id: string, shared: boolean): Promise<void> {
    await apiClient.delete(credentialsUrl(id, shared), QUIET);
  },

  async getAuthorizationUrl(id: string, shared: boolean): Promise<{ authorizationUrl: string }> {
    const { data } = await apiClient.get(
      instanceUrl(id, shared ? '/shared-credentials/oauth/authorize' : '/oauth/authorize'),
      { ...QUIET, params: desktopAuthorizeParams() },
    );
    return data;
  },

  async completeOAuthCallback(params: { code: string; state: string }): Promise<{
    success: boolean;
    instanceId: string;
    name: string;
  }> {
    const { data } = await apiClient.get(`${BASE_URL}/oauth/callback`, { params, ...QUIET });
    return data;
  },

  async testConnection(id: string, body: { toolName?: string; arguments?: Record<string, unknown> } = {}): Promise<ApiToolsetTestResult> {
    const { data } = await apiClient.post(instanceUrl(id, '/test'), body, QUIET);
    return data;
  },
};
