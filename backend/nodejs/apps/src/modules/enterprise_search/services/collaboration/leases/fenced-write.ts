import mongoose, { ClientSession } from 'mongoose';
import { isReplicaSet } from '../../../../../libs/utils/replica-set';
import { LeaseHandle, LeaseLostError } from './lease.types';

/**
 * Runs a terminal write only while the run still holds its lease. On a replica set the renew and
 * `fn` share one transaction; standalone they are sequential and a takeover between them is
 * accepted (74 F3).
 */
export async function fencedWrite<T>(
  lease: LeaseHandle,
  fn: (dbSession: ClientSession | null) => Promise<T>,
): Promise<T> {
  if (!isReplicaSet()) {
    if (!(await lease.renew())) throw new LeaseLostError(lease.runId);
    return fn(null);
  }
  const dbSession = await mongoose.startSession();
  try {
    let result: T | undefined;
    await dbSession.withTransaction(async () => {
      if (!(await lease.renew(dbSession)))
        throw new LeaseLostError(lease.runId);
      result = await fn(dbSession);
    });
    return result as T;
  } finally {
    await dbSession.endSession();
  }
}
