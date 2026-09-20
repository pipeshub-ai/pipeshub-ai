/**
 * Service accounts and the tokens they hold.
 *
 * A service account is a machine identity: automation authenticates as one so
 * that it reads with its own permissions rather than borrowing a colleague's.
 * Nobody signs in as one, and it is always a member, never an administrator.
 */

/** GET /api/v1/service-accounts */
export interface ServiceAccount {
  id: string;
  slug: string;
  fullName: string;
  email: string;
  description?: string;
  isDisabled: boolean;
  createdAt?: string;
  updatedAt?: string;
}

export interface ServiceAccountListApiResponse {
  serviceAccounts: ServiceAccount[];
}

/** POST /api/v1/service-accounts */
export interface CreateServiceAccountPayload {
  slug: string;
  fullName: string;
  description?: string;
}

/** PATCH /api/v1/service-accounts/:id */
export interface UpdateServiceAccountPayload {
  fullName?: string;
  description?: string;
  isDisabled?: boolean;
}

/** GET /api/v1/service-tokens?serviceAccountId= */
export interface ServiceToken {
  id: string;
  name: string;
  serviceAccountId: string;
  scopes: string[];
  createdAt: string;
  expiresAt: string;
  lastUsedAt?: string;
}

export interface ServiceTokenListApiResponse {
  tokens: ServiceToken[];
}

export interface ServiceTokenScopesApiResponse {
  scopes: string[];
}

/** POST /api/v1/service-tokens */
export interface CreateServiceTokenPayload {
  serviceAccountId: string;
  name: string;
  /** Required and non-empty: these never default to every scope. */
  scopes: string[];
  expiryDays?: number;
}

/**
 * The raw token is in this response and nowhere else, ever. Only its hash is
 * stored, so there is no second chance to read it.
 */
export interface CreateServiceTokenApiResponse {
  message?: string;
  token: ServiceToken & { accessToken: string };
}
