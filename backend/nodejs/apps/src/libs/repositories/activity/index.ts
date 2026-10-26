import { MongoActivityRepository } from './mongo';
import type { ActivityRepository } from './types';
import { pickStore } from '../store-select';

export * from './types';

let repo: ActivityRepository | undefined;

export const getActivityRepository = (): ActivityRepository =>
  (repo ??= pickStore<ActivityRepository>('activity', {
    mongo: () => new MongoActivityRepository(),
  }));

/** Replaces the repository (tests, container wiring); no argument restores the default for the selected store. */
export const setActivityRepository = (r?: ActivityRepository): void => {
  repo = r;
};
