import { activeUnit } from '../../../db';
import { kernelTx } from '../../pg';
import type { Tx } from '../../uow';
import { orgUnit, type PgKernelTx } from './pg';

/**
 * A per-org store call that a sweep also makes from inside its jobs unit (the anchor job, the fold and alert sweeps,
 * the audit link): in that jobs unit when one is open, else in the caller's unit or as its bound org (`orgUnit`, which
 * refuses a call with no org bound or another org bound). Every statement run through it names `org_id` in its
 * predicate, so the jobs role's wider row policy never widens what the call reads or writes.
 */
export function orgOrJobsUnit<T>(
  tx: Tx | undefined,
  orgId: string,
  _reason: string,
  opts: { write: boolean },
  fn: (k: PgKernelTx) => Promise<T>,
): Promise<T> {
  const open = tx !== undefined ? kernelTx(tx) : activeUnit();
  if (open?.service === 'jobs') return fn(open);
  return orgUnit(tx, orgId, opts, fn);
}
