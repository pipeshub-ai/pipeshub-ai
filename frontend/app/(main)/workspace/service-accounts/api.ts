import { apiClient } from '@/lib/api';
import type {
  CreateServiceAccountPayload,
  CreateServiceTokenApiResponse,
  CreateServiceTokenPayload,
  ServiceAccount,
  ServiceAccountListApiResponse,
  ServiceTokenListApiResponse,
  ServiceTokenScopesApiResponse,
  UpdateServiceAccountPayload,
} from './types';

const ACCOUNTS_URL = '/api/v1/service-accounts';
const TOKENS_URL = '/api/v1/service-tokens';

export const ServiceAccountsApi = {
  /** GET /api/v1/service-accounts */
  async list(): Promise<ServiceAccountListApiResponse> {
    const { data } = await apiClient.get<ServiceAccountListApiResponse>(ACCOUNTS_URL);
    return data;
  },

  /** POST /api/v1/service-accounts */
  async create(body: CreateServiceAccountPayload): Promise<ServiceAccount> {
    const { data } = await apiClient.post<ServiceAccount>(ACCOUNTS_URL, body);
    return data;
  },

  /** PATCH /api/v1/service-accounts/:id */
  async update(
    id: string,
    body: UpdateServiceAccountPayload
  ): Promise<ServiceAccount> {
    const { data } = await apiClient.patch<ServiceAccount>(
      `${ACCOUNTS_URL}/${id}`,
      body
    );
    return data;
  },

  /** DELETE /api/v1/service-accounts/:id — also revokes every token it holds. */
  async remove(id: string): Promise<void> {
    await apiClient.delete(`${ACCOUNTS_URL}/${id}`);
  },
};

export const ServiceTokensApi = {
  /** GET /api/v1/service-tokens?serviceAccountId= */
  async list(serviceAccountId: string): Promise<ServiceTokenListApiResponse> {
    const { data } = await apiClient.get<ServiceTokenListApiResponse>(TOKENS_URL, {
      params: { serviceAccountId },
    });
    return data;
  },

  /** GET /api/v1/service-tokens/scopes */
  async getScopes(): Promise<ServiceTokenScopesApiResponse> {
    const { data } = await apiClient.get<ServiceTokenScopesApiResponse>(
      `${TOKENS_URL}/scopes`
    );
    return data;
  },

  /**
   * POST /api/v1/service-tokens
   * The response carries the raw token exactly once.
   */
  async create(
    body: CreateServiceTokenPayload
  ): Promise<CreateServiceTokenApiResponse> {
    const { data } = await apiClient.post<CreateServiceTokenApiResponse>(
      TOKENS_URL,
      body
    );
    return data;
  },

  /** DELETE /api/v1/service-tokens/:tokenId?serviceAccountId= */
  async revoke(tokenId: string, serviceAccountId: string): Promise<void> {
    await apiClient.delete(`${TOKENS_URL}/${tokenId}`, {
      params: { serviceAccountId },
    });
  },
};
