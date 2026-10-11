// ========================================
// MCP Servers types — mirrors backend/python/app/agents/mcp/models.py and
// backend/python/app/api/routes/mcp_servers.py response shapes 1:1 (camelCase)
// so the frontend never has to reshape payloads.
// ========================================

export type McpTransport = 'stdio' | 'sse' | 'streamable_http';

export type McpAuthMode = 'none' | 'api_token' | 'oauth' | 'headers';

/** `custom_stdio_disabled`: a custom STDIO server and the operator has not set MCP_ALLOW_CUSTOM_STDIO=true. */
export type McpInstanceDisabledReason = 'custom_stdio_disabled';

export const MCP_CUSTOM_STDIO_FLAG = 'MCP_ALLOW_CUSTOM_STDIO';
/** `org`: an administrator's instance, visible to everyone. `personal`: one user's own. */
export type McpInstanceScope = 'org' | 'personal';

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
  /** The entry that took this one's place: no new servers from it, existing ones keep running. */
  replacedBy?: string | null;
}

/** Request body for creating/updating an instance. */
export interface McpServerInstancePayload {
  name: string;
  typeId?: string | null;
  /** Create only. Ignored for non-admins, whose instances are always personal. */
  scope?: McpInstanceScope;
  transport: McpTransport;
  authMode: McpAuthMode;
  useAdminAuth?: boolean;
  description?: string | null;

  // STDIO — credential values are supplied per user via /authenticate, never on the instance.
  command?: string | null;
  args?: string[];
  /** Custom STDIO only — env var names the process expects (e.g. API_KEY). Catalog templates supply this. */
  requiredEnv?: string[];

  // SSE / Streamable HTTP
  url?: string | null;
  headerName?: string | null;

  // OAuth (custom servers only — catalog templates already have these)
  authorizationUrl?: string | null;
  tokenUrl?: string | null;
  scopes?: string[];

  /** null = the deployment default (15 s to connect, 60 s per tool call). */
  connectTimeoutSeconds?: number | null;
  callTimeoutSeconds?: number | null;
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

  // How the server is reached (command, args, url, OAuth endpoints, scopes) is sent only to
  // administrators and, for a personal server, its owner; members get the rest.
  command?: string | null;
  args?: string[];
  requiredEnv: string[];
  optionalEnv: string[];

  url?: string | null;
  headerName?: string | null;

  authorizationUrl?: string | null;
  tokenUrl?: string | null;
  scopes?: string[];

  connectTimeoutSeconds?: number | null;
  callTimeoutSeconds?: number | null;

  isCustom: boolean;
  /** Absent on records saved before personal instances existed — those are org instances. */
  scope?: McpInstanceScope;
  createdAt: number;
  updatedAt: number;

  /** Set on list/get responses only. */
  hasOAuthClientConfig?: boolean;
  /** Set on list/get responses only: why this instance won't run on this deployment. */
  disabledReason?: McpInstanceDisabledReason | null;
}

/** What an administrator sees of a user's personal server (`listInstances({ includePersonal })`):
 * enough to recognise and delete it, never how to reach it. */
export type McpPersonalInstanceSummary = Pick<
  McpServerInstance,
  '_id' | 'orgId' | 'createdBy' | 'name' | 'typeId' | 'transport' | 'authMode' | 'isCustom' | 'createdAt' | 'updatedAt' | 'scope'
>;

/** A single tool discovered from a connected MCP server. */
export interface McpToolInfo {
  name: string;
  namespacedName: string;
  description?: string | null;
  inputSchema: Record<string, unknown>;
  /** The server's own hints (`readOnlyHint`, `destructiveHint`, …), as it sent them. */
  annotations?: Record<string, unknown> | null;
  /** What the tool does to data, which sets its starting approval rule (worked out by the backend). */
  kind?: McpToolKind;
  kindSource?: McpToolKindSource;
}

/** read → Pre-approved, write → Allow on approval, destructive → Deny, until someone sets a rule. */
export type McpToolKind = 'read' | 'write' | 'destructive';
/** Where a tool's kind came from: a deleting word in its name, the server's hints, or neither. */
export type McpToolKindSource = 'name' | 'server' | 'none';

/** `GET /my-mcp-servers` entry — instance + the caller's effective auth/tool state. */
export interface McpMyServerEntry extends McpServerInstance {
  isAuthenticated: boolean;
  tools: McpToolInfo[];
  toolsError?: string | null;
  /** The server missed the listing's time budget; chat still waits for it. */
  toolsTimedOut?: boolean;
  /** Why `toolsError` is set, so the page needn't match on its text. */
  toolsErrorCode?: McpToolsErrorCode | null;
  /** When the tools shown were discovered (epoch ms), if they came from the tool cache. */
  toolsCachedAt?: number | null;
  /** When the current sign-in was made (epoch ms); absent on records from before it was kept. */
  connectedAt?: number | null;
}

export type McpToolsErrorCode =
  | 'auth_expired'
  | 'unauthorized'
  | 'needs_permission'
  | 'blocked'
  | 'unreachable'
  | 'timeout'
  | 'error';

export interface McpCatalogResponse {
  templates: McpServerTemplate[];
  total: number;
  page: number;
  limit: number;
  /** Operator setting: whether custom servers may use the STDIO transport on this deployment. */
  customStdioAllowed: boolean;
}

export interface McpInstancesResponse {
  instances: McpServerInstance[];
}

export interface McpMyServersResponse {
  instances: McpMyServerEntry[];
}

export interface McpToolsResponse {
  tools: McpToolInfo[];
  /** When the list was read from the server (epoch ms). */
  syncedAt?: number;
}

/** Whether a tool call runs, asks the person in the chat first, or never runs. */
export type McpToolRule = 'allow' | 'ask' | 'block';

/** A person's or an agent's rules: tool name → rule. A tool without one uses its starting rule. */
export interface McpToolRules {
  tools: Record<string, McpToolRule>;
}

/** A company rule is a floor: nobody's own rule can be less strict. */
export interface McpCompanyToolRule {
  rule?: 'ask' | 'block' | null;
  /** In a run nobody can answer a card (Slack, the API), Ask becomes Allow. */
  unattended?: boolean;
}

export interface McpToolPolicy {
  tools: Record<string, McpCompanyToolRule>;
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
  /** The redirect URI the server sends to the provider; register the OAuth app with this.
   * Null when the server couldn't read its address just then. */
  redirectUri?: string | null;
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
  /** The redirect URI the server sends to the provider; register the OAuth app with this.
   * Null when the server couldn't read its address just then. */
  redirectUri?: string | null;
}

export interface McpSuccessResponse {
  success: boolean;
  isAuthenticated?: boolean;
}

/** Bounds the backend enforces (`MCPServerInstanceConfig`). */
export const MCP_TIMEOUT_LIMITS = {
  connect: { min: 1, max: 45 },
  call: { min: 1, max: 600 },
} as const;

export function isPersonalMcpInstance(instance: Pick<McpServerInstance, 'scope'> | null | undefined): boolean {
  return instance?.scope === 'personal';
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
