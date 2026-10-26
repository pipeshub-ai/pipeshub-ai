import { MongoDelegationRepository } from './mongo';
import type { DelegationRepository } from './types';
import { pickStore } from '../store-select';

export * from './types';

let repo: DelegationRepository | undefined;

export const getDelegationRepository = (): DelegationRepository =>
  (repo ??= pickStore<DelegationRepository>('delegation', {
    mongo: () => new MongoDelegationRepository(),
  }));

/** Replaces the repository (tests, container wiring); no argument restores the Mongo one. */
export const setDelegationRepository = (r?: DelegationRepository): void => {
  repo = r;
};
