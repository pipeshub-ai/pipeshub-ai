import { MongoIdentityPort } from './mongo';
import { PgIdentityPort } from './pg';
import type { IdentityPort } from './types';
import { pickStore } from '../store-select';

export * from './types';

let repo: IdentityPort | undefined;

export const getIdentityPort = (): IdentityPort =>
  (repo ??= pickStore<IdentityPort>('identity', {
    postgres: () => new PgIdentityPort(),
    mongo: () => new MongoIdentityPort(),
  }));

/** Replaces the repository (tests, container wiring); no argument restores the default for the selected store. */
export const setIdentityPort = (r?: IdentityPort): void => {
  repo = r;
};
