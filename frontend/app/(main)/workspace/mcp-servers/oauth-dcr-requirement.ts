import type { McpOAuthConfigResponse, McpOAuthDiscoveryResult } from './types';

export type DcrProbeState =
  | { status: 'idle' | 'loading' }
  | { status: 'done'; result: McpOAuthDiscoveryResult }
  | { status: 'error' };

/**
 * Whether the MCP server behind an OAuth instance supports dynamic client registration.
 *
 * `null` means unknown (probe pending/failed and no template hint either) — callers must
 * never treat `null` as a confirmed "no DCR" and force credentials to required on a guess.
 */
export function resolveDcrSupport(
  dcrProbe: DcrProbeState,
  templateSupportsDcr: boolean | null | undefined
): boolean | null {
  if (dcrProbe.status === 'done') {
    if (dcrProbe.result.metadataFound) return dcrProbe.result.supportsDcr;
    return templateSupportsDcr ?? null;
  }
  return templateSupportsDcr ?? null;
}

/** OAuth client credentials are only mandatory once DCR is confirmed unsupported. */
export function isOauthClientRequired(authMode: string, dcrSupported: boolean | null): boolean {
  return authMode === 'oauth' && dcrSupported === false;
}

export function isOauthClientMissing(
  oauthClientRequired: boolean,
  hasExistingOAuthClient: boolean,
  oauthClientId: string,
  oauthClientSecret: string
): boolean {
  return (
    oauthClientRequired &&
    !hasExistingOAuthClient &&
    !(oauthClientId.trim() && oauthClientSecret.trim())
  );
}

/**
 * The redirect URI an admin registers the OAuth app with. The server's answer is the address it
 * actually sends (its configured public address, sub-path included); the page's own origin only
 * stands in until that answer arrives.
 */
export function resolveMcpOAuthCallbackUrl(
  dcrProbe: DcrProbeState,
  savedConfig: Pick<McpOAuthConfigResponse, 'redirectUri'> | null | undefined,
  origin: string | null = typeof window === 'undefined' ? null : window.location.origin
): string | null {
  const fromServer = (dcrProbe.status === 'done' ? dcrProbe.result.redirectUri : undefined) ?? savedConfig?.redirectUri;
  if (fromServer) return fromServer;
  return origin ? `${origin.replace(/\/$/, '')}/mcp-servers/oauth/callback/` : null;
}
