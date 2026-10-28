import { MongoResetLinkRepository } from './mongo';
import { PgResetLinkRepository } from './pg';
import type { ResetLinkRepository } from './types';
import { pickStore } from '../store-select';

export * from './types';

let repo: ResetLinkRepository | undefined;

export const getResetLinkRepository = (): ResetLinkRepository =>
  (repo ??= pickStore<ResetLinkRepository>('reset-link', {
    postgres: () => new PgResetLinkRepository(),
    mongo: () => new MongoResetLinkRepository(),
  }));

/** Replaces the repository (tests, container wiring); no argument restores the default for the selected store. */
export const setResetLinkRepository = (r?: ResetLinkRepository): void => {
  repo = r;
};
