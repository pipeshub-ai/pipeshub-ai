import { MongoActivityRepository } from './mongo';
import type { ActivityRepository } from './types';

export * from './types';

let repo: ActivityRepository | undefined;

export const getActivityRepository = (): ActivityRepository =>
  (repo ??= new MongoActivityRepository());

/** Replaces the repository (tests, container wiring); no argument restores the default for the selected store. */
export const setActivityRepository = (r?: ActivityRepository): void => {
  repo = r;
};
