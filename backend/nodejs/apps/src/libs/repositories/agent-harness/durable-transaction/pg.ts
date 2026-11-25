import { PgUnitOfWork, kernelTx } from '../../pg';
import type { Tx } from '../../uow';
import { durableCommits } from './commits';

/**
 * The PostgreSQL durable transaction: one fenced unit of work (READ COMMITTED, the store epoch asserted in its
 * prologue) as the organisation bound in the ambient context. A serialization failure or deadlock re-runs `work`
 * on a new unit, so each attempt starts with no deferred publish; they run once the unit has committed.
 */
export async function inPgDurableTransaction<T>(
  work: (tx: Tx) => Promise<T>,
): Promise<T> {
  let attempt: object | undefined;
  try {
    const value = await new PgUnitOfWork().run(async (tx) => {
      if (attempt !== undefined) durableCommits.discard(attempt);
      attempt = kernelTx(tx);
      durableCommits.begin(attempt);
      return work(tx);
    });
    if (attempt !== undefined) await durableCommits.committed(attempt);
    return value;
  } finally {
    if (attempt !== undefined) durableCommits.discard(attempt);
  }
}
