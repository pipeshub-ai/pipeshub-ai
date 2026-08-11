import { isConnectorInstanceOAuthAuthIncompleteForSyncUi } from '../../utils/auth-helpers';
import type { ConnectorConfig, ConnectorInstance } from '../../types';

/** Setup / readiness derived from list API flags (not indexing stats). */
export type InstanceSetupStatusKey =
  | 'not_configured'
  | 'needs_authentication'
  | 'ready';

export type InstanceSetupStatusView = {
  key: InstanceSetupStatusKey;
  badgeColor: 'gray' | 'amber' | 'green';
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
