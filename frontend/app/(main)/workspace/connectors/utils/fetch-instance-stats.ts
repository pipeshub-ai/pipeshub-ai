import { ConnectorsApi } from '../api';
import { useConnectorsStore } from '../store';

/** Load connector indexing stats into the store (skips if cached unless `force`). */
export async function fetchInstanceStats(
  connectorId: string,
  options?: { force?: boolean }
): Promise<void> {
  const state = useConnectorsStore.getState();
  if (!options?.force && state.instanceStats[connectorId]) {
    return;
  }
  const res = await ConnectorsApi.getConnectorStats(connectorId);
  useConnectorsStore.getState().setInstanceStats(connectorId, res.data);
}

/** Refetch stats when the instance management drawer is open for this connector. */
export function fetchInstanceStatsIfPanelOpen(connectorId: string): void {
  const { isInstancePanelOpen, selectedInstance } = useConnectorsStore.getState();
  if (!isInstancePanelOpen || selectedInstance?._key !== connectorId) {
    return;
  }
  void fetchInstanceStats(connectorId, { force: true }).catch(() => {});
}
