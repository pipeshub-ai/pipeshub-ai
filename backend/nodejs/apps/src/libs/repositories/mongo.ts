import mongoose, { ClientSession } from 'mongoose';
import { UniqueConstraintError, VersionConflictError } from './errors';
import type { Tx, UnitOfWork } from './uow';

export class MongoTx implements Tx {
  readonly store = 'mongo' as const;
  constructor(readonly session?: ClientSession) {}
}

/** The session behind a `Tx`, for Mongo repositories only. Undefined outside a transaction. */
export const sessionOf = (tx?: Tx): ClientSession | undefined =>
  // eslint-disable-next-line @typescript-eslint/no-unnecessary-type-assertion
  (tx as MongoTx | undefined)?.session;

/**
 * Typed form of a Mongo driver error; anything else is returned unchanged.
 * Only the key names are kept for a duplicate, never the values (they can be emails).
 */
export function mapMongoError(error: unknown): unknown {
  const e = error as
    | { code?: unknown; name?: unknown; keyPattern?: Record<string, unknown> }
    | null
    | undefined;
  if (e?.code === 11000) {
    return new UniqueConstraintError('Duplicate key', {
      key: Object.keys(e.keyPattern ?? {}).join(','),
    });
  }
  if (e?.name === 'VersionError') {
    return new VersionConflictError('Document was modified concurrently');
  }
  return error;
}

/**
 * Transaction when the deployment is a replica set, otherwise today's
 * non-transactional path (`rsAvailable` is `REPLICA_SET_AVAILABLE`).
 */
export class MongoUnitOfWork implements UnitOfWork {
  constructor(private readonly rsAvailable: boolean) {}

  async run<T>(fn: (tx: Tx) => Promise<T>): Promise<T> {
    try {
      if (!this.rsAvailable) {
        return await fn(new MongoTx());
      }
      const session = await mongoose.startSession();
      try {
        return await session.withTransaction(() => fn(new MongoTx(session)));
      } finally {
        await session.endSession();
      }
    } catch (error) {
      throw mapMongoError(error);
    }
  }
}

/** Whether a string is a well-formed document id for this store. */
export const isValidId = (id: string): boolean =>
  mongoose.Types.ObjectId.isValid(id);

/** The store's own id object for a well-formed id string, for typed schema fields that still need one. */
export const toDriverId = (id: string): mongoose.Types.ObjectId =>
  new mongoose.Types.ObjectId(id);
