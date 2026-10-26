import { MongoOrgRepository } from './mongo';
import type { OrgRepository } from './types';

export * from './types';

let repo: OrgRepository | undefined;

export const getOrgRepository = (): OrgRepository =>
  (repo ??= new MongoOrgRepository());

/** Replaces the repository (tests, container wiring); no argument restores the default for the selected store. */
export const setOrgRepository = (r?: OrgRepository): void => {
  repo = r;
};
