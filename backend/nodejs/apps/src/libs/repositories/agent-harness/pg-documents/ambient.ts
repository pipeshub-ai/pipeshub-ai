import { getCurrentOrgId } from '../../../context/orgContext';
import { activeUnit } from '../../../db';
import { mapPgStoreError } from '../../pg';
import { inUnit } from '../../pg-util';
import { jobsUnit, type PgKernelTx } from './pg';

/**
 * A call keyed by something that names no organisation (a link's token digest, a notice key, an outbox id): in the
 * unit already open, else as the bound organisation (row policies then hide every other org's rows), else, for the
 * background work that has no organisation, as the jobs role.
 */
export async function ambientUnit<T>(
  reason: string,
  opts: { write: boolean },
  fn: (k: PgKernelTx) => Promise<T>,
): Promise<T> {
  const open = activeUnit();
  if (open) return fn(open);
  if (!getCurrentOrgId()) return jobsUnit(reason, opts, fn);
  try {
    return await inUnit(undefined, opts, fn);
  } catch (error) {
    throw mapPgStoreError(error);
  }
}

/**
 * Deployment-wide work (what is due, what expired): in the unit already open, else as the jobs role, so a sweep made
 * from a request still reaches every organisation, as it does on MongoDB.
 */
export function crossOrgUnit<T>(
  reason: string,
  opts: { write: boolean },
  fn: (k: PgKernelTx) => Promise<T>,
): Promise<T> {
  const open = activeUnit();
  return open ? fn(open) : jobsUnit(reason, opts, fn);
}

export { inBoundOrgUnit } from '../durable-transaction/bound-org';
