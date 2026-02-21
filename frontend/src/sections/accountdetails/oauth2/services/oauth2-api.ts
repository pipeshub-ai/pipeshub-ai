/**
 * OAuth 2.0 (Pipeshub OAuth Provider) API Service
 * Manages OAuth client apps: list, create, get, update, delete, tokens, scopes.
 */

import axios from 'src/utils/axios';

const OAUTH2_CLIENTS_BASE = '/api/v1/oauth-clients';

// ---------------------------------------------------------------------------
// Types (aligned with backend oauth.types.ts)
// ---------------------------------------------------------------------------

export interface OAuth2App {
  id: string;
  slug: string;
  clientId: string;
  name: string;
  description?: string;
  redirectUris: string[];
  allowedGrantTypes: string[];
  allowedScopes: string[];
  status: string;
  logoUrl?: string;
  homepageUrl?: string;
  privacyPolicyUrl?: string;
  termsOfServiceUrl?: string;
  isConfidential: boolean;
  accessTokenLifetime: number;
  refreshTokenLifetime: number;
  createdAt: string;
  updatedAt: string;
}

export interface OAuth2AppWithSecret extends OAuth2App {
  clientSecret: string;
}

export interface CreateOAuth2AppRequest {
  name: string;
  description?: string;
  redirectUris: string[];
  allowedGrantTypes?: string[];
  allowedScopes: string[];
  homepageUrl?: string;
  privacyPolicyUrl?: string;
  termsOfServiceUrl?: string;
  isConfidential?: boolean;
  accessTokenLifetime?: number;
  refreshTokenLifetime?: number;
}

export interface UpdateOAuth2AppRequest {
  name?: string;
  description?: string;
  redirectUris?: string[];
  allowedGrantTypes?: string[];
  allowedScopes?: string[];
  homepageUrl?: string | null;
  privacyPolicyUrl?: string | null;
  termsOfServiceUrl?: string | null;
  accessTokenLifetime?: number;
  refreshTokenLifetime?: number;
}

export interface ListOAuth2AppsQuery {
  page?: number;
  limit?: number;
  status?: string;
  search?: string;
}

export interface PaginatedOAuth2Apps {
  data: OAuth2App[];
  pagination: {
    page: number;
    limit: number;
    total: number;
    totalPages: number;
  };
}

export interface ScopeCategory {
  [category: string]: Array<{ name: string; description: string; category: string }>;
}

export interface TokenListItem {
  id: string;
  tokenType: 'access' | 'refresh';
  userId?: string;
  scopes: string[];
  createdAt: string;
  expiresAt: string;
  isRevoked: boolean;
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

export const OAuth2Api = {
  async listApps(params?: ListOAuth2AppsQuery): Promise<PaginatedOAuth2Apps> {
    const { data } = await axios.get(OAUTH2_CLIENTS_BASE, { params });
    return data;
  },

  async getApp(appId: string): Promise<OAuth2App> {
    const { data } = await axios.get(`${OAUTH2_CLIENTS_BASE}/${appId}`);
    return data;
  },

  async createApp(body: CreateOAuth2AppRequest): Promise<{ message: string; app: OAuth2AppWithSecret }> {
    const { data } = await axios.post(OAUTH2_CLIENTS_BASE, body);
    return data;
  },

  async updateApp(appId: string, body: UpdateOAuth2AppRequest): Promise<{ message: string; app: OAuth2App }> {
    const { data } = await axios.put(`${OAUTH2_CLIENTS_BASE}/${appId}`, body);
    return data;
  },

  async deleteApp(appId: string): Promise<{ message: string }> {
    const { data } = await axios.delete(`${OAUTH2_CLIENTS_BASE}/${appId}`);
    return data;
  },

  async regenerateSecret(appId: string): Promise<{ message: string; clientId: string; clientSecret: string }> {
    const { data } = await axios.post(`${OAUTH2_CLIENTS_BASE}/${appId}/regenerate-secret`);
    return data;
  },

  async suspendApp(appId: string): Promise<{ message: string; app: OAuth2App }> {
    const { data } = await axios.post(`${OAUTH2_CLIENTS_BASE}/${appId}/suspend`);
    return data;
  },

  async activateApp(appId: string): Promise<{ message: string; app: OAuth2App }> {
    const { data } = await axios.post(`${OAUTH2_CLIENTS_BASE}/${appId}/activate`);
    return data;
  },

  async listScopes(): Promise<{ scopes: ScopeCategory }> {
    const { data } = await axios.get(`${OAUTH2_CLIENTS_BASE}/scopes`);
    return data;
  },

  async listAppTokens(appId: string): Promise<{ tokens: TokenListItem[] }> {
    const { data } = await axios.get(`${OAUTH2_CLIENTS_BASE}/${appId}/tokens`);
    return data;
  },

  async revokeAllTokens(appId: string): Promise<{ message: string }> {
    const { data } = await axios.post(`${OAUTH2_CLIENTS_BASE}/${appId}/revoke-all-tokens`);
    return data;
  },
};
