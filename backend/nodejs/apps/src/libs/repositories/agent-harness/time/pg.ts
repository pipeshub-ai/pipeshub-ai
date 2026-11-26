import { dbNowMs, type PgKernelTx } from '../pg-documents/pg';

/**
 * Where a PostgreSQL store's lease and deadline predicates get "now": the database's clock, read in the unit that
 * decides, so a Node replica with a skewed or jumped clock can neither steal a live lease nor keep a lapsed one [R-8].
 * The statements take it as a parameter, so a test can fix it as `fixedMongoTime` does for MongoDB.
 */
export interface PgTime {
  /** Epoch milliseconds, read in `k`. */
  now(k: PgKernelTx): Promise<number>;
}

export const pgServerTime: PgTime = { now: dbNowMs };

/** Test double: the database clock reads `now()`. */
export const fixedPgTime = (now: () => number): PgTime => ({
  now: () => Promise.resolve(now()),
});
