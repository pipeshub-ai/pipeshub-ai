import { CONNECTOR_INSTANCE_STATUS } from '../../constants';
import { isConnectorInstanceOAuthAuthIncompleteForSyncUi } from '../../utils/auth-helpers';
import type { ConnectorConfig, ConnectorInstance } from '../../types';

/** Setup / readiness derived from list API flags (not indexing stats). */
export type InstanceSetupStatusKey =
  | 'not_configured'
  | 'needs_authentication'
  | 'ready';

/** Transient sync job from backend `status` (IDLE → no badge). */
export type InstanceSyncOperationKey = 'syncing' | 'full_syncing' | 'deleting';

export type InstanceSetupStatusView = {
  key: InstanceSetupStatusKey;
  badgeColor: 'gray' | 'amber' | 'green';
  icon: string;
};

export type InstanceSyncOperationView = {
  key: InstanceSyncOperationKey;
  badgeColor: 'blue' | 'red';
  icon: string;
};

export function deriveInstanceSetupStatus(
  instance: Pick<
    ConnectorInstance,
    'isConfigured' | 'isAuthenticated' | 'authType' | 'type' | 'scope'
  >,
  config?: ConnectorConfig,
): InstanceSetupStatusView {
  if (!instance.isConfigured) {
    return { key: 'not_configured', badgeColor: 'gray', icon: 'settings' };
  }
  if (isConnectorInstanceOAuthAuthIncompleteForSyncUi(config, instance)) {
    return { key: 'needs_authentication', badgeColor: 'amber', icon: 'vpn_key' };
  }
  return { key: 'ready', badgeColor: 'green', icon: 'check_circle' };
}

export function deriveInstanceSyncOperation(
  instance: Pick<ConnectorInstance, 'status'>,
): InstanceSyncOperationView | null {
  const normalized = (instance.status ?? CONNECTOR_INSTANCE_STATUS.IDLE).toUpperCase();

  if (normalized === CONNECTOR_INSTANCE_STATUS.DELETING) {
    return { key: 'deleting', badgeColor: 'red', icon: 'delete' };
  }
  if (normalized === CONNECTOR_INSTANCE_STATUS.FULL_SYNCING) {
    return { key: 'full_syncing', badgeColor: 'blue', icon: 'cloud_sync' };
  }
  if (normalized === CONNECTOR_INSTANCE_STATUS.SYNCING) {
    return { key: 'syncing', badgeColor: 'blue', icon: 'sync' };
  }

  return null;
}
