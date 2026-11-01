import mongoose, { ClientSession } from 'mongoose';
import { UniqueConstraintError, VersionConflictError } from './errors';
import {
  FENCE_COLLECTION,
  WRITE_TIMEOUT_MS,
  assertMongoWritable,
  mongoFenceArmed,
  mongoFenceInstalled,
} from './fence';
import { ServiceUnavailableError } from '../errors/http.errors';
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

const TRANSIENT_RETRY_MS = 120_000; // the driver's own limit for withTransaction

const labelled = (e: unknown, label: string): boolean =>
  (e as { hasErrorLabel?: (l: string) => boolean }).hasErrorLabel?.(label) ===
  true;

/**
 * COMMIT with a client-side deadline. `maxCommitTimeMS` bounds the server's work, not a stall in front of it, so at the
 * deadline the session is killed on the server (which aborts a transaction that has not committed) before reporting.
 */
export async function commitWithin(
  session: ClientSession,
  ms: number,
): Promise<void> {
  let timer: NodeJS.Timeout | undefined;
  const commit = session.commitTransaction();
  commit.catch(() => undefined);
  const expired = new Promise<'expired'>((resolve) => {
    timer = setTimeout(() => resolve('expired'), ms);
  });
  try {
    if (
      (await Promise.race([commit.then(() => 'done' as const), expired])) ===
      'done'
    )
      return;
  } finally {
    clearTimeout(timer);
  }
  await mongoose.connection.db
    ?.admin()
    .command(
      { killSessions: session.id ? [session.id] : [] },
      { timeoutMS: WRITE_TIMEOUT_MS },
    )
    .catch(() => undefined);
  throw new ServiceUnavailableError(
    'The commit did not finish in time; its outcome is unknown',
  );
}

/**
 * `withTransaction` for an armed fence. Each write was fenced when it was sent, but COMMIT is not a write, and the
 * driver retries an unknown commit result on its own, long after a freeze. So: the fence is read again just before
 * every commit attempt, a commit is retried only for 5 s after it was first sent, and an expired one is reported (its
 * outcome is unknown) instead of being retried. The whole transaction is re-run on a transient error, as the driver does.
 */
async function fencedTransaction<T>(
  session: ClientSession,
  body: () => Promise<T>,
  writes: boolean,
): Promise<T> {
  const started = Date.now();
  const fence = async (): Promise<void> => {
    const db = mongoose.connection.db;
    // no connection: the commit itself will fail
    if (writes && db) await assertMongoWritable(db);
  };
  for (;;) {
    session.startTransaction({ maxCommitTimeMS: WRITE_TIMEOUT_MS });
    let out: T;
    try {
      out = await body();
      await fence();
    } catch (e) {
      await session.abortTransaction().catch(() => undefined);
      if (
        labelled(e, 'TransientTransactionError') &&
        Date.now() - started < TRANSIENT_RETRY_MS
      )
        continue;
      throw e;
    }
    const commitSent = Date.now();
    for (;;) {
      try {
        await commitWithin(session, WRITE_TIMEOUT_MS);
        return out;
      } catch (e) {
        if (
          labelled(e, 'UnknownTransactionCommitResult') &&
          Date.now() - commitSent < WRITE_TIMEOUT_MS
        ) {
          await fence();
          continue;
        }
        if (
          labelled(e, 'TransientTransactionError') &&
          Date.now() - started < TRANSIENT_RETRY_MS
        )
          break;
        throw e;
      }
    }
  }
}

/**
 * Transaction when the deployment is a replica set, otherwise today's
 * non-transactional path (`rsAvailable` is `REPLICA_SET_AVAILABLE`).
 */
export class MongoUnitOfWork implements UnitOfWork {
  constructor(private readonly rsAvailable: boolean) {}

  async run<T>(
    fn: (tx: Tx) => Promise<T>,
    opts?: { readOnly?: boolean },
  ): Promise<T> {
    try {
      if (!this.rsAvailable) {
        return await fn(new MongoTx());
      }
      const session = await mongoose.startSession();
      try {
        const body = (): Promise<T> => fn(new MongoTx(session));
        if (!mongoFenceArmed() || !mongoFenceInstalled())
          return await session.withTransaction(body);
        return await fencedTransaction(session, body, !opts?.readOnly);
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

export const DRAIN_CACHE_MS = 3_000;
let drainSeen: { at: number; draining: boolean } | undefined;

/**
 * Whether the cutover tooling asked for no new chat turns (fence mode `draining`). Cached for a few seconds, so a chat turn pays
 * one read per few seconds; an unreadable fence is not a reason to refuse a turn (the write fence has its own answer).
 */
export async function mongoDrainRequested(): Promise<boolean> {
  const now = Date.now();
  if (drainSeen && now - drainSeen.at < DRAIN_CACHE_MS)
    return drainSeen.draining;
  let draining: boolean;
  try {
    const doc = await mongoose.connection.db
      ?.collection<{ _id: string; mode?: string }>(FENCE_COLLECTION)
      .findOne(
        { _id: 'fence' },
        { readPreference: 'primary', timeoutMS: 2_000 },
      );
    // every mode but `active` refuses the turn: after `draining` the writes are refused too, so a stream would only fail late
    draining = doc?.mode !== undefined && doc.mode !== 'active';
  } catch {
    draining = false;
  }
  drainSeen = { at: now, draining };
  return draining;
}
