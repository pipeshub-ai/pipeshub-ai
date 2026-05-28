import { ConnectorsApi } from '../api';
import { useConnectorsStore } from '../store';
import type { Connector, ConnectorConfig } from '../types';
import { fetchInstanceStatsIfPanelOpen } from './fetch-instance-stats';

export type AfterInstanceConfigRefreshed = (
  instance: Connector,
  config: ConnectorConfig
) => void | Promise<void>;

/** Refetch instance row + config; refresh drawer stats when that instance is open. */
export async function refreshConnectorInstanceDetails(
  connectorId: string,
  options?: { afterConfig?: AfterInstanceConfigRefreshed }
): Promise<Connector> {
  const { upsertConnectorInstance, setInstanceConfig } = useConnectorsStore.getState();

  const fresh = await ConnectorsApi.getConnectorInstance(connectorId);
  upsertConnectorInstance(fresh);

  const config = await ConnectorsApi.getConnectorConfig(connectorId).catch(() => null);
  if (config) {
    setInstanceConfig(connectorId, config);
    await options?.afterConfig?.(fresh, config);
  }

  fetchInstanceStatsIfPanelOpen(connectorId);
  return fresh;
}
