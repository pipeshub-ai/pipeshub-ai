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
  listApps(params?: ListOAuth2AppsQuery): Promise<PaginatedOAuth2Apps> {
    return axios.get(OAUTH2_CLIENTS_BASE, { params }).then((res) => res.data);
  },

  getApp(appId: string): Promise<OAuth2App> {
    return axios.get(`${OAUTH2_CLIENTS_BASE}/${appId}`).then((res) => res.data);
  },

  createApp(body: CreateOAuth2AppRequest): Promise<{ message: string; app: OAuth2AppWithSecret }> {
    return axios.post(OAUTH2_CLIENTS_BASE, body).then((res) => res.data);
  },

  updateApp(appId: string, body: UpdateOAuth2AppRequest): Promise<{ message: string; app: OAuth2App }> {
    return axios.put(`${OAUTH2_CLIENTS_BASE}/${appId}`, body).then((res) => res.data);
  },

  deleteApp(appId: string): Promise<{ message: string }> {
    return axios.delete(`${OAUTH2_CLIENTS_BASE}/${appId}`).then((res) => res.data);
  },

  regenerateSecret(appId: string): Promise<{ message: string; clientId: string; clientSecret: string }> {
    return axios.post(`${OAUTH2_CLIENTS_BASE}/${appId}/regenerate-secret`).then((res) => res.data);
  },

  suspendApp(appId: string): Promise<{ message: string; app: OAuth2App }> {
    return axios.post(`${OAUTH2_CLIENTS_BASE}/${appId}/suspend`).then((res) => res.data);
  },

  activateApp(appId: string): Promise<{ message: string; app: OAuth2App }> {
    return axios.post(`${OAUTH2_CLIENTS_BASE}/${appId}/activate`).then((res) => res.data);
  },

  listScopes(): Promise<{ scopes: ScopeCategory }> {
    return axios.get(`${OAUTH2_CLIENTS_BASE}/scopes`).then((res) => res.data);
  },

  listAppTokens(appId: string): Promise<{ tokens: TokenListItem[] }> {
    return axios.get(`${OAUTH2_CLIENTS_BASE}/${appId}/tokens`).then((res) => res.data);
  },

  revokeAllTokens(appId: string): Promise<{ message: string }> {
    return axios.post(`${OAUTH2_CLIENTS_BASE}/${appId}/revoke-all-tokens`).then((res) => res.data);
  },
};
