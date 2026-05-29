import type { Connector } from '../types';

/** Whether the connector schema requires native-app admin access before team setup. */
export function isAdminAccessRequired(connector: Connector): boolean {
  return connector.isAdminAccessRequired === true;
}

/** Connector type key to redirect non-admin users to (e.g. gitlabpersonal). */
export function getPersonalConnectorRedirectType(connector: Connector): string | undefined {
  const type = connector.personalConnectorType;
  return typeof type === 'string' && type.length > 0 ? type : undefined;
}

/** Show admin-access dialog only when creating a new instance and schema flag is set. */
export function shouldPromptAdminAccess(connector: Connector, isCreateMode: boolean): boolean {
  return isCreateMode && isAdminAccessRequired(connector);
}
