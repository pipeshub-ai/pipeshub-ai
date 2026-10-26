import { MongoGroupRepository } from './mongo';
import type { GroupRepository } from './types';

export * from './types';

let repo: GroupRepository | undefined;

export const getGroupRepository = (): GroupRepository =>
  (repo ??= new MongoGroupRepository());

/** Replaces the repository (tests, container wiring); no argument restores the default for the selected store. */
export const setGroupRepository = (r?: GroupRepository): void => {
  repo = r;
};
