import type { McpAuthMode } from '@/app/(main)/workspace/mcp-servers/types';

/** A stream the server ended with RUN_ERROR; `code` and `details` say what the thread can offer. */
export class ChatStreamError extends Error {
  constructor(
    message: string,
    readonly code?: string,
    readonly details?: unknown
  ) {
    super(message);
    this.name = 'ChatStreamError';
  }
}

/** Kept on a failed reply's metadata so the thread can render an action for it. */
export interface ChatStreamErrorInfo {
  code: string;
  details?: unknown;
}

export function streamErrorInfo(error: unknown): ChatStreamErrorInfo | undefined {
  return error instanceof ChatStreamError && error.code ? { code: error.code, details: error.details } : undefined;
}

export const MCP_SERVER_CONFIG_MISSING = 'mcp_server_config_missing';

export interface McpBlockedServer {
  instanceId: string;
  name: string;
  problem: 'not_found' | 'not_connected';
  authMode?: McpAuthMode;
  /** Everyone shares one credential, which only an administrator can enter. */
  sharedCredential: boolean;
}

export interface McpConfigMissingDetails {
  agentId?: string;
  /** The agent runs with its own credentials, which are set up in Agent Builder. */
  serviceAccount: boolean;
  servers: McpBlockedServer[];
}

const AUTH_MODES: ReadonlySet<string> = new Set<McpAuthMode>(['none', 'api_token', 'oauth', 'headers']);

function blockedServer(raw: unknown): McpBlockedServer | null {
  if (!raw || typeof raw !== 'object') return null;
  const s = raw as Record<string, unknown>;
  if (typeof s.instanceId !== 'string' || !s.instanceId) return null;
  if (s.problem !== 'not_found' && s.problem !== 'not_connected') return null;
  return {
    instanceId: s.instanceId,
    name: typeof s.name === 'string' && s.name ? s.name : s.instanceId,
    problem: s.problem,
    authMode: typeof s.authMode === 'string' && AUTH_MODES.has(s.authMode) ? (s.authMode as McpAuthMode) : undefined,
    sharedCredential: s.sharedCredential === true,
  };
}

/** The servers an agent chat was stopped on, or null when `info` is another error or malformed. */
export function mcpConfigMissingDetails(info: ChatStreamErrorInfo | undefined): McpConfigMissingDetails | null {
  if (info?.code !== MCP_SERVER_CONFIG_MISSING) return null;
  const details = info.details;
  if (!details || typeof details !== 'object') return null;
  const { agentId, serviceAccount, servers } = details as Record<string, unknown>;
  if (!Array.isArray(servers)) return null;
  const parsed = servers.map(blockedServer).filter((s): s is McpBlockedServer => s !== null);
  if (parsed.length === 0) return null;
  return {
    agentId: typeof agentId === 'string' && agentId ? agentId : undefined,
    serviceAccount: serviceAccount === true,
    servers: parsed,
  };
}
