import { MongoProjectRepository } from './mongo';
import type { ProjectRepository } from './types';
import { pickStore } from '../store-select';

export * from './types';

let repo: ProjectRepository | undefined;

export const getProjectRepository = (): ProjectRepository =>
  (repo ??= pickStore<ProjectRepository>('project', {
    mongo: () => new MongoProjectRepository(),
  }));

/** Replaces the repository (tests, container wiring); no argument restores the Mongo one. */
export const setProjectRepository = (r?: ProjectRepository): void => {
  repo = r;
};
