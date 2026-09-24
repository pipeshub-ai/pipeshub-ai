import type { McpOAuthDiscoveryResult } from './types';

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
