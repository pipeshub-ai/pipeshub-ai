// ========================================
// MCP Servers types — mirrors backend/python/app/agents/mcp/models.py and
// backend/python/app/api/routes/mcp_servers.py response shapes 1:1 (camelCase)
// so the frontend never has to reshape payloads.
// ========================================

export type McpTransport = 'stdio' | 'sse' | 'streamable_http';

export type McpAuthMode = 'none' | 'api_token' | 'oauth' | 'headers';

export interface McpAuthHint {
  label: string;
  placeholder?: string | null;
  helpText?: string | null;
}

/** A catalog entry — pure metadata, never carries credentials. */
export interface McpServerTemplate {
  typeId: string;
  displayName: string;
  description: string;
  icon?: string | null;
  transport: McpTransport;
  defaultAuthMode: McpAuthMode;
  supportedAuthModes: McpAuthMode[];

  // STDIO
  command?: string | null;
  args: string[];
  requiredEnv: string[];
  optionalEnv: string[];

  // SSE / Streamable HTTP
  defaultUrl?: string | null;

  // OAuth
  authorizationUrl?: string | null;
  tokenUrl?: string | null;
  defaultScopes: string[];
  supportsDcr: boolean;

  documentationUrl?: string | null;
  authHint?: McpAuthHint | null;
  tags: string[];
}

/** Admin-facing request body for creating/updating an instance. */
export interface McpServerInstancePayload {
  name: string;
  typeId?: string | null;
  transport: McpTransport;
  authMode: McpAuthMode;
  useAdminAuth?: boolean;
  description?: string | null;

  // STDIO — env is an explicit allowlist mapping (never arbitrary passthrough)
  command?: string | null;
  args?: string[];
  /** Custom STDIO only — env var names the process expects (e.g. API_KEY). Catalog templates supply this. */
  requiredEnv?: string[];
  env?: Record<string, string>;

  // SSE / Streamable HTTP
  url?: string | null;
  headerName?: string | null;

  // OAuth (custom servers only — catalog templates already have these)
  authorizationUrl?: string | null;
  tokenUrl?: string | null;
  scopes?: string[];
}

/** Persisted instance metadata — no secrets. */
export interface McpServerInstance {
  _id: string;
  orgId: string;
  createdBy: string;
  name: string;
  typeId?: string | null;
  transport: McpTransport;
  authMode: McpAuthMode;
  useAdminAuth: boolean;
  description?: string | null;

  command?: string | null;
  args: string[];
  requiredEnv: string[];
  optionalEnv: string[];

  url?: string | null;
  headerName?: string | null;

  authorizationUrl?: string | null;
  tokenUrl?: string | null;
  scopes: string[];

  isCustom: boolean;
  createdAt: number;
  updatedAt: number;

  /** Set on list/get responses only. */
  hasOAuthClientConfig?: boolean;
}

/** A single tool discovered from a connected MCP server. */
export interface McpToolInfo {
  name: string;
  namespacedName: string;
  description?: string | null;
  inputSchema: Record<string, unknown>;
}

/** `GET /my-mcp-servers` entry — instance + the caller's effective auth/tool state. */
export interface McpMyServerEntry extends McpServerInstance {
  isAuthenticated: boolean;
  tools: McpToolInfo[];
  toolsError?: string | null;
}

export interface McpCatalogResponse {
  templates: McpServerTemplate[];
  total: number;
  page: number;
  limit: number;
}

export interface McpInstancesResponse {
  instances: McpServerInstance[];
}

export interface McpMyServersResponse {
  instances: McpMyServerEntry[];
}

export interface McpToolsResponse {
  tools: McpToolInfo[];
}

export interface McpAuthenticatePayload {
  apiToken?: string;
  headerName?: string;
  headerValue?: string;
  /** STDIO multi-env credentials, allowlisted against instance requiredEnv/optionalEnv. */
  env?: Record<string, string>;
}

export interface McpOAuthConfigPayload {
  clientId: string;
  clientSecret: string;
}

export interface McpOAuthConfigResponse {
  configured: boolean;
  clientId?: string;
  clientSecret?: string;
}

export interface McpOAuthAuthorizationUrlResponse {
  authorizationUrl: string;
}

export interface McpOAuthCallbackResponse {
  success: boolean;
  error?: string;
  errorMessage?: string;
  /** Set on success — lets the opener refresh/target the specific instance that was authorized. */
  instanceId?: string | null;
}

/** `POST /oauth/discover` response — live RFC 9728/8414 probe result for a server URL. */
export interface McpOAuthDiscoveryResult {
  /** False also means "couldn't tell" (network error/timeout) — check `metadataFound`. */
  supportsDcr: boolean;
  /** False = discovery found nothing at all; treat DCR support as unknown, not "no". */
  metadataFound: boolean;
  authorizationEndpoint?: string | null;
  tokenEndpoint?: string | null;
  registrationEndpoint?: string | null;
  scopesSupported: string[];
}

export interface McpSuccessResponse {
  success: boolean;
  isAuthenticated?: boolean;
}

// ── UI-only state ──

/** null = create mode, string = edit mode (instance id) */
export type EditingMcpInstanceTarget = string | null;

export const MCP_TRANSPORT_LABELS: Record<McpTransport, string> = {
  stdio: 'STDIO (command)',
  sse: 'SSE',
  streamable_http: 'Streamable HTTP',
};

export const MCP_AUTH_MODE_LABELS: Record<McpAuthMode, string> = {
  none: 'No authentication',
  api_token: 'API token',
  oauth: 'OAuth 2.0',
  headers: 'Custom header',
};
