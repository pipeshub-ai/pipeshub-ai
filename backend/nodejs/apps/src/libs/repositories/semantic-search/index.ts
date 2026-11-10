import { MongoSemanticSearchRepository } from './mongo';
import { PgSemanticSearchRepository } from './pg';
import { pickStore } from '../store-select';
import type { SemanticSearchRepository } from './types';

export * from './types';

let current: SemanticSearchRepository | undefined;

export const getSemanticSearchRepository = (): SemanticSearchRepository =>
  (current ??= pickStore<SemanticSearchRepository>('semantic-search', {
    postgres: () => new PgSemanticSearchRepository(),
    mongo: () => new MongoSemanticSearchRepository(),
  }));

/** Replaces the repository (tests); no argument restores the default. */
export const setSemanticSearchRepository = (
  r?: SemanticSearchRepository,
): void => {
  current = r;
};
