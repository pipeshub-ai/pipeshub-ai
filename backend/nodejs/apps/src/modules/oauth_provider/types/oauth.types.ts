/** What a connected app (an OAuth app or a personal access token) may see,
 * as its user picked it: whole apps and collections, record groups, and
 * folders or records with everything under them. The Python services bound
 * every search and every record reached by id to it. */
export interface KnowledgeLimit {
  apps: string[];
  recordGroups: string[];
  records: string[];
}

// Token Payload
export interface OAuthTokenPayload {
  userId: string;
  orgId: string;
  iss: string;
  exp: number;
  iat: number;
  jti: string;
  scope: string;
  client_id: string;
  tokenType: 'oauth';
  isRefreshToken?: true;
  fullName?: string;
  accountType?: string;
  createdBy?: string;
  /**
   * RFC 8693 section 4.1: the party acting on behalf of `userId`.
   *
   * Present only on a delegated token, where it names the service account
   * that obtained it. Nothing authorises on this claim — the token speaks as
   * `userId` and is checked as that person throughout — but it is what lets a
   * log, an introspection response or an incident review say who was really
   * calling.
   */
  act?: { sub: string };
  /** Absent when the app may see everything its user can. */
  knowledgeLimit?: KnowledgeLimit;
}

// Generated Tokens Response
export interface GeneratedTokens {
  accessToken: string;
  /** The access token's `jti`, which is what identifies it in later logs. */
  jti: string;
  /** OAuthAccessToken document id, e.g. for a PAT list/revoke UI to
   * reference this token without ever seeing it again. */
  accessTokenId: string;
  refreshToken?: string;
  tokenType: string;
  expiresIn: number;
  scope: string;
  idToken?: string;
}

// Per-call overrides for GeneratedTokens.accessToken minting, used by
// personal access tokens: a fixed lifetime and user-given label rather
// than the issuing app's own accessTokenLifetime.
export interface GenerateTokensOptions {
  accessTokenLifetimeOverrideSeconds?: number;
  name?: string;
  // User grants only: PAT and service pseudo-apps store allowedScopes once
  // and validate against MCP_SCOPES instead.
  recheckAppScopes?: boolean;
  /**
   * Mint this as a delegated token: `userId` keeps speaking as the person,
   * while this names the service account acting as them. See the `act` claim
   * on {@link OAuthTokenPayload}.
   */
  actorUserId?: string;
  knowledgeLimit?: KnowledgeLimit;
}

// Token Response (RFC 6749 compliant)
export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  refresh_token?: string;
  scope: string;
  id_token?: string;
  /** RFC 8693 section 2.2.1: required on a token-exchange response. */
  issued_token_type?: string;
}

// Authorization Request Parameters
export interface AuthorizeRequest {
  response_type: 'code';
  client_id: string;
  redirect_uri: string;
  scope: string;
  state: string;
  code_challenge?: string;
  code_challenge_method?: 'S256';
  nonce?: string;
}

// Token Request Parameters
export interface TokenRequest {
  grant_type:
    | 'authorization_code'
    | 'client_credentials'
    | 'refresh_token'
    | 'urn:ietf:params:oauth:grant-type:device_code'
    | 'urn:ietf:params:oauth:grant-type:token-exchange';
  code?: string;
  redirect_uri?: string;
  client_id: string;
  client_secret?: string;
  refresh_token?: string;
  scope?: string;
  code_verifier?: string;
  device_code?: string;
  /** RFC 8693: the credential being exchanged — here, a service token. */
  subject_token?: string;
  subject_token_type?: string;
  /** The person the caller wants to act as, by user id or email address. */
  requested_subject?: string;
}

// Revoke Request
export interface RevokeRequest {
  token: string;
  token_type_hint?: 'access_token' | 'refresh_token';
  client_id: string;
  client_secret?: string;
}

// Introspect Request
export interface IntrospectRequest {
  token: string;
  token_type_hint?: 'access_token' | 'refresh_token';
  client_id: string;
  client_secret?: string;
}

// Introspect Response (RFC 7662)
export interface IntrospectResponse {
  active: boolean;
  scope?: string;
  client_id?: string;
  username?: string;
  token_type?: string;
  exp?: number;
  iat?: number;
  nbf?: number;
  user_id?: string;
  iss?: string;
  jti?: string;
}

// UserInfo Response (OIDC)
export interface UserInfoResponse {
  user_id: string;
  name?: string;
  given_name?: string;
  family_name?: string;
  email?: string;
  email_verified?: boolean;
  picture?: string;
  updated_at?: number;
}

// Create OAuth App Request
export interface CreateOAuthAppRequest {
  name: string;
  description?: string;
  redirectUris?: string[];
  allowedGrantTypes?: string[];
  allowedScopes: string[];
  homepageUrl?: string;
  privacyPolicyUrl?: string;
  termsOfServiceUrl?: string;
  isConfidential?: boolean;
  accessTokenLifetime?: number;
  refreshTokenLifetime?: number;
  knowledgeLimit?: KnowledgeLimit;
  /**
   * The service account this app's own tokens act as.
   *
   * Required when the app allows `client_credentials`, and ignored otherwise.
   * That grant has no person in it, so without this the app acts as whoever
   * created it — and an app created by an administrator would carry
   * administrator rights for as long as it exists.
   */
  tokenIdentityUserId?: string;
}

// Update OAuth App Request
export interface UpdateOAuthAppRequest {
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
  knowledgeLimit?: KnowledgeLimit | null;
}

