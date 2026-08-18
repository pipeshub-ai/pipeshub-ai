/**
 * postMessage contracts between the MCP OAuth callback page
 * (`/mcp-servers/oauth/callback/`) and the opener (workspace MCP servers pages).
 *
 * Types are namespaced so they never collide with generic auth popups
 * (`OAUTH_SUCCESS` from `/auth/oauth/callback`) or the connector/toolset variants.
 */
export const MCP_OAUTH_POST_MESSAGE = {
  SUCCESS: 'MCP_OAUTH_SUCCESS',
  ERROR: 'MCP_OAUTH_ERROR',
} as const;

function openerTargetOrigin(): string {
  return typeof window !== 'undefined' ? window.location.origin : '';
}

export function postMcpOAuthSuccessToOpener(instanceId: string | null): void {
  const origin = openerTargetOrigin();
  if (!origin || !window.opener) return;
  try {
    window.opener.postMessage({ type: MCP_OAUTH_POST_MESSAGE.SUCCESS, instanceId }, origin);
  } catch (e) {
    if (process.env.NODE_ENV === 'development') {
      console.debug('[mcp-oauth] postMessage success failed', e);
    }
  }
}

export function postMcpOAuthErrorToOpener(error: string): void {
  const origin = openerTargetOrigin();
  if (!origin || !window.opener) return;
  try {
    window.opener.postMessage({ type: MCP_OAUTH_POST_MESSAGE.ERROR, error }, origin);
  } catch (e) {
    if (process.env.NODE_ENV === 'development') {
      console.debug('[mcp-oauth] postMessage error failed', e);
    }
  }
}

export function isMcpOAuthSuccessMessageType(type: unknown): boolean {
  return type === MCP_OAUTH_POST_MESSAGE.SUCCESS;
}

export function isMcpOAuthErrorMessageType(type: unknown): boolean {
  return type === MCP_OAUTH_POST_MESSAGE.ERROR;
}
