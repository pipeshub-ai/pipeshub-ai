import { ConnectorsApi } from '../api';
import type { Connector, ConnectorListResponse, ConnectorScope } from '../types';
import { filterConnectorsForScope } from './filter-connectors-by-scope';

/**
 * Refetch registry + active lists for a scope (with scope filter). Does not touch loading flags.
 * `activeResponse` is the active list as the server sent it, for callers that cache it.
 */
export async function fetchFilteredConnectorLists(scope: ConnectorScope): Promise<{
  registry: Connector[] | null;
  active: Connector[] | null;
  activeResponse: ConnectorListResponse | null;
}> {
  const [registryRes, activeRes] = await Promise.allSettled([
    ConnectorsApi.getRegistryConnectors(scope),
    ConnectorsApi.getActiveConnectors(scope),
  ]);
  const registry =
    registryRes.status === 'fulfilled'
      ? filterConnectorsForScope(registryRes.value.connectors, scope)
      : null;
  const activeResponse = activeRes.status === 'fulfilled' ? activeRes.value : null;
  const active = activeResponse ? filterConnectorsForScope(activeResponse.connectors, scope) : null;
  return { registry, active, activeResponse };
}