// OAuth App Response (public data without secret)
export interface OAuthAppResponse {
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
  knowledgeLimit?: KnowledgeLimit;
  /**
   * The service account this app's own `client_credentials` tokens act as.
   *
   * Absent means the app has no service account and its tokens act as whoever
   * created it. Apps created since that became a requirement always have one;
   * an app registered before it may not, and those are the ones an
   * administrator still has to move.
   */
  tokenIdentityUserId?: string;
  createdAt: Date;
  updatedAt: Date;
}

// OAuth App with Secret (returned only on creation/regeneration)
export interface OAuthAppWithSecret extends OAuthAppResponse {
  clientSecret: string;
}

// Consent Data (for authorization page)
export interface ConsentData {
  app: {
    name: string;
    description?: string;
    logoUrl?: string;
    homepageUrl?: string;
    privacyPolicyUrl?: string;
    isDynamic?: boolean;
    /** What the app's creator let it see; absent for everything the user can. */
    knowledgeLimit?: KnowledgeLimit;
  };
  scopes: Array<{
    name: string;
    description: string;
    category: string;
  }>;
  // Requested but outside the app's allowed scopes; shown, never granted.
  notGrantedScopes?: Array<{
    name: string;
    description: string;
    category: string;
  }>;
  user: {
    email: string;
    name?: string;
  };
  workspace?: {
    name: string;
  };
  redirectUri: string;
  state: string;
}

// Auth Code Exchange Result
export interface AuthCodeExchangeResult {
  userId: string;
  orgId: string;
  scopes: string[];
  knowledgeLimit?: KnowledgeLimit;
}

// OAuth Error Response (RFC 6749)
export interface OAuthErrorResponse {
  error: string;
  error_description?: string;
  error_uri?: string;
  state?: string;
}

// OAuth Protected Resource Metadata (RFC 9728)
export interface OAuthProtectedResourceMetadata {
  resource: string;
  authorization_servers: string[];
  scopes_supported: string[];
  bearer_methods_supported: string[];
  resource_documentation?: string;
  /** PipesHub first-party device client. Present when device grant is on and the instance has an org. */
  pipeshub_device_client_id?: string;
}

// OIDC Discovery Response
export interface OpenIDConfiguration {
  issuer: string;
  authorization_endpoint: string;
  token_endpoint: string;
  userinfo_endpoint: string;
  revocation_endpoint: string;
  introspection_endpoint: string;
  jwks_uri: string;
  registration_endpoint?: string;
  device_authorization_endpoint?: string;
  /** PipesHub first-party device client. Present when device grant is on and the instance has an org. */
  pipeshub_device_client_id?: string;
  scopes_supported: string[];
  response_types_supported: string[];
  grant_types_supported: string[];
  token_endpoint_auth_methods_supported: string[];
  subject_types_supported: string[];
  id_token_signing_alg_values_supported: string[];
  claims_supported: string[];
  code_challenge_methods_supported: string[];
}

// List Apps Query
export interface ListAppsQuery {
  page?: number;
  limit?: number;
  status?: string;
  search?: string;
}

// Paginated Response
export interface PaginatedResponse<T> {
  data: T[];
  pagination: {
    page: number;
    limit: number;
    total: number;
    totalPages: number;
  };
}

// Token List Item
export interface TokenListItem {
  id: string;
  tokenType: 'access' | 'refresh';
  userId?: string;
  scopes: string[];
  createdAt: Date;
  expiresAt: Date;
  isRevoked: boolean;
  name?: string;
  lastUsedAt?: Date;
  /** Absent when the token may see everything its user can. */
  knowledgeLimit?: KnowledgeLimit;
}

// Personal Access Tokens
export interface CreatePatRequest {
  name: string;
  scopes?: string[];
  /** Days until expiry, or 'never' for a non-expiring token (stored as a
   * far-future expiresAt — the schema's expiresAt is required). */
  expiryDays?: 30 | 90 | 365 | 'never';
  /** What the token may see; omit for everything the user can. */
  knowledgeLimit?: KnowledgeLimit;
}

export interface PatWithSecret {
  id: string;
  name: string;
  scopes: string[];
  createdAt: Date;
  expiresAt: Date;
  accessToken: string;
  knowledgeLimit?: KnowledgeLimit;
}

export interface PatListItem {
  id: string;
  name: string;
  scopes: string[];
  createdAt: Date;
  expiresAt: Date;
  lastUsedAt?: Date;
  knowledgeLimit?: KnowledgeLimit;
}

/**
 * A PAT as seen by an org admin — includes who it belongs to, since an
 * admin is looking across every member's tokens rather than just their
 * own (see `PatService.listAllTokens` / `GET /personal-access-tokens/admin`).
 */
export interface AdminPatListItem extends PatListItem {
  userId: string;
  ownerEmail?: string;
  ownerFullName?: string;
  /** True if the owning user has been deleted (or no longer resolves at
   * all) — the token still needs to be visible/revocable for cleanup,
   * even though there's no live account behind it. */
  ownerDeleted: boolean;
}

// Request with OAuth user info
export interface OAuthAuthenticatedRequest {
  oauthClient?: {
    clientId: string;
    orgId: string;
    scopes: string[];
  };
  oauthUser?: {
    userId: string;
    orgId: string;
    email: string;
  };
}

// JWK (JSON Web Key) - RFC 7517
export interface JWK {
  kty: 'RSA';
  use: 'sig';
  alg: 'RS256';
  kid: string;
  n: string;
  e: string;
}

// JWKS (JSON Web Key Set)
export interface JWKS {
  keys: JWK[];
}
