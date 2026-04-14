import { apiClient } from '@/lib/api';
import type {
  CreateOAuthClientApiResponse,
  CreateOAuthClientPayload,
  OAuthClient,
  OAuthClientsListResponse,
  OAuthScopesApiResponse,
  RegenerateOAuthClientSecretResponse,
  UpdateOAuthClientPayload,
} from './types';

const BASE_URL = '/api/v1/oauth-clients';

export const Oauth2Api = {
  /**
   * List OAuth 2.0 clients for the organization.
   * GET /api/v1/oauth-clients?page=&limit=
   */
  async listOAuthClients(params: {
    page: number;
    limit: number;
    search?: string;
  }): Promise<OAuthClientsListResponse> {
    const { data } = await apiClient.get<OAuthClientsListResponse>(BASE_URL, {
      params: {
        page: params.page,
        limit: params.limit,
        ...(params.search ? { search: params.search } : {}),
      },
    });
    return data;
  },

  /**
   * Available OAuth scopes for client registration.
   * GET /api/v1/oauth-clients/scopes
   */
  async getScopes(): Promise<OAuthScopesApiResponse> {
    const { data } = await apiClient.get<OAuthScopesApiResponse>(
      `${BASE_URL}/scopes`
    );
    return data;
  },

  /**
   * Create OAuth 2.0 client application.
   * POST /api/v1/oauth-clients
   */
  async createOAuthClient(
    body: CreateOAuthClientPayload
  ): Promise<CreateOAuthClientApiResponse> {
    const { data } = await apiClient.post<CreateOAuthClientApiResponse>(
      BASE_URL,
      body
    );
    return data;
  },

  /**
   * Get a single OAuth 2.0 client by id.
   * GET /api/v1/oauth-clients/:id
   */
  async getOAuthClient(id: string): Promise<OAuthClient> {
    const { data } = await apiClient.get<OAuthClient>(`${BASE_URL}/${id}`);
    return data;
  },

  /**
   * Update OAuth 2.0 client application.
   * PATCH /api/v1/oauth-clients/:id
   */
  async updateOAuthClient(
    id: string,
    body: UpdateOAuthClientPayload
  ): Promise<OAuthClient | void> {
    const { data } = await apiClient.put<OAuthClient | void>(
      `${BASE_URL}/${id}`,
      body
    );
    return data;
  },

  /**
   * Regenerate client secret.
   * POST /api/v1/oauth-clients/:id/regenerate-secret
   */
  async regenerateOAuthClientSecret(
    id: string
  ): Promise<RegenerateOAuthClientSecretResponse> {
    const { data } = await apiClient.post<RegenerateOAuthClientSecretResponse>(
      `${BASE_URL}/${id}/regenerate-secret`
    );
    return data;
  },

  /**
   * Revoke all issued tokens for this OAuth client.
   * POST /api/v1/oauth-clients/:id/revoke-all-tokens
   */
  async revokeOAuthClientTokens(id: string): Promise<void> {
    await apiClient.post(`${BASE_URL}/${id}/revoke-all-tokens`);
  },

  /**
   * Suspend OAuth 2.0 client (blocks new tokens / usage per backend rules).
   * POST /api/v1/oauth-clients/:id/suspend
   */
  async suspendOAuthClient(id: string): Promise<void> {
    await apiClient.post(`${BASE_URL}/${id}/suspend`);
  },

  /**
   * Reactivate a suspended OAuth 2.0 client.
   * POST /api/v1/oauth-clients/:id/activate
   */
  async activateOAuthClient(id: string): Promise<void> {
    await apiClient.post(`${BASE_URL}/${id}/activate`);
  },

  /**
   * Delete OAuth 2.0 client application.
   * DELETE /api/v1/oauth-clients/:id
   */
  async deleteOAuthClient(id: string): Promise<void> {
    await apiClient.delete(`${BASE_URL}/${id}`);
  },
};
