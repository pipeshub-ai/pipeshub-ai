import { MongoOrgRepository } from './mongo';
import { PgOrgRepository } from './pg';
import type { OrgRepository } from './types';
import { pickStore } from '../store-select';

export * from './types';

let repo: OrgRepository | undefined;

export const getOrgRepository = (): OrgRepository =>
  (repo ??= pickStore<OrgRepository>('org', {
    postgres: () => new PgOrgRepository(),
    mongo: () => new MongoOrgRepository(),
  }));

/** Replaces the repository (tests, container wiring); no argument restores the default for the selected store. */
export const setOrgRepository = (r?: OrgRepository): void => {
  repo = r;
};
