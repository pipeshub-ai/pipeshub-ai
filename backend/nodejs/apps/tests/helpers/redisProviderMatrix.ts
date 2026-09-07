/**
 * Shared list of fake-provider factories that a parametrised contract suite
 * runs against (T4). Node has no contract suite of its own yet (Python's
 * four `test_*_contract.py` files are the template -- see
 * `backend/python/tests/support/redis_provider_matrix.py`), but this module
 * is the equivalent seam: an EE mocha root hook can `require()` this file
 * and call `registerRedisProviderMatrixEntry()` to append a `memorydb`
 * entry before any suite built on `REDIS_PROVIDER_MATRIX` runs, so the same
 * suite exercises it with no OSS test changes.
 */
import {
  createFakeRedisProvider,
  FakeRedisProvider,
} from './fake-redis-provider';

export interface RedisProviderMatrixEntry {
  /** Mocha `describe`/`it` title suffix, e.g. `standalone`, `cluster`, `memorydb`. */
  id: string;
  makeProvider: () => FakeRedisProvider;
}

function standaloneEntry(): RedisProviderMatrixEntry {
  return {
    id: 'standalone',
    makeProvider: () => createFakeRedisProvider(undefined, '', { isCluster: false }),
  };
}

function clusterEntry(): RedisProviderMatrixEntry {
  return {
    id: 'cluster',
    makeProvider: () => createFakeRedisProvider(undefined, '', { isCluster: true }),
  };
}

export const REDIS_PROVIDER_MATRIX: RedisProviderMatrixEntry[] = [
  standaloneEntry(),
  clusterEntry(),
];

/**
 * Append another entry to `REDIS_PROVIDER_MATRIX` (T4). Must run before the
 * suites that iterate the matrix are defined -- mocha builds its test tree
 * by executing every `describe()` body during the collection pass, so a
 * root hook file loaded via `.mocharc.yaml`'s `require:` list (before spec
 * files are required) is early enough.
 */
export function registerRedisProviderMatrixEntry(entry: RedisProviderMatrixEntry): void {
  REDIS_PROVIDER_MATRIX.push(entry);
}

/** Test-only: drop any entries appended beyond the OSS standalone/cluster pair. */
export function resetRedisProviderMatrixForTests(): void {
  REDIS_PROVIDER_MATRIX.length = 0;
  REDIS_PROVIDER_MATRIX.push(standaloneEntry(), clusterEntry());
}
