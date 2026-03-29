export enum McpAuthType {
  OAUTH = 'oauth',
  BEARER_TOKEN = 'bearer_token',
}

export enum ClientRegistrationMethod {
  PRE_REGISTERED = 'pre_registered',
  CLIENT_ID_METADATA_DOCUMENT = 'client_id_metadata_document',
  DCR = 'dcr',
  BEARER_TOKEN = 'bearer_token',
}

export interface ProtectedResourceMetadata {
  resource: string;
  authorization_servers?: string[];
  scopes_supported?: string[];
  bearer_methods_supported?: string[];
  resource_signing_alg_values_supported?: string[];
}

export interface AuthServerMetadata {
  issuer: string;
  authorization_endpoint: string;
  token_endpoint: string;
  registration_endpoint?: string;
  code_challenge_methods_supported?: string[];
  client_id_metadata_document_supported?: boolean;
  scopes_supported?: string[];
  response_types_supported?: string[];
  grant_types_supported?: string[];
  token_endpoint_auth_methods_supported?: string[];
}

export interface DcrResponse {
  client_id: string;
  client_secret?: string;
  client_secret_expires_at?: number;
  client_id_issued_at?: number;
  registration_access_token?: string;
  registration_client_uri?: string;
}

export interface OAuthFlowState {
  mcpServerUrl: string;
  codeVerifier: string;
  redirectUri: string;
  scope?: string;
  orgId: string;
  userId: string;
  clientId: string;
  clientSecret?: string;
  registrationMethod: ClientRegistrationMethod;
  tokenEndpoint: string;
  resource: string;
  createdAt: number;
}

export interface StoredTokenData {
  authType: McpAuthType;
  accessToken: string; // encrypted
  refreshToken?: string; // encrypted
  tokenType: string;
  scope?: string;
  expiresAt?: number; // Unix timestamp in ms
  mcpServerUrl: string;
  clientId?: string;
  clientSecret?: string; // encrypted
  tokenEndpoint?: string;
  resource?: string;
  registrationMethod: ClientRegistrationMethod;
  createdAt: number;
  updatedAt: number;
}

export interface StartAuthRequest {
  mcpServerUrl: string;
  scope?: string;
  clientId?: string;
  clientSecret?: string;
  frontendBaseUrl: string;
}

export interface StartAuthResponse {
  authorizationUrl: string;
  state: string;
}

export interface ConnectBearerRequest {
  mcpServerUrl: string;
  bearerToken: string;
}

export interface TokenResponse {
  accessToken: string;
  tokenType: string;
  expiresAt?: number;
  scope?: string;
  mcpServerUrl: string;
  authType: McpAuthType;
}

export interface McpConnectionInfo {
  mcpServerUrl: string;
  authType: McpAuthType;
  scope?: string;
  expiresAt?: number;
  createdAt: number;
  registrationMethod: ClientRegistrationMethod;
}

export interface ClientMetadataDocument {
  client_id: string;
  client_name: string;
  redirect_uris: string[];
  grant_types: string[];
  response_types: string[];
  token_endpoint_auth_method: string;
  scope?: string;
}

export interface PkceChallenge {
  codeVerifier: string;
  codeChallenge: string;
}

export interface WwwAuthenticateParams {
  resource_metadata?: string;
  scope?: string;
  realm?: string;
  error?: string;
  error_description?: string;
}
