import type { KnowledgeLimit } from '@/app/components/knowledge-limit/knowledge-limit';

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
  /** What every token of the app may reach; absent for everything each user can see. */
  knowledgeLimit?: KnowledgeLimit;
  createdAt?: string;
  updatedAt?: string;
  /**
   * The service account this application's own tokens act as. Absent means
   * they act as whoever created it — the case the migration prompt is for.
   */
  tokenIdentityUserId?: string;
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
  knowledgeLimit?: KnowledgeLimit;
  /** Required when allowedGrantTypes includes client_credentials. */
  tokenIdentityUserId?: string;
}

/** POST /api/v1/oauth-clients — response includes clientSecret only at creation time */
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
  /** null lifts the limit. */
  knowledgeLimit: KnowledgeLimit | null;
}

/** POST /api/v1/oauth-clients/:id/regenerate-secret */
export interface RegenerateOAuthClientSecretResponse {
  message: string;
  clientId: string;
  clientSecret: string;
}
