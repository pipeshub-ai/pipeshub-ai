import { MongoConnectorsConfigRepository } from './mongo';
import type { ConnectorsConfigRepository } from './types';
import { pickStore } from '../store-select';

export * from './types';

let repo: ConnectorsConfigRepository | undefined;

export const getConnectorsConfigRepository = (): ConnectorsConfigRepository =>
  (repo ??= pickStore<ConnectorsConfigRepository>('connectors-config', {
    mongo: () => new MongoConnectorsConfigRepository(),
  }));

/** Replaces the repository (tests, container wiring); no argument restores the Mongo one. */
export const setConnectorsConfigRepository = (
  r?: ConnectorsConfigRepository,
): void => {
  repo = r;
};
