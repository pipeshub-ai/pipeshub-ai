import type { Connector } from '../types';

/** Plain-string info from `@Connector(..., connector_info="...")` for UI callouts. */
export function getConnectorInfoText(connector: Connector | null | undefined): string | null {
  const ci = connector?.connectorInfo;
  if (typeof ci !== 'string') return null;
  const t = ci.trim();
  return t.length > 0 ? t : null;
}

/**
 * Prefer config.documentationLinks; rare legacy shape stores `documentationUrl` on a connectorInfo object.
 */
export function getConnectorDocumentationUrl(
  connector: Connector | null | undefined
): string | undefined {
  if (!connector) return undefined;
  const configObj = connector.config as Record<string, unknown> | undefined;
  const docLinks = configObj?.documentationLinks as { url?: string }[] | undefined;
  const fromLinks = docLinks?.[0]?.url;
  if (typeof fromLinks === 'string' && fromLinks.trim()) return fromLinks.trim();

  const ci = connector.connectorInfo;
  if (ci && typeof ci === 'object' && !Array.isArray(ci) && 'documentationUrl' in ci) {
    const u = (ci as { documentationUrl?: unknown }).documentationUrl;
    return typeof u === 'string' && u.trim() ? u.trim() : undefined;
  }
  return undefined;
}
