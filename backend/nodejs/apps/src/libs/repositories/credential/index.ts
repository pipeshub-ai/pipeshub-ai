import { getIdentityPort } from '../identity';
import type { CredentialRepository } from './types';

export * from './types';

/** The credential calls are part of the identity port (a person's secrets belong to no one organisation): same object, same store. */
export const getCredentialRepository = (): CredentialRepository =>
  getIdentityPort();

/** Tests: install a fake identity port, or call with no argument to restore the default. */
export { setIdentityPort as setCredentialRepository } from '../identity';
