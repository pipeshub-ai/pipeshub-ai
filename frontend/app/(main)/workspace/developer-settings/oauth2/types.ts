/**
 * OAuth 2.0 clients (developer settings).
 * List item shape from GET /api/v1/oauth-clients.
 */
export type OAuthGrantTypeValue =
  | 'authorization_code'
  | 'refresh_token'
  | 'client_credentials';

export type OAuthClientStatus = 'active' | 'suspended' | string;

export interface OAuthClient {
  id: string;
  slug?: string;
  clientId: string;
  name: string;
  description?: string | null;
  redirectUris?: string[];
  allowedGrantTypes?: OAuthGrantTypeValue[];
  allowedScopes?: string[];
  status?: OAuthClientStatus;
  isConfidential?: boolean;
  accessTokenLifetime?: number;
  refreshTokenLifetime?: number;
  homepageUrl?: string | null;
  privacyPolicyUrl?: string | null;
  termsOfServiceUrl?: string | null;
  createdAt?: string;
  updatedAt?: string;
}

export interface OAuthClientsPagination {
  page: number;
  limit: number;
  total: number;
  totalPages: number;
}

export interface OAuthClientsListResponse {
  data: OAuthClient[];
  pagination: OAuthClientsPagination;
}

/** Single scope from GET /api/v1/oauth-clients/scopes */
export interface OAuthScopeItem {
  name: string;
  description: string;
  category: string;
  requiresUserConsent: boolean;
}

/** Response shape: scopes grouped by category key */
export interface OAuthScopesApiResponse {
  scopes: Record<string, OAuthScopeItem[]>;
}

/** POST /api/v1/oauth-clients */
export interface CreateOAuthClientPayload {
  name: string;
  description?: string;
  redirectUris: string[];
  allowedGrantTypes: OAuthGrantTypeValue[];
  allowedScopes: string[];
  homepageUrl?: string;
  privacyPolicyUrl?: string;
  termsOfServiceUrl?: string;
  isConfidential: boolean;
}

/** POST /api/v1/oauth-clients ΓÇö response includes clientSecret only at creation time */
export interface CreateOAuthClientApiResponse {
  message?: string;
  app: OAuthClient & { clientSecret?: string };
}

/** PUT /api/v1/oauth-clients/:id */
export interface UpdateOAuthClientPayload {
  name: string;
  description?: string;
  redirectUris: string[];
  allowedGrantTypes: OAuthGrantTypeValue[];
  allowedScopes: string[];
  accessTokenLifetime: number;
  refreshTokenLifetime: number;
  homepageUrl?: string;
  privacyPolicyUrl?: string;
  termsOfServiceUrl?: string;
  isConfidential?: boolean;
}

/** POST /api/v1/oauth-clients/:id/regenerate-secret */
export interface RegenerateOAuthClientSecretResponse {
  message: string;
  clientId: string;
  clientSecret: string;
}
