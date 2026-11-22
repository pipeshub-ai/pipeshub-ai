import { getIdentityPort } from '../../../libs/repositories/identity';

/**
 * The organisation that stores a dynamically registered client. A metadata document client is
 * stored at its first consent, in the organisation of the person who consented. A client
 * registered at /register (RFC 7591) arrives with no session and is the instance's: this install
 * has one organisation, which stores it. Null until the instance has one. Exported through
 * `src/config.ts`.
 */
export const dynamicClientOwnerOrg = (consenter?: {
  orgId: string;
}): Promise<string | null> =>
  consenter
    ? Promise.resolve(consenter.orgId)
    : getIdentityPort().oldestOrgId();
