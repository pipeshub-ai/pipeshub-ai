import { ConnectorsApi } from '../api';

/**
 * Ensures sync is enabled for the instance, then kicks a normal resync job.
 * The toggle endpoint flips `isActive`; it must not be used as "start sync" when already active.
 */
export async function ensureConnectorSyncActiveThenResync(instance: {
  _key: string;
  type: string;
  isActive: boolean;
}): Promise<void> {
  if (!instance.isActive) {
    await ConnectorsApi.toggleConnector(instance._key, 'sync');
  }
  await ConnectorsApi.resyncConnector(instance._key, instance.type);
}
