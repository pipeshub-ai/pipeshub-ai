import { MongoUserRepository } from './mongo';
import { PgUserRepository } from './pg';
import type { UserRepository } from './types';
import { pickStore } from '../store-select';

export * from './types';

let repo: UserRepository | undefined;

export const getUserRepository = (): UserRepository =>
  (repo ??= pickStore<UserRepository>('user', {
    postgres: () => new PgUserRepository(),
    mongo: () => new MongoUserRepository(),
  }));

/** Replaces the repository (tests, container wiring); no argument restores the default for the selected store. */
export const setUserRepository = (r?: UserRepository): void => {
  repo = r;
};
