import type { McpSignInServer, MessagePart } from './types';

/**
 * The servers the reply's sign-in card lists (Python `mcp_sign_in.py`): from its `mcp_sign_in`
 * part, with anything not shaped like a server dropped.
 */
export function mcpSignInServers(parts: MessagePart[] | undefined): McpSignInServer[] {
  const part = parts?.find((p) => p.type === 'mcp_sign_in');
  if (!part || !Array.isArray(part.servers)) return [];
  const servers: McpSignInServer[] = [];
  for (const value of part.servers as unknown[]) {
    if (!value || typeof value !== 'object') continue;
    const raw = value as Record<string, unknown>;
    if (typeof raw.instanceId !== 'string' || !raw.instanceId || typeof raw.serverName !== 'string') continue;
    const scopes = Array.isArray(raw.scopes) ? raw.scopes.filter((s): s is string => typeof s === 'string' && s !== '') : [];
    servers.push({
      instanceId: raw.instanceId,
      serverName: raw.serverName || raw.instanceId,
      scopes,
      ...(typeof raw.agentKey === 'string' && raw.agentKey ? { agentKey: raw.agentKey } : {}),
    });
  }
  return servers;
}
