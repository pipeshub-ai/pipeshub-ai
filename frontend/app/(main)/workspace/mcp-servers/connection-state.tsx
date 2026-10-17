'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Badge } from '@radix-ui/themes';
import type { McpMyServerEntry } from './types';

/** One answer, everywhere, to "can I use this server right now, and if not, what fixes it?" */
export type McpConnectionState =
  | 'ready'
  /** The caller has never connected. Action: Connect. */
  | 'needs_connect'
  /** The caller's sign-in expired or was rejected. Action: Reconnect. */
  | 'needs_reconnect'
  /** Everyone shares one credential and no administrator has entered it (member's view). */
  | 'waiting_for_admin'
  /** The same, seen by an administrator, who can fix it. */
  | 'shared_credential_missing'
  /** This deployment's URL guard or local-command policy refuses it: an administrator's fix. */
  | 'blocked'
  /** Connected, but the server answered with an error or couldn't be reached. */
  | 'unreachable'
  /** Connected, but it missed the listing's time budget; chats still wait for it. */
  | 'slow';

type StateEntry = Pick<
  McpMyServerEntry,
  'authMode' | 'useAdminAuth' | 'isAuthenticated' | 'toolsError' | 'toolsErrorCode' | 'toolsTimedOut'
>;

/** Mirrors the backend's `uses_shared_credential`: OAuth is always per person, even when marked shared. */
export function usesSharedCredential(entry: Pick<McpMyServerEntry, 'authMode' | 'useAdminAuth'>): boolean {
  return Boolean(entry.useAdminAuth) && (entry.authMode === 'api_token' || entry.authMode === 'headers');
}

export function mcpConnectionState(entry: StateEntry, { isAdmin = false }: { isAdmin?: boolean } = {}): McpConnectionState {
  const shared = usesSharedCredential(entry);
  if (entry.authMode !== 'none' && !entry.isAuthenticated) {
    if (shared) return isAdmin ? 'shared_credential_missing' : 'waiting_for_admin';
    return 'needs_connect';
  }
  switch (entry.toolsErrorCode) {
    case 'auth_expired':
    case 'unauthorized':
    // Signing in again asks for the scopes the server said it needs.
    case 'needs_permission':
      // A shared credential that stopped working is the administrator's to replace.
      if (shared) return isAdmin ? 'shared_credential_missing' : 'waiting_for_admin';
      return 'needs_reconnect';
    case 'blocked':
      return 'blocked';
    case 'timeout':
      return 'slow';
    case 'unreachable':
    case 'error':
      return 'unreachable';
    default:
      break;
  }
  // Responses from before the backend sent a code.
  if (entry.toolsTimedOut) return 'slow';
  if (entry.toolsError) return 'unreachable';
  return 'ready';
}

const BADGES: Record<McpConnectionState, { color: 'green' | 'amber' | 'red' | 'gray'; key: string }> = {
  ready: { color: 'green', key: 'workspace.mcpServers.status.ready' },
  needs_connect: { color: 'amber', key: 'workspace.mcpServers.status.notConnected' },
  needs_reconnect: { color: 'amber', key: 'workspace.mcpServers.status.reconnectNeeded' },
  waiting_for_admin: { color: 'gray', key: 'workspace.mcpServers.status.waitingForAdmin' },
  shared_credential_missing: { color: 'amber', key: 'workspace.mcpServers.status.sharedCredentialMissing' },
  blocked: { color: 'red', key: 'workspace.mcpServers.status.blocked' },
  unreachable: { color: 'red', key: 'workspace.mcpServers.status.unreachable' },
  slow: { color: 'gray', key: 'workspace.mcpServers.status.slow' },
};

/** The colour and label key a state is shown with. */
export function mcpStatusLook(state: McpConnectionState): { color: 'green' | 'amber' | 'red' | 'gray'; key: string } {
  return BADGES[state];
}

export function McpStatusBadge({ state }: { state: McpConnectionState }) {
  const { t } = useTranslation();
  const badge = BADGES[state];
  return (
    <Badge color={badge.color} size="1">
      {t(badge.key)}
    </Badge>
  );
}
