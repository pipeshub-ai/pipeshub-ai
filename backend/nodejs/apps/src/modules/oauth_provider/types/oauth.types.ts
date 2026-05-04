// Token Payload
export interface OAuthTokenPayload {
  userId: string
  orgId: string
  iss: string
  exp: number
  iat: number
  jti: string
  scope: string
  client_id: string
  tokenType: 'oauth'
  isRefreshToken?: true
  fullName?: string
  accountType?: string
  createdBy?: string
}

// Generated Tokens Response
export interface GeneratedTokens {
  accessToken: string
  refreshToken?: string
  tokenType: string
  expiresIn: number
  scope: string
  idToken?: string
}

// Token Response (RFC 6749 compliant)
export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
  refresh_token?: string
  scope: string
  id_token?: string
}

// Authorization Request Parameters
export interface AuthorizeRequest {
  response_type: 'code'
  client_id: string
  redirect_uri: string
  scope: string
  state: string
  code_challenge?: string
  code_challenge_method?: 'S256' | 'plain'
  nonce?: string
}

// Token Request Parameters
export interface TokenRequest {
  grant_type: 'authorization_code' | 'client_credentials' | 'refresh_token'
  code?: string
  redirect_uri?: string
  client_id: string
  client_secret?: string
  refresh_token?: string
  scope?: string
  code_verifier?: string
}

// Revoke Request
export interface RevokeRequest {
  token: string
  token_type_hint?: 'access_token' | 'refresh_token'
  client_id: string
  client_secret?: string
}

// Introspect Request
export interface IntrospectRequest {
  token: string
  token_type_hint?: 'access_token' | 'refresh_token'
  client_id: string
  client_secret?: string
}

// Introspect Response (RFC 7662)
export interface IntrospectResponse {
  active: boolean
  scope?: string
  client_id?: string
  username?: string
  token_type?: string
  exp?: number
  iat?: number
  nbf?: number
  user_id?: string
  iss?: string
  jti?: string
}

// UserInfo Response (OIDC)
export interface UserInfoResponse {
  user_id: string
  name?: string
  given_name?: string
  family_name?: string
  email?: string
  email_verified?: boolean
  picture?: string
  updated_at?: number
}

// Create OAuth App Request
export interface CreateOAuthAppRequest {
  name: string
  description?: string
  redirectUris?: string[]
  allowedGrantTypes?: string[]
  allowedScopes: string[]
  homepageUrl?: string
  privacyPolicyUrl?: string
  termsOfServiceUrl?: string
  isConfidential?: boolean
  accessTokenLifetime?: number
  refreshTokenLifetime?: number
}

// Update OAuth App Request
export interface UpdateOAuthAppRequest {
  name?: string
  description?: string
  redirectUris?: string[]
  allowedGrantTypes?: string[]
  allowedScopes?: string[]
  homepageUrl?: string | null
  privacyPolicyUrl?: string | null
  termsOfServiceUrl?: string | null
  accessTokenLifetime?: number
  refreshTokenLifetime?: number
}

// OAuth App Response (public data without secret)
export interface OAuthAppResponse {
  id: string
  slug: string
  clientId: string
  name: string
  description?: string
  redirectUris: string[]
  allowedGrantTypes: string[]
  allowedScopes: string[]
  status: string
  logoUrl?: string
  homepageUrl?: string
  privacyPolicyUrl?: string
  termsOfServiceUrl?: string
  isConfidential: boolean
  accessTokenLifetime: number
  refreshTokenLifetime: number
  /** 'admin' for manually created apps, 'dcr' for self-registered (RFC 7591). */
  registeredVia: string
  /** Timestamp of the most recent successful /authorize. Bumped on every consent. */
  lastAuthorizedAt?: Date
  createdAt: Date
  updatedAt: Date
}

// OAuth App with Secret (returned only on creation/regeneration)
export interface OAuthAppWithSecret extends OAuthAppResponse {
  clientSecret: string
}

// Consent Data (for authorization page)
export interface ConsentData {
  app: {
    name: string
    description?: string
    logoUrl?: string
    homepageUrl?: string
    privacyPolicyUrl?: string
  }
  scopes: Array<{
    name: string
    description: string
    category: string
  }>
  user: {
    email: string
    name?: string
  }
  redirectUri: string
  state: string
}

// Auth Code Exchange Result
export interface AuthCodeExchangeResult {
  userId: string
  orgId: string
  scopes: string[]
}

// OAuth Error Response (RFC 6749)
export interface OAuthErrorResponse {
  error: string
  error_description?: string
  error_uri?: string
  state?: string
}

