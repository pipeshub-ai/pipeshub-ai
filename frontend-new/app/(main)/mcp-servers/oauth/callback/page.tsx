import { McpOAuthCallbackClient } from './mcp-oauth-callback-client';

/**
 * MCP server OAuth callback page.
 * Handles the redirect from the OAuth provider after user authorization.
 * Dynamic slug in the path (`:serverType`) is handled via `next.config.mjs` rewrites.
 */
export default function McpOAuthCallbackPage() {
  return <McpOAuthCallbackClient />;
}
