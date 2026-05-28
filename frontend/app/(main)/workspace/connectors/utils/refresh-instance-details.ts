import { ConnectorsApi } from '../api';
import { useConnectorsStore } from '../store';
import type { ConnectorConfig, ConnectorInstance } from '../types';
import { fetchInstanceStatsIfPanelOpen } from './fetch-instance-stats';

export type AfterInstanceConfigRefreshed = (
  instance: ConnectorInstance,
  config: ConnectorConfig
) => void | Promise<void>;

/** Refetch instance row + config; refresh drawer stats when that instance is open. */
export async function refreshConnectorInstanceDetails(
  connectorId: string,
  options?: { afterConfig?: AfterInstanceConfigRefreshed }
): Promise<ConnectorInstance> {
  const { upsertConnectorInstance, setInstanceConfig } = useConnectorsStore.getState();

  const fresh = await ConnectorsApi.getConnectorInstance(connectorId);
  upsertConnectorInstance(fresh);

  if (fresh.isConfigured) {
    const config = await ConnectorsApi.getConnectorConfig(connectorId);
    setInstanceConfig(connectorId, config);
    await options?.afterConfig?.(fresh, config);
  }

  fetchInstanceStatsIfPanelOpen(connectorId);
  return fresh;
}

/** Run per-instance refresh in parallel; throw if any instance fails. */
export async function refreshAllConnectorInstances(
  instanceIds: string[],
  refreshOne: (connectorId: string) => Promise<unknown>
): Promise<void> {
  if (instanceIds.length === 0) return;
  const results = await Promise.allSettled(instanceIds.map(refreshOne));
  if (results.some((r) => r.status === 'rejected')) {
    throw new Error('One or more connector instances failed to refresh');
  }
}
