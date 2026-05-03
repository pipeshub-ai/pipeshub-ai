import { ConnectorsApi } from '../api';
import type { Connector, ConnectorScope } from '../types';
import { filterConnectorsForScope } from './filter-connectors-by-scope';

/** Refetch registry + active lists for a scope (with scope filter). Does not touch loading flags. */
export async function fetchFilteredConnectorLists(
  scope: ConnectorScope
): Promise<{ registry: Connector[] | null; active: Connector[] | null }> {
  const [registryRes, activeRes] = await Promise.allSettled([
    ConnectorsApi.getRegistryConnectors(scope),
    ConnectorsApi.getActiveConnectors(scope),
  ]);
  const registry =
    registryRes.status === 'fulfilled'
      ? filterConnectorsForScope(registryRes.value.connectors, scope)
      : null;
  const active =
    activeRes.status === 'fulfilled'
      ? filterConnectorsForScope(activeRes.value.connectors, scope)
      : null;
  return { registry, active };
}
