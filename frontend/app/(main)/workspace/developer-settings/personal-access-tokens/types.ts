/**
 * Personal access tokens (developer settings).
 * List item shape from GET /api/v1/personal-access-tokens.
 */
export interface PatListItem {
  id: string;
  name: string;
  scopes: string[];
  createdAt: string;
  expiresAt: string;
  lastUsedAt?: string;
}

export interface PatListApiResponse {
  tokens: PatListItem[];
}

/** Single scope from GET /api/v1/personal-access-tokens/scopes */
export interface PatScopeItem {
  name: string;
  description: string;
  category: string;
  requiresUserConsent: boolean;
}

export interface PatScopesApiResponse {
  scopes: PatScopeItem[];
}

export type PatExpiryOption = 30 | 90 | 365 | 'never';

/** POST /api/v1/personal-access-tokens */
export interface CreatePatPayload {
  name: string;
  scopes?: string[];
  expiryDays?: PatExpiryOption;
}

/** POST /api/v1/personal-access-tokens — response carries the raw token
 * exactly once; it's never retrievable again after this response. */
export interface CreatePatApiResponse {
  message?: string;
  token: PatListItem & { accessToken: string };
}
