import { MongoIdentityPort } from './mongo';
import type { IdentityPort } from './types';

export * from './types';

let repo: IdentityPort | undefined;

export const getIdentityPort = (): IdentityPort =>
  (repo ??= new MongoIdentityPort());

/** Replaces the repository (tests, container wiring); no argument restores the default for the selected store. */
export const setIdentityPort = (r?: IdentityPort): void => {
  repo = r;
};
