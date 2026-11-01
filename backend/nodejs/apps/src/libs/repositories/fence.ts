import { Logger } from '../services/logger.service';
import { ServiceUnavailableError } from '../errors/http.errors';
import { DocStoreFencedError, DocStoreReadOnlyError } from './errors';
import { docstoreMetrics } from './metrics';

/**
 * The Mongo half of the store fence. The cutover tooling writes one document,
 * `{ _id: 'fence', mode: 'active' | 'draining' | 'readonly' | 'retired', epoch }`, to the `docstore_fence` collection of the
 * application database; no document means `active`.
 *
 * Every collection write method of the driver is wrapped here, whichever Mongoose model or raw collection issued it
 * (bulk-operation builders, `db.command`, `$merge`/`$out`, `client.bulkWrite` are not: a lint bans them in `src`):
 *  1. it first reads the fence from the primary, with a 2 s deadline for the read, and refuses unless `active`;
 *  2. while the store is being moved (`draining`) it carries `timeoutMS: 5000`, so a write blocked on the server fails (and is not
 *     retried) instead of landing after the fence moved. An `active` store puts no deadline on a write: a long multi-document
 *     write cut off by a client timeout could be left half applied;
 *  3. it carries the server-side `comment` FENCED_WRITE_COMMENT (in a transaction too), so the cutover can see in `$currentOp`
 *     which writes are still running.
 * The server-side revoke of the app user's write role (a later step) is the layer that does not trust this process.
 */
export const FENCE_COLLECTION = 'docstore_fence';
export const FENCE_READ_DEADLINE_MS = 2_000;
export const WRITE_TIMEOUT_MS = 5_000;

/** `draining`: writes still proceed, but no new chat turn starts (see drain.ts). */
type Mode = 'active' | 'draining' | 'readonly' | 'retired';

/** Driver write methods and the position of their options argument. */
const WRITES: Record<string, number> = {
  insertOne: 1,
  insertMany: 1,
  bulkWrite: 1,
  updateOne: 2,
  updateMany: 2,
  replaceOne: 2,
  deleteOne: 1,
  deleteMany: 1,
  findOneAndDelete: 1,
  findOneAndReplace: 2,
  findOneAndUpdate: 2,
  drop: 0,
  rename: 1,
};

interface FenceDb {
  collection(name: string): FenceReader;
}
interface DriverCollection {
  collectionName: string;
  s: { db: FenceDb };
}
interface FenceReader {
  findOne(
    filter: object,
    options: object,
  ): Promise<{ mode?: Mode; epoch?: number } | null>;
}

/** Throws unless the fence says writes may proceed. Reads the primary, never a cache: a cache would be the gap. */
/**
 * An `active` answer is trusted for this long, so a burst of writes pays for one read. A freeze or retirement then reaches a writer
 * at most this much later. Every other answer is read afresh each time.
 *
 * The rule for the cutover tool: after setting `draining`, wait at least FENCE_VISIBLE_MS (read deadline + this trust window), so
 * that every writer has seen the change; then wait until `$currentOp` (allUsers, idleSessions) lists no operation whose
 * `command.comment` starts with FENCED_WRITE_COMMENT; only then set `readonly` or `retired`. Writes started after the first wait
 * see `draining` and carry the 5 s write timeout.
 */
export const ACTIVE_FENCE_TRUST_MS = 500;
const trustMs = (): number =>
  Number(process.env.DOC_STORE_FENCE_TRUST_MS ?? ACTIVE_FENCE_TRUST_MS);
export const FENCE_VISIBLE_MS = FENCE_READ_DEADLINE_MS + ACTIVE_FENCE_TRUST_MS;
/** Every write the fence lets through carries this `comment` (as a prefix of a caller's own string comment). */
export const FENCED_WRITE_COMMENT = 'pipeshub:fenced-write';
let activeUntil = 0;
// reads can finish out of order: a read that started before the last non-`active` answer must not re-trust `active`
let readSeq = 0;
let lastNotActiveSeq = 0;

/** Tests: forget a trusted `active` answer. */
export const forgetFenceCache = (): void => {
  activeUntil = 0;
  lastNotActiveSeq = 0;
};

/**
 * Throws unless writes may proceed; otherwise says which mode allowed it.
 */
