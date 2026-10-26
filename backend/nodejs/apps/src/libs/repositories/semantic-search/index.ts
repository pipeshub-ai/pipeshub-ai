import { MongoSemanticSearchRepository } from './mongo';
import type { SemanticSearchRepository } from './types';

export * from './types';

let current: SemanticSearchRepository | undefined;

export const getSemanticSearchRepository = (): SemanticSearchRepository =>
  (current ??= new MongoSemanticSearchRepository());

/** Replaces the repository (tests); no argument restores the default. */
export const setSemanticSearchRepository = (
  r?: SemanticSearchRepository,
): void => {
  current = r;
};
