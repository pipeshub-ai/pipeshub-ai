import { MongoIdentityLookup } from './mongo';
import { PgIdentityLookup } from './pg';
import type { IdentityLookup } from './types';
import { pickStore } from '../store-select';

export * from './types';

let lookup: IdentityLookup | undefined;

export const getIdentityLookup = (): IdentityLookup =>
  (lookup ??= pickStore<IdentityLookup>('identity-lookup', {
    postgres: () => new PgIdentityLookup(),
    mongo: () => new MongoIdentityLookup(),
  }));

/** Tests: install a fake, or call with no argument to restore the default. */
export const setIdentityLookup = (l?: IdentityLookup): void => {
  lookup = l;
};
