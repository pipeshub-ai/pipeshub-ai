import { apiClient } from '@/lib/api';
import type {
  CreateServiceAccountPayload,
  Delegation,
  DelegationCapability,
  DelegationEventListApiResponse,
  DelegationListApiResponse,
  GrantDelegationPayload,
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
const DELEGATIONS_URL = '/api/v1/service-account-delegations';

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

export const DelegationsApi = {
  /**
   * GET /api/v1/service-account-delegations/capability
   *
   * Answers in both editions, so a failure here means the request did not
   * arrive rather than that the capability is missing. Treated as unavailable
   * either way: offering a control that cannot work is worse than hiding one
   * that could.
   */
  async capability(): Promise<DelegationCapability> {
    const { data } = await apiClient.get<DelegationCapability>(
      `${DELEGATIONS_URL}/capability`
    );
    return data;
  },

  /** GET /api/v1/service-account-delegations?serviceAccountId= */
  async list(serviceAccountId: string): Promise<DelegationListApiResponse> {
    const { data } = await apiClient.get<DelegationListApiResponse>(
      DELEGATIONS_URL,
      { params: { serviceAccountId } }
    );
    return data;
  },

  /** POST /api/v1/service-account-delegations */
  async grant(body: GrantDelegationPayload): Promise<Delegation> {
    const { data } = await apiClient.post<{ delegation: Delegation }>(
      DELEGATIONS_URL,
      body
    );
    return data.delegation;
  },

  /** DELETE /api/v1/service-account-delegations/:subjectUserId */
  async revoke(subjectUserId: string, serviceAccountId: string): Promise<void> {
    await apiClient.delete(`${DELEGATIONS_URL}/${subjectUserId}`, {
      params: { serviceAccountId },
    });
  },

  /**
   * GET /api/v1/service-account-delegations/events
   * Omit the account id to read the log across the whole organisation.
   */
  async events(
    serviceAccountId?: string,
    limit?: number
  ): Promise<DelegationEventListApiResponse> {
    const { data } = await apiClient.get<DelegationEventListApiResponse>(
      `${DELEGATIONS_URL}/events`,
      { params: { serviceAccountId, limit } }
    );
    return data;
  },
};
