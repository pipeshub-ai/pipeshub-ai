'use client';

import { McpOAuthCallbackClient } from '@/app/(public)/mcp-servers/oauth/callback/mcp-oauth-callback-client';
import { ApiToolsetsApi } from '@/app/(main)/workspace/api-toolsets/api';

/**
 * API toolset OAuth callback: the fixed redirect URI the backend gives every toolset
 * (`{publicFrontend}/api-toolsets/oauth/callback/`). Same popup flow as MCP sign-in.
 */
export default function ApiToolsetOAuthCallbackPage() {
  return <McpOAuthCallbackClient completeCallback={ApiToolsetsApi.completeOAuthCallback} desktopFamily="toolset" />;
}
