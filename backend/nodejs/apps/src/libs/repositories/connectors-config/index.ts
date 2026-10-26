import { MongoConnectorsConfigRepository } from './mongo';
import type { ConnectorsConfigRepository } from './types';

export * from './types';

let repo: ConnectorsConfigRepository | undefined;

export const getConnectorsConfigRepository = (): ConnectorsConfigRepository =>
  (repo ??= new MongoConnectorsConfigRepository());

/** Replaces the repository (tests, container wiring); no argument restores the Mongo one. */
export const setConnectorsConfigRepository = (
  r?: ConnectorsConfigRepository,
): void => {
  repo = r;
};
