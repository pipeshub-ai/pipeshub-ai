import { ConnectorsApi } from '../api';
import type { ConnectorInstance } from '../types';

/**
 * Single entry point for "make this instance sync now".
 * - Inactive → toggle sync ON; backend publishes `appEnabled` with `syncAction:"immediate"`.
 * - Active   → resync (kick a new sync job on the already-enabled connector).
 * Matches the legacy frontend: never chains toggle + resync in one action.
 */
export async function startConnectorSync(
  instance: Pick<ConnectorInstance, '_key'> &
    Partial<Pick<ConnectorInstance, 'type' | 'isActive'>>
): Promise<void> {
  const fresh = await ConnectorsApi.getConnectorInstance(instance._key);
  if (fresh.isActive) {
    const type = fresh.type || instance.type;
    if (!type) {
      throw new Error(
        `startConnectorSync: connector type unknown for instance ${instance._key}`
      );
    }
    await ConnectorsApi.resyncConnector(instance._key, type);
    return;
  }
  await ConnectorsApi.toggleConnector(instance._key, 'sync');
}
