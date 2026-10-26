import { MongoOrgAuthConfigRepository } from './mongo';
import type { OrgAuthConfigRepository } from './types';

export * from './types';

let repo: OrgAuthConfigRepository | undefined;

export const getOrgAuthConfigRepository = (): OrgAuthConfigRepository =>
  (repo ??= new MongoOrgAuthConfigRepository());

/** Replaces the repository (tests, container wiring); no argument restores the default for the selected store. */
export const setOrgAuthConfigRepository = (
  r?: OrgAuthConfigRepository,
): void => {
  repo = r;
};
