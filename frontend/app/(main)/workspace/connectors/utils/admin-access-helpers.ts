import type { Connector } from '../types';

/** Whether the connector schema requires native-app admin access before team setup. */
export function isAdminAccessRequired(connector?: Connector | null): boolean {
  return connector?.isAdminAccessRequired === true;
}

/** Personal connector to point users at, as a registry `type` (e.g. "GitLab Personal"). */
export function getPersonalConnectorRedirectType(connector?: Connector | null): string | undefined {
  const type = connector?.personalConnectorType;
  return typeof type === 'string' && type.length > 0 ? type : undefined;
}

/** Personal connectors page, focused on `connectorType` when one is known. */
export function personalConnectorHref(connectorType?: string): string {
  return connectorType
    ? `/workspace/connectors/personal/?connectorType=${encodeURIComponent(connectorType)}`
    : '/workspace/connectors/personal/';
}

/** Show admin-access dialog only when creating a new instance and schema flag is set. */
export function shouldPromptAdminAccess(connector: Connector, isCreateMode: boolean): boolean {
  return isCreateMode && isAdminAccessRequired(connector);
}
