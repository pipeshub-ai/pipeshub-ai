import { MongoGroupRepository } from './mongo';
import { PgGroupRepository } from './pg';
import type { GroupRepository } from './types';
import { pickStore } from '../store-select';

export * from './types';

let repo: GroupRepository | undefined;

export const getGroupRepository = (): GroupRepository =>
  (repo ??= pickStore<GroupRepository>('group', {
    postgres: () => new PgGroupRepository(),
    mongo: () => new MongoGroupRepository(),
  }));

/** Replaces the repository (tests, container wiring); no argument restores the default for the selected store. */
export const setGroupRepository = (r?: GroupRepository): void => {
  repo = r;
};
