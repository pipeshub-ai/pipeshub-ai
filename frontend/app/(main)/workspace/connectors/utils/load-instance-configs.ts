import { onSessionEnd } from '@/lib/api/session-cache';
import { ConnectorsApi } from '../api';
import { useConnectorsStore } from '../store';

// Signing out does not reload the desktop app: an answer for the previous person must not land in the store.
let generation = 0;
onSessionEnd(() => {
  generation += 1;
});

function stillListed(connectorId: string): boolean {
  return useConnectorsStore.getState().activeConnectors.some((connector) => connector._key === connectorId);
}

/**
 * Reads each instance's saved config into the store. A config for an instance
 * removed meanwhile, or one that arrives after sign-out, is dropped. Rejects when
 * any read failed, so the set is not treated as fresh and the next visit reads it again.
 */
export async function loadInstanceConfigs(connectorIds: string[]): Promise<true> {
  const started = generation;
  const results = await Promise.allSettled(
    connectorIds.map(async (id) => {
      const config = await ConnectorsApi.getConnectorConfig(id);
      if (started === generation && stillListed(id)) {
        useConnectorsStore.getState().setInstanceConfig(id, config);
      }
    }),
  );
  if (results.some((result) => result.status === 'rejected')) {
    throw new Error('Some connector configs could not be read');
  }
  return true;
}
