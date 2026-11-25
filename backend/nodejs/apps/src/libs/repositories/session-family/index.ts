import { MongoSessionFamilyRepository } from './mongo';
import { PgSessionFamilyRepository } from './pg';
import type { SessionFamilyRepository } from './types';
import { pickStore } from '../store-select';

export * from './types';

let repo: SessionFamilyRepository | undefined;

export const getSessionFamilyRepository = (): SessionFamilyRepository =>
  (repo ??= pickStore<SessionFamilyRepository>('session-family', {
    postgres: () => new PgSessionFamilyRepository(),
    mongo: () => new MongoSessionFamilyRepository(),
  }));

/** Replaces the repository (tests); no argument restores the configured one. */
export const setSessionFamilyRepository = (
  r?: SessionFamilyRepository,
): void => {
  repo = r;
};
