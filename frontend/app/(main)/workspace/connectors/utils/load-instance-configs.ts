import { captureSession } from '@/lib/api/session-cache';
import { revalidateSwrNamespace, SWR_NS } from '@/lib/api/swr-cache';
import { listenForConnectorOAuthSuccess } from '@/app/(main)/connectors/oauth/connector-oauth-window-messages';
import { ConnectorsApi } from '../api';
import { useConnectorsStore } from '../store';

// A sign-in finished in the OAuth popup changes the instance's config even when its panel was closed meanwhile.
listenForConnectorOAuthSuccess(() => revalidateSwrNamespace(SWR_NS.connectorConfigs));

/**
 * Reads each instance's saved config into the store, keyed by instance, so
 * every answer is kept even if the page changed meanwhile. One that arrives
 * after sign-out is dropped. Rejects when any read failed, so the set is not
 * treated as fresh and the next visit reads it again.
 */
export async function loadInstanceConfigs(connectorIds: string[]): Promise<true> {
  // Signing out does not reload the desktop app: an answer for the previous person must not land in the store.
  const stillSignedIn = captureSession();
  const results = await Promise.allSettled(
    connectorIds.map(async (id) => {
      const config = await ConnectorsApi.getConnectorConfig(id);
      if (stillSignedIn()) useConnectorsStore.getState().setInstanceConfig(id, config);
    }),
  );
  if (results.some((result) => result.status === 'rejected')) {
    throw new Error('Some connector configs could not be read');
  }
  return true;
}
