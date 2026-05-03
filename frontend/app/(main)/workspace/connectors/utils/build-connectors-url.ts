/**
 * Build a URL for the connectors page, routing to team or personal
 * scope based on admin status.
 *
 * @param isAdmin - true → team scope, false/null → personal scope
 * @param connectorTypeParam - optional connector type to open its panel
 */
export function buildConnectorsUrl(
  isAdmin: boolean | null,
  connectorTypeParam?: string
): string {
  const base = isAdmin === true
    ? '/workspace/connectors/team/'
    : '/workspace/connectors/personal/';
  if (!connectorTypeParam) return base;
  return `${base}?connectorType=${encodeURIComponent(connectorTypeParam)}`;
}
