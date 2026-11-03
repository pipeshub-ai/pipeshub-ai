import { onSessionEnd } from '@/lib/api/session-cache';
import { revalidateSwrNamespace, SWR_NS } from '@/lib/api/swr-cache';
import { CONNECTOR_OAUTH_POST_MESSAGE } from '@/app/(main)/connectors/oauth/connector-oauth-window-messages';
import { ConnectorsApi } from '../api';
import { useConnectorsStore } from '../store';

// Signing out does not reload the desktop app: an answer for the previous person must not land in the store.
// shortcut: a local session counter; switch to Fix 5's sessionEpoch() once it lands.
let generation = 0;
onSessionEnd(() => {
  generation += 1;
});

// A sign-in finished in the OAuth popup changes the instance's config even when its panel was closed meanwhile.
if (typeof window !== 'undefined') {
  window.addEventListener('message', (event: MessageEvent) => {
    if (event.origin !== window.location.origin) return;
    if ((event.data as { type?: string } | null)?.type === CONNECTOR_OAUTH_POST_MESSAGE.SUCCESS) {
      revalidateSwrNamespace(SWR_NS.connectorConfigs);
    }
  });
}

/**
 * Reads each instance's saved config into the store, keyed by instance, so
 * every answer is kept even if the page changed meanwhile. One that arrives
 * after sign-out is dropped. Rejects when any read failed, so the set is not
 * treated as fresh and the next visit reads it again.
 */
export async function loadInstanceConfigs(connectorIds: string[]): Promise<true> {
  const started = generation;
  const results = await Promise.allSettled(
    connectorIds.map(async (id) => {
      const config = await ConnectorsApi.getConnectorConfig(id);
      if (started === generation) useConnectorsStore.getState().setInstanceConfig(id, config);
    }),
  );
  if (results.some((result) => result.status === 'rejected')) {
    throw new Error('Some connector configs could not be read');
  }
  return true;
}
