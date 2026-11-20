'use client';

import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { useConnectorsStore } from '../../store';
import { canEnterOAuthClient } from '../../utils/auth-helpers';

/** Scope of the connector open in the panel: a new one takes the catalog's, an existing one keeps its own. */
export function usePanelConnectorScope(): string | undefined {
  return useConnectorsStore((s) => (s.panelConnectorId ? s.panelConnector?.scope : s.selectedScope));
}

/** {@link canEnterOAuthClient} for the connector open in the panel. */
export function useCanEnterOAuthClient(): boolean | null {
  const isAdmin = useUserStore(selectIsAdmin);
  return canEnterOAuthClient(isAdmin, usePanelConnectorScope());
}