// OAuth Protected Resource Metadata (RFC 9728)
export interface OAuthProtectedResourceMetadata {
  resource: string
  authorization_servers: string[]
  scopes_supported: string[]
  bearer_methods_supported: string[]
  resource_documentation?: string
}

// OIDC Discovery Response
export interface OpenIDConfiguration {
  issuer: string
  authorization_endpoint: string
  token_endpoint: string
  userinfo_endpoint: string
  revocation_endpoint: string
  introspection_endpoint: string
  /** RFC 7591 §3 dynamic client registration endpoint. */
  registration_endpoint?: string
  jwks_uri: string
  scopes_supported: string[]
  response_types_supported: string[]
  grant_types_supported: string[]
  token_endpoint_auth_methods_supported: string[]
  subject_types_supported: string[]
  id_token_signing_alg_values_supported: string[]
  claims_supported: string[]
  code_challenge_methods_supported: string[]
}

// List Apps Query
export interface ListAppsQuery {
  page?: number
  limit?: number
  status?: string
  search?: string
}

// Paginated Response
export interface PaginatedResponse<T> {
  data: T[]
  pagination: {
    page: number
    limit: number
    total: number
    totalPages: number
  }
}

// Token List Item
export interface TokenListItem {
  id: string
  tokenType: 'access' | 'refresh'
  userId?: string
  scopes: string[]
  createdAt: Date
  expiresAt: Date
  isRevoked: boolean
}

// Request with OAuth user info
export interface OAuthAuthenticatedRequest {
  oauthClient?: {
    clientId: string
    orgId: string
    scopes: string[]
  }
  oauthUser?: {
    userId: string
    orgId: string
    email: string
  }
}

// JWK (JSON Web Key) - RFC 7517
export interface JWK {
  kty: 'RSA'
  use: 'sig'
  alg: 'RS256'
  kid: string
  n: string
  e: string
}

// JWKS (JSON Web Key Set)
export interface JWKS {
  keys: JWK[]
}

// ===========================================================================
// Dynamic Client Registration (RFC 7591) + Management (RFC 7592)
// All fields use snake_case to match the wire format.
// ===========================================================================

/**
 * RFC 7591 §2 Client Metadata. Sent on POST /register and PUT /register/:client_id.
 * Most fields are optional — the server fills in defaults.
 */
export interface ClientRegistrationRequest {
  client_name?: string
  redirect_uris?: string[]
  /** Default: ['authorization_code', 'refresh_token']. */
  grant_types?: string[]
  /** Default: ['code'] when authorization_code is in grant_types. */
  response_types?: string[]
  /** Default: 'client_secret_basic' for confidential, 'none' for public. */
  token_endpoint_auth_method?: 'none' | 'client_secret_basic' | 'client_secret_post'
  /** Space-separated list of requested scopes (RFC 6749 §3.3). */
  scope?: string
  client_uri?: string
  logo_uri?: string
  policy_uri?: string
  tos_uri?: string
  contacts?: string[]
  software_id?: string
  software_version?: string
  /** RFC 7591: 'native' for desktop/mobile/CLI/loopback; 'web' for hosted browser apps. */
  application_type?: 'native' | 'web'
}

/**
 * RFC 7591 §3.2.1 successful registration response.
 * Echoes the registered metadata plus server-issued credentials.
 */
export interface ClientRegistrationResponse extends ClientRegistrationRequest {
  client_id: string
  /** Omitted entirely when token_endpoint_auth_method = 'none'. */
  client_secret?: string
  /** Unix seconds when client_id was issued. */
  client_id_issued_at: number
  /** Unix seconds when client_secret expires; 0 = never. Omitted with no secret. */
  client_secret_expires_at?: number
  /** RFC 7592: bearer credential for GET/PUT/DELETE /register/:client_id. */
  registration_access_token?: string
  /** RFC 7592: absolute URL the client uses for self-management. */
  registration_client_uri?: string
  /** Free-form server tag, e.g. 'mcp.dcr.public'. */
  client_profile?: string
}

/**
 * RFC 7591 §3.2.2 error response. Returned for invalid_redirect_uri,
 * invalid_client_metadata, invalid_software_statement, etc.
 */
export interface ClientRegistrationErrorResponse {
  error:
    | 'invalid_redirect_uri'
    | 'invalid_client_metadata'
    | 'invalid_software_statement'
    | 'unapproved_software_statement'
    | 'access_denied'
  error_description?: string
}
