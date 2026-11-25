import type { Tx } from '../../uow';
import { requireBoundOrg } from './bound-org';
import { docStore } from '../../store-select';
import { inMongoDurableTransaction } from './mongo';
import { inPgDurableTransaction } from './pg';

export { durableCommits } from './commits';
export type { StoreSession } from './commits';

/**
 * Runs `work` in one durable transaction of the document store (`DOC_STORE`): a majority, snapshot transaction on a
 * MongoDB replica set, a fenced unit of work as the bound organisation on PostgreSQL. `work` may run more than once on
 * a transient error, so it must only write through `tx`. Publishes deferred with `durableCommits` run after the commit.
 *
 * `orgId`: the organisation the transaction is for, on both stores the one its entry point bound (`requireBoundOrg`);
 * a transaction with none bound, or another, is refused before it opens.
 */
export async function inDurableTransaction<T>(
  work: (tx: Tx) => Promise<T>,
  options: DurableTransactionOptions = {},
): Promise<T> {
  const { orgId, ...mongo } = options;
  if (orgId !== undefined) requireBoundOrg(orgId);
  return docStore() === 'postgres'
    ? inPgDurableTransaction(work)
    : inMongoDurableTransaction(work, mongo);
}

export interface DurableTransactionOptions {
  batchWrites?: boolean;
  orgId?: string;
}
