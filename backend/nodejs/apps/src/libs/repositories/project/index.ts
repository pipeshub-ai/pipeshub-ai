import { MongoProjectRepository } from './mongo';
import type { ProjectRepository } from './types';

export * from './types';

let repo: ProjectRepository | undefined;

export const getProjectRepository = (): ProjectRepository =>
  (repo ??= new MongoProjectRepository());

/** Replaces the repository (tests, container wiring); no argument restores the Mongo one. */
export const setProjectRepository = (r?: ProjectRepository): void => {
  repo = r;
};
