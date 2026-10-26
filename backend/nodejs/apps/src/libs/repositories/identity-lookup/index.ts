import { MongoIdentityLookup } from './mongo';
import type { IdentityLookup } from './types';

export * from './types';

let lookup: IdentityLookup | undefined;

export const getIdentityLookup = (): IdentityLookup =>
  (lookup ??= new MongoIdentityLookup());

/** Tests: install a fake, or call with no argument to restore the default. */
export const setIdentityLookup = (l?: IdentityLookup): void => {
  lookup = l;
};