export async function assertMongoWritable(
  db: FenceDb,
): Promise<'active' | 'draining'> {
  if (Date.now() < activeUntil) return 'active';
  const seq = ++readSeq;
  const t0 = process.hrtime.bigint();
  let doc: Awaited<ReturnType<FenceReader['findOne']>>;
  try {
    doc = await db
      .collection(FENCE_COLLECTION)
      .findOne(
        { _id: 'fence' },
        { readPreference: 'primary', timeoutMS: FENCE_READ_DEADLINE_MS },
      );
  } catch (e) {
    docstoreMetrics.fenceRefused.inc({ store: 'mongo', reason: 'unreadable' });
    Logger.getInstance({ service: 'MongoFence' }).error(
      'fence unreadable; write refused',
      { error: (e as Error).message },
    );
    throw new ServiceUnavailableError(
      'The document store fence could not be read',
    );
  }
  const elapsedMs = Number(process.hrtime.bigint() - t0) / 1e6;
  docstoreMetrics.fenceCheck.observe({}, elapsedMs / 1000);
  // a read that came back late says nothing about now
  if (elapsedMs > FENCE_READ_DEADLINE_MS) {
    docstoreMetrics.fenceRefused.inc({ store: 'mongo', reason: 'deadline' });
    throw new ServiceUnavailableError('The document store fence read was late');
  }
  const mode = doc?.mode ?? 'active';
  // only `active` is trusted: `draining` still allows writes, so a cached one would let a write through after a move to `readonly`
  if (mode !== 'active') {
    lastNotActiveSeq = Math.max(lastNotActiveSeq, seq);
    activeUntil = 0;
  } else if (seq > lastNotActiveSeq) activeUntil = Date.now() + trustMs();
  if (mode === 'readonly') {
    docstoreMetrics.fenceRefused.inc({ store: 'mongo', reason: 'readonly' });
    throw new DocStoreReadOnlyError();
  }
  if (mode === 'retired') {
    docstoreMetrics.fenceRefused.inc({ store: 'mongo', reason: 'retired' });
    throw new DocStoreFencedError('this store is no longer the live one');
  }
  return mode === 'draining' ? 'draining' : 'active';
}

let installed = false;

/** Whether this process wrapped the driver's writes (`installMongoFence` ran): only then do units of work fence their commits. */
export const mongoFenceInstalled = (): boolean => installed;

/**
 * The Mongo fence is off unless `DOC_STORE_FENCE=on`. Switching it on makes every collection write read a one-document collection
 * first (an `active` answer is trusted for a short time), so that the cutover tooling can freeze the store. It must be on in every
 * process before a move to PostgreSQL starts: an application that still has Mongo open when the old store is retired would
 * otherwise go on writing to it.
 */
export const mongoFenceArmed = (): boolean =>
  ['on', '1', 'true'].includes(
    (process.env.DOC_STORE_FENCE ?? '').trim().toLowerCase(),
  );

/**
 * Wraps the driver's write methods once per process. Safe to call again. The caller passes the driver's `Collection`
 * class (`mongoose.mongo.Collection`) so that Mongoose stays imported only where the ratchet allows it.
 */
export function installMongoFence(collectionClass: {
  prototype: object;
}): void {
  if (installed || !mongoFenceArmed()) return;
  installed = true;
  const proto = collectionClass.prototype as unknown as Record<
    string,
    (this: DriverCollection, ...a: unknown[]) => Promise<unknown>
  >;
  for (const [method, optsAt] of Object.entries(WRITES)) {
    const original = proto[method];
    if (!original) throw new Error(`driver Collection.${method} not found`);
    proto[method] = async function (
      this: DriverCollection,
      ...args: unknown[]
    ) {
      if (this.collectionName === FENCE_COLLECTION)
        return original.apply(this, args);
      const mode = await assertMongoWritable(this.s.db);
      // shortcut: filter/doc may be omitted by callers of deleteOne()/deleteMany(); pad so options land at optsAt
      while (args.length < optsAt) args.push(undefined);
      const opts = (args[optsAt] ?? {}) as {
        timeoutMS?: number;
        comment?: unknown;
        session?: { inTransaction(): boolean };
      };
      // inside a transaction the deadline belongs to the transaction, not to a statement; and only a store that is being moved has one
      const bounded =
        mode === 'draining' &&
        opts.timeoutMS === undefined &&
        !opts.session?.inTransaction();
      // shortcut: a caller's non-string comment is kept as it is and so is not tagged; none exists in `src`
      const comment =
        opts.comment === undefined
          ? FENCED_WRITE_COMMENT
          : typeof opts.comment === 'string'
            ? `${FENCED_WRITE_COMMENT} ${opts.comment}`
            : opts.comment;
      args[optsAt] = {
        ...opts,
        comment,
        ...(bounded ? { timeoutMS: WRITE_TIMEOUT_MS } : {}),
      };
      return original.apply(this, args);
    };
  }
}
