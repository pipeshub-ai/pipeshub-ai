import { MongoOrgMetricsJobRepository } from './mongo';
import type { OrgMetricsJobRepository } from './types';

export * from './types';

let repo: OrgMetricsJobRepository | undefined;

export const getOrgMetricsJobRepository = (): OrgMetricsJobRepository =>
  (repo ??= new MongoOrgMetricsJobRepository());

/** Replaces the repository (tests, container wiring); no argument restores the default for the selected store. */
export const setOrgMetricsJobRepository = (
  r?: OrgMetricsJobRepository,
): void => {
  repo = r;
};
