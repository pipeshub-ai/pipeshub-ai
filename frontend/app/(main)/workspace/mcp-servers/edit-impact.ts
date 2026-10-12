import type { McpMyServerEntry, McpServerInstancePayload } from './types';
import { usesSharedCredential } from './connection-state';

/**
 * What saving an edit does to stored credentials, so the form can ask first:
 * - `all_credentials`: every user's and agent's credential for the server is removed;
 * - `shared_credential`: only the shared admin credential is removed;
 * - `none`: credentials are kept.
 */
export type McpEditCredentialImpact = 'none' | 'all_credentials' | 'shared_credential';

type ConnectionTarget = Pick<
  McpServerInstancePayload,
  'typeId' | 'transport' | 'url' | 'command' | 'args' | 'authMode' | 'tokenUrl' | 'useAdminAuth'
>;

// Mirrors `_CONNECTION_TARGET_FIELDS` in backend/python/app/api/routes/mcp_servers.py.
const CONNECTION_TARGET_FIELDS = ['typeId', 'transport', 'url', 'command', 'args', 'authMode', 'tokenUrl'] as const;

/** Missing, empty and null all compare equal, as `(value or None)` does on the backend. */
function comparable(value: unknown): string | null {
  if (value === undefined || value === null || value === '') return null;
  if (Array.isArray(value) && value.length === 0) return null;
  return JSON.stringify(value);
}

/** Mirrors the update route's `_connection_target_changed` and `_settle_shared_credential`. */
export function mcpEditCredentialImpact(
  existing: Pick<McpMyServerEntry, keyof ConnectionTarget>,
  next: ConnectionTarget
): McpEditCredentialImpact {
  const sameCatalogType = Boolean(next.typeId) && existing.typeId === next.typeId;
  // A catalog server connects to its template's target whatever the stored copy says.
  const fields = sameCatalogType ? (['authMode'] as const) : CONNECTION_TARGET_FIELDS;
  if (fields.some((field) => comparable(existing[field]) !== comparable(next[field]))) {
    return 'all_credentials';
  }
  const sharedBefore = usesSharedCredential(existing);
  const sharedAfter = usesSharedCredential({ authMode: next.authMode, useAdminAuth: Boolean(next.useAdminAuth) });
  return sharedBefore && !sharedAfter ? 'shared_credential' : 'none';
}
