export const TokenScopes = Object.freeze({
  SEND_MAIL: 'mail:send',
  FETCH_CONFIG: 'fetch:config',
  PASSWORD_RESET: 'password:reset',
  USER_LOOKUP: 'user:lookup',
  TOKEN_REFRESH: 'token:refresh',
  STORAGE_TOKEN: 'storage:token',
  CONVERSATION_CREATE: 'conversation:create',
  CONVERSATION_PERMISSIONS: 'conversation:permissions',
  CONVERSATION_ERASE: 'conversation:erase',
  CONVERSATION_FORK: 'conversation:fork',
  // The agent-erasure sweeper asking the query service whether an agent's delete committed (H5).
  AGENT_ERASE: 'agent:erase',
  // The usage report and budget notices asking the query service for one org's agent names (WP-51).
  AGENT_NAMES: 'agent:names',
  VALIDATE_EMAIL: 'email:validate',
  ENTITY_USER_WRITE: 'entity:user:write',
  ORG_EMAIL_VERIFY: 'org:email:verify',
  EMAIL_VERIFIED: 'email:verified',
  DESKTOP_COMMAND: 'desktop:command',
  // Held only by the Python services, on their caller-role lookups, so the
  // global rate limiter can tell those from client traffic (rate-limit.middleware).
  CALLER_ROLE: 'caller:role',
  // Held only by the /mcp handler, on the API calls it makes for a tool call, so
  // they are not counted again and an MCP-audience token is accepted on them
  // (libs/middlewares/mcp-relay.ts). Never sent as an Authorization token.
  MCP_RELAY: 'mcp:relay',
  // Slack bot process only. Narrower than FETCH_CONFIG because the route it
  // guards returns Slack workspace credentials for every org.
  SLACK_BOT_VERIFY: 'slack-bot:verify',
  // The Python services' binding writes (CPD-8): the credential migration,
  // legacy credential routes with CONTROL_PLANE_CREDENTIALS on, the token refresher.
  CONTROL_PLANE_BINDINGS: 'control-plane:bindings',
  // Node -> query: one org's legacy capability facts for the control plane's
  // migration and sync (CPD-7); the org is the token's.
  CAPABILITIES_READ: 'capabilities:read',
  // Node -> query: a user's teams and roles in the token's org (CPD-11's MembershipPort).
  DIRECTORY_READ: 'directory:read',
  // Query -> Node: "this org's agent or instances changed, sync them" (CPD-7's
  // dual write). Never accepted by the Python services.
  CAPABILITIES_SYNC: 'capabilities:sync',
  // Node user and org deletion -> connectors: erase the principal's personal
  // toolset and MCP instances (C-14); the org is the token's.
  PRINCIPAL_ERASE: 'principal:erase',
} as const);

// Create a type for the TokenScopes keys
export type TokenScopes = (typeof TokenScopes)[keyof typeof TokenScopes];

// Scopes on tokens held by end users; these are signed with the derived
// user-action key (libs/utils/jwtKeys.ts), never the raw scoped secret.
export const USER_ACTION_TOKEN_SCOPES: ReadonlySet<TokenScopes> =
  new Set<TokenScopes>([
    TokenScopes.PASSWORD_RESET,
    TokenScopes.VALIDATE_EMAIL,
    TokenScopes.TOKEN_REFRESH,
    TokenScopes.ORG_EMAIL_VERIFY,
    TokenScopes.EMAIL_VERIFIED,
  ]);

export const isUserActionScope = (scope: string): boolean =>
  (USER_ACTION_TOKEN_SCOPES as ReadonlySet<string>).has(scope);
