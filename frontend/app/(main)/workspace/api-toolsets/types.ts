/** Shapes of the `/api/v1/api-toolsets` routes (camelCase JSON). */

export type ApiToolsetAuthType =
  | 'none'
  | 'api_key'
  | 'http_bearer'
  | 'http_basic'
  | 'oauth2_client_credentials'
  | 'oauth2_authorization_code';

export type ApiOperationKind = 'read' | 'write' | 'destructive';

export interface ApiAuthScheme {
  name: string;
  type: ApiToolsetAuthType;
  description: string;
  location?: string | null;
  paramName?: string | null;
  tokenUrl?: string | null;
  authorizationUrl?: string | null;
  scopes: Record<string, string>;
}

/** What every list shows of an operation. */
export interface ApiOperationSummary {
  toolName: string;
  method: string;
  path: string;
  summary: string;
  kind: ApiOperationKind;
  /** False when the spec lets anyone call it (`security: []`). */
  requiresAuth: boolean;
}

export interface ApiCompiledOperation extends ApiOperationSummary {
  operationId?: string | null;
  description: string;
  bodyRequired: boolean;
  inputSchema: Record<string, unknown>;
}

export interface ApiCompiledSpec {
  title: string;
  version: string;
  openapiVersion: string;
  description: string;
  servers: { url: string; description: string }[];
  authSchemes: ApiAuthScheme[];
  operations: ApiCompiledOperation[];
  warnings: string[];
  specHash: string;
}

export interface ApiToolset {
  id: string;
  name: string;
  description: string;
  specTitle: string;
  authType: ApiToolsetAuthType;
  useSharedCredential: boolean;
  isConnected: boolean;
  operations: ApiOperationSummary[];
  updatedAt: number;
}

/** The admin's view: how the API is reached and authenticated. Never secrets. */
export interface ApiToolsetConfig extends ApiToolset {
  baseUrl: string;
  authScheme: ApiAuthScheme;
  scopes: string[];
  timeoutSeconds: number;
  specVersion: string;
  openapiVersion: string;
  oauthClientId: string | null;
  hasOauthClientSecret: boolean;
  createdBy: string;
  createdAt: number;
}

export interface ApiToolsetPayload {
  name: string;
  description?: string;
  /** Left out on an edit that keeps the stored spec. */
  spec?: string;
  baseUrl: string;
  authSchemeName?: string;
  operations: string[];
  scopes?: string[];
  useSharedCredential?: boolean;
  timeoutSeconds?: number;
  oauthClientId?: string;
  /** Blank on an edit keeps the stored secret. */
  oauthClientSecret?: string;
}

/** Only the fields the toolset's auth type uses. */
export interface ApiToolsetCredentials {
  apiKey?: string;
  token?: string;
  username?: string;
  password?: string;
  clientId?: string;
  clientSecret?: string;
}

export interface ApiToolsetTestResult {
  ok: boolean;
  status: number;
  body: string;
  truncated: boolean;
}
