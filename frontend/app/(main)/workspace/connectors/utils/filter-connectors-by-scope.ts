import type { Connector, ConnectorScope } from '../types';

/**
 * Keeps only connectors that belong on the team vs personal catalog.
 * Matches legacy `frontend/.../connectors.tsx` `currentScopeConnectors` behavior so
 * mixed API responses (e.g. personal instances under `?scope=team`) are not shown.
 */
export function filterConnectorsForScope(
  connectors: Connector[],
  scope: ConnectorScope
): Connector[] {
  if (scope === 'personal') {
    return connectors.filter((c) => c.scope === 'personal' || !c.scope);
  }
  return connectors.filter((c) => c.scope === 'team');
}
