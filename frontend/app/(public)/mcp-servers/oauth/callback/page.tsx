import { McpOAuthCallbackClient } from './mcp-oauth-callback-client';

/**
 * MCP OAuth callback page — the fixed `redirect_uri` the backend builds for every MCP
 * instance (`{baseUrl}/mcp-servers/oauth/callback/`). State is server-side and instance-
 * agnostic, so unlike toolsets/connectors this route needs no per-slug path segment.
 */
export default function McpOAuthCallbackPage() {
  return <McpOAuthCallbackClient />;
}
