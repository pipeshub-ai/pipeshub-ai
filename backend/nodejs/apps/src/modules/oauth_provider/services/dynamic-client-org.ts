import { getIdentityPort } from '../../../libs/repositories/identity';

/**
 * The organisation that stores a client registered at /register (RFC 7591). The endpoint has no
 * session and the client is the instance's, not a workspace's: this install has one organisation,
 * which stores it. Null until the instance has one. Exported through `src/config.ts`.
 */
export const instanceOrgForDynamicClients = (): Promise<string | null> =>
  getIdentityPort().oldestOrgId();
