/**
 * REST path allowlist for the desktop Socket.IO -> HTTP proxy (`DesktopProxySocketGateway`).
 * Only paths under these prefixes may be forwarded to the local Node API.
 */
import * as path from 'path';

/** Allowed REST path roots for the desktop Socket.IO -> HTTP proxy. */
export const DEFAULT_REST_PROXY_ALLOWED_PREFIXES = [
  '/api/v1/connectors',
  '/api/v1/knowledgeBase',
  '/api/v1/crawlingManager',
] as const;

type RestProxyPathCheck =
  | { ok: true; normalizedPath: string }
  | { ok: false; reason: string };

/**
 * Decode, normalize, and verify the REST path is under one of the allowed API prefixes
 * (segment-boundary safe - no `/api/v1/connectorsEvil` bypass).
 */
export function normalizeAndAssertRestProxyPath(
  rawPath: string,
  allowedPrefixes: readonly string[],
): RestProxyPathCheck {
  const trimmed = rawPath.trim();
  if (!trimmed.startsWith('/')) {
    return { ok: false, reason: 'Path must start with /' };
  }

  let decoded: string;
  try {
    decoded = decodeURIComponent(trimmed);
  } catch {
    return { ok: false, reason: 'Invalid path encoding' };
  }

  if (decoded.includes('\0')) {
    return { ok: false, reason: 'Invalid path' };
  }

  const normalized = path.posix.normalize(decoded);
  if (!normalized.startsWith('/')) {
    return { ok: false, reason: 'Invalid path' };
  }

  const collapsed = normalized.replace(/\/+/g, '/');

  for (const prefix of allowedPrefixes) {
    if (collapsed === prefix || collapsed.startsWith(`${prefix}/`)) {
      return { ok: true, normalizedPath: collapsed };
    }
  }

  return { ok: false, reason: 'Path is not allowed for REST proxy' };
}
