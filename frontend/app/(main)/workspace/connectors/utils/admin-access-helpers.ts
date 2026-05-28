import type { Connector, ConnectorSchemaConfigFlags } from '../types';

function readConfigFlags(connector: Connector): ConnectorSchemaConfigFlags {
  const config = connector.config as ConnectorSchemaConfigFlags | undefined;
  return config ?? {};
}

/** Whether the connector schema requires native-app admin access before team setup. */
export function isAdminAccessRequired(connector: Connector): boolean {
  return readConfigFlags(connector).isAdminAccessRequired === true;
}

/** Connector type key to redirect non-admin users to (e.g. gitlabpersonal). */
export function getPersonalConnectorRedirectType(connector: Connector): string | undefined {
  const type = readConfigFlags(connector).personalConnectorType;
  return typeof type === 'string' && type.length > 0 ? type : undefined;
}

/** Show admin-access dialog only when creating a new instance and schema flag is set. */
export function shouldPromptAdminAccess(connector: Connector, isCreateMode: boolean): boolean {
  return isCreateMode && isAdminAccessRequired(connector);
}

/** Prefer registry entry so config flags are always present. */
export function resolveConnectorForSetup(
  connector: Connector,
  registryConnectors: Connector[]
): Connector {
  const registry = registryConnectors.find((c) => c.type === connector.type);
  return registry ?? connector;
}
