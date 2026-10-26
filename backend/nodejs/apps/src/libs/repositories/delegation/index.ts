import { MongoDelegationRepository } from './mongo';
import type { DelegationRepository } from './types';

export * from './types';

let repo: DelegationRepository | undefined;

export const getDelegationRepository = (): DelegationRepository =>
  (repo ??= new MongoDelegationRepository());

/** Replaces the repository (tests, container wiring); no argument restores the Mongo one. */
export const setDelegationRepository = (r?: DelegationRepository): void => {
  repo = r;
};
