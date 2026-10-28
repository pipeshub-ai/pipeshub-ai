import { MongoIdentityLookup } from './mongo';
import type { IdentityLookup } from './types';
import { pickStore } from '../store-select';

export * from './types';

let lookup: IdentityLookup | undefined;

export const getIdentityLookup = (): IdentityLookup =>
  (lookup ??= pickStore<IdentityLookup>('identity-lookup', {
    mongo: () => new MongoIdentityLookup(),
  }));

/** Tests: install a fake, or call with no argument to restore the default. */
export const setIdentityLookup = (l?: IdentityLookup): void => {
  lookup = l;
};
