'use client';

import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { useConnectorsStore } from '../../store';
import { canEditLinkedOAuthApp, canEnterOAuthClient } from '../../utils/auth-helpers';

/**
 * Scope of the connector open in the panel: a new one takes the catalog's, an existing one keeps its own.
 * Right after a create the panel still holds the catalog row, which has no scope, so the scope the
 * panel was opened with stands in.
 */
export function usePanelConnectorScope(): string | undefined {
  return useConnectorsStore((s) =>
    s.panelConnectorId ? (s.panelConnector?.scope ?? s.selectedScope) : s.selectedScope
  );
}

/** {@link canEnterOAuthClient} for the connector open in the panel. */
export function useCanEnterOAuthClient(): boolean | null {
  const isAdmin = useUserStore(selectIsAdmin);
  return canEnterOAuthClient(isAdmin, usePanelConnectorScope());
}

/** {@link canEditLinkedOAuthApp} for the connector open in the panel. */
export function useCanEditLinkedOAuthApp(): boolean | null {
  const isAdmin = useUserStore(selectIsAdmin);
  return canEditLinkedOAuthApp(isAdmin, usePanelConnectorScope());
}
