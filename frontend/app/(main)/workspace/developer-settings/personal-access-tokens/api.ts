import { apiClient } from '@/lib/api';
import type {
  CreatePatApiResponse,
  CreatePatPayload,
  PatListApiResponse,
  PatScopesApiResponse,
} from './types';

const BASE_URL = '/api/v1/personal-access-tokens';

export const PatApi = {
  /**
   * List the calling user's own personal access tokens.
   * GET /api/v1/personal-access-tokens
   */
  async listTokens(): Promise<PatListApiResponse> {
    const { data } = await apiClient.get<PatListApiResponse>(BASE_URL);
    return data;
  },

  /**
   * Scopes available to a new personal access token (the org's MCP scope set).
   * GET /api/v1/personal-access-tokens/scopes
   */
  async getScopes(): Promise<PatScopesApiResponse> {
    const { data } = await apiClient.get<PatScopesApiResponse>(
      `${BASE_URL}/scopes`
    );
    return data;
  },

  /**
   * Create a personal access token. The response carries the raw token
   * exactly once — the caller must show/copy it now.
   * POST /api/v1/personal-access-tokens
   */
  async createToken(body: CreatePatPayload): Promise<CreatePatApiResponse> {
    const { data } = await apiClient.post<CreatePatApiResponse>(
      BASE_URL,
      body
    );
    return data;
  },

  /**
   * Revoke one of the calling user's own personal access tokens.
   * DELETE /api/v1/personal-access-tokens/:id
   */
  async revokeToken(id: string, reason?: string): Promise<void> {
    await apiClient.delete(`${BASE_URL}/${id}`, {
      data: reason ? { reason } : undefined,
    });
  },
};
