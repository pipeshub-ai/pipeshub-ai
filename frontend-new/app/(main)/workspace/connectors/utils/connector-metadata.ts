import type { Connector, DocumentationLink } from '../types';

/** Plain-string info from `@Connector(..., connector_info="...")` for UI callouts. */
export function getConnectorInfoText(connector: Connector | null | undefined): string | null {
  const ci = connector?.connectorInfo;
  if (typeof ci !== 'string') return null;
  const t = ci.trim();
  return t.length > 0 ? t : null;
}

/**
 * Resolves the connector documentation URL for the "open documentation" action.
 * Prefers `documentationLinks` with type `pipeshub`, then the first non-empty link,
 * then legacy `connectorInfo.documentationUrl` when `connectorInfo` is an object.
 */
export function getConnectorDocumentationUrl(
  connector: Connector | null | undefined
): string | undefined {
  if (!connector) return undefined;
  const configObj = connector.config as Record<string, unknown> | undefined;
  const links = (configObj?.documentationLinks as DocumentationLink[] | undefined) ?? [];

  const pipeshub = links.find((l) => l.type === 'pipeshub' && (l.url?.trim() ?? '') !== '');
  if (pipeshub?.url) return pipeshub.url.trim();
  const first = links.find((l) => (l.url?.trim() ?? '') !== '');
  if (first?.url) return first.url.trim();

  const ci = connector.connectorInfo;
  if (ci && typeof ci === 'object' && !Array.isArray(ci) && 'documentationUrl' in ci) {
    const u = (ci as { documentationUrl?: unknown }).documentationUrl;
    if (typeof u === 'string' && u.trim()) return u.trim();
  }
  return undefined;
}
