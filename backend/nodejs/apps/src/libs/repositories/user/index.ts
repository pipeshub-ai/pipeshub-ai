import { MongoUserRepository } from './mongo';
import type { UserRepository } from './types';

export * from './types';

let repo: UserRepository | undefined;

export const getUserRepository = (): UserRepository =>
  (repo ??= new MongoUserRepository());

/** Replaces the repository (tests, container wiring); no argument restores the default for the selected store. */
export const setUserRepository = (r?: UserRepository): void => {
  repo = r;
};
