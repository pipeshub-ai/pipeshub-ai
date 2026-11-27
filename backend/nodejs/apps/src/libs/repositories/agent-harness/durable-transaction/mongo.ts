import mongoose, { ClientSession, type CreateOptions } from 'mongoose';
import type { TransactionOptions, WriteConcernSettings } from 'mongodb';
import { MongoTx } from '../../mongo';
import type { Tx } from '../../uow';
import { transactionWrites } from '../transaction-writes/mongo';
import { durableCommits } from './commits';

export { durableCommits } from './commits';
export type { StoreSession } from './commits';

/**
 * Journal, admission and commit transactions: an acknowledged commit survives
 * failover (PSA's implicit w:1 would not), and the transaction reads one
 * snapshot from the primary [R-4, D-G2].
 */
export const DURABLE_TRANSACTION_OPTIONS: TransactionOptions = {
  readPreference: 'primary',
  readConcern: { level: 'snapshot' },
  writeConcern: { w: 'majority' },
};

/** For single-document writes outside a transaction (renew, leader lease). */
export const MAJORITY_WRITE: { writeConcern: WriteConcernSettings } = {
  writeConcern: { w: 'majority' },
};

/** The same for `Model.create`, whose Mongoose 9 options name the write concern at the top level. */
export const MAJORITY_CREATE: CreateOptions = { w: 'majority' };

const handles = new WeakMap<ClientSession, MongoTx>();

/** The unit-of-work handle of a driver session: one per session, so a handle keyed by it stays the same across stores. */
export function mongoTx(session: ClientSession): MongoTx {
  let tx = handles.get(session);
  if (tx === undefined) {
    tx = new MongoTx(session);
    handles.set(session, tx);
  }
  return tx;
}

/**
 * The driver session behind a unit-of-work handle, for the harness's Mongo stores only. Main's unit of work on a
 * standalone server hands out a handle with no session (writes go one by one, as before); a PostgreSQL handle is a
 * wiring fault and fails.
 */
export function mongoSession(tx: Tx): ClientSession {
  if (!('session' in tx))
    throw new Error(
      'a Mongo harness store was given a transaction from another store',
    );
  return (tx as MongoTx).session as ClientSession;
}

/** `inMongoTransaction` with the backend-neutral handle the harness's ports take. */
export function inMongoDurableTransaction<T>(
  work: (tx: Tx) => Promise<T>,
  options: { batchWrites?: boolean } = {},
): Promise<T> {
  return inMongoTransaction((session) => work(mongoTx(session)), options);
}

/**
 * Runs `work` in a durable transaction on the driver session (the harness's Mongo stores). `work` may run more than once
 * on a transient error, so it must only write through `session`. `batchWrites`: the writes `work` queues through
 * `transactionWrites` go in one command at its end, so `work` must not read back what it queued.
 */
export async function inMongoTransaction<T>(
  work: (session: ClientSession) => Promise<T>,
  { batchWrites = false }: { batchWrites?: boolean } = {},
): Promise<T> {
  const session = await mongoose.startSession();
  try {
    let result: { value: T } | undefined;
    await session.withTransaction(async () => {
      durableCommits.begin(session);
      if (batchWrites) await transactionWrites.begin(session);
      const value = await work(session);
      if (batchWrites) await transactionWrites.flush(session);
      result = { value };
    }, DURABLE_TRANSACTION_OPTIONS);
    if (result === undefined) {
      throw new Error('Durable transaction completed without a result');
    }
    await durableCommits.committed(session);
    return result.value;
  } finally {
    durableCommits.discard(session);
    await session.endSession();
  }
}

/**
 * The driver session of a handle a Mongo store method was given: the port's `Tx`, or (from the store's own code) the
 * session itself.
 */
export function asMongoSession(handle: Tx | ClientSession): ClientSession;
export function asMongoSession(
  handle: Tx | ClientSession | undefined,
): ClientSession | undefined;
export function asMongoSession(
  handle: Tx | ClientSession | null,
): ClientSession | null;
export function asMongoSession(
  handle: Tx | ClientSession | null | undefined,
): ClientSession | null | undefined {
  if (handle === null || handle === undefined) return handle;
  return (handle as Partial<Tx>).store === undefined
    ? (handle as ClientSession)
    : mongoSession(handle as Tx);
}
