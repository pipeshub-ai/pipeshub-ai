import { ConnectorsApi } from '../api';
import type { ConnectorInstance } from '../types';

/**
 * Ensures sync is enabled for the instance, then kicks a normal resync job.
 * Re-fetches the instance first so `isActive` is never taken from stale client state — the toggle
 * endpoint flips `isActive` and would deactivate sync if called when the server already has sync on.
 * `type` is optional when unknown to the caller; the fresh GET supplies it for resync.
 */
export async function ensureConnectorSyncActiveThenResync(
  instance: Pick<ConnectorInstance, '_key'> & Partial<Pick<ConnectorInstance, 'type'>>
): Promise<void> {
  const fresh = await ConnectorsApi.getConnectorInstance(instance._key);
  if (!fresh.isActive) {
    await ConnectorsApi.toggleConnector(instance._key, 'sync');
  }
  const connectorType = fresh.type || instance.type;
  await ConnectorsApi.resyncConnector(instance._key, connectorType);
}
