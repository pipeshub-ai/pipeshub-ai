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

/**
 * An `active` answer is trusted for this long, so a burst of writes pays for one read. A freeze or retirement then reaches a writer
 * at most this much later. Every other answer is read afresh each time.
 */
export const ACTIVE_FENCE_TRUST_MS = 500;
let trust = ACTIVE_FENCE_TRUST_MS;
/** Tests only: the trust window is a constant in production, so the cutover's wait cannot drift from it. */
export const setFenceTrustMsForTests = (ms: number): void => {
  trust = ms;
};
const trustMs = (): number => trust;
/** After `draining` is set, every writer has seen it once this long has passed (read deadline + trust window). */
export const FENCE_VISIBLE_MS = FENCE_READ_DEADLINE_MS + ACTIVE_FENCE_TRUST_MS;

/**
 * A write that passed the fence is handed to the driver at once, but the driver may wait before it sends: for a pooled
 * connection (`waitQueueTimeoutMS`), to open one (`connectTimeoutMS`) and to find the primary (`serverSelectionTimeoutMS`), and it
 * may retry a failed write (`retryWrites`). While the fence is armed the client bounds each of these phases (not the server's
 * execution of the write) and does not retry, so the write reaches the server, where `$currentOp` shows it, within
 * FENCE_SEND_MS of passing the fence (an upper bound: the three waits added up).
 */
export const FENCE_PRE_SEND_TIMEOUT_MS = 2_000;
export const FENCE_SEND_MS = 3 * FENCE_PRE_SEND_TIMEOUT_MS;
/** The MongoClient options to merge in while the fence is armed; empty when it is off. */
export const fenceClientOptions = (): {
  waitQueueTimeoutMS?: number;
  connectTimeoutMS?: number;
  serverSelectionTimeoutMS?: number;
  retryWrites?: boolean;
} =>
  mongoFenceArmed()
    ? {
        waitQueueTimeoutMS: FENCE_PRE_SEND_TIMEOUT_MS,
        connectTimeoutMS: FENCE_PRE_SEND_TIMEOUT_MS,
        serverSelectionTimeoutMS: FENCE_PRE_SEND_TIMEOUT_MS,
        retryWrites: false,
      }
    : {};
/**
 * The rule for the cutover tool, after it sets `draining`:
 *  1. wait FENCE_CUTOVER_WAIT_MS (every writer has seen `draining`, and any write that passed the fence earlier has reached the server);
 *  2. poll `$currentOp` ({ allUsers: true, idleSessions: true }, match FENCED_WRITE_OP_MATCH) twice, FENCE_POLL_GAP_MS apart,
 *     and go on only when both polls (and every poll until then) show no such operation;
 *  3. only then set `readonly` or `retired`.
 * Writes that start after step 1 see `draining` and carry the 5 s write timeout.
 */
export const FENCE_CUTOVER_WAIT_MS = FENCE_VISIBLE_MS + FENCE_SEND_MS;
export const FENCE_POLL_GAP_MS = 1_000;
/**
 * Every write the fence lets through carries the `comment` `{ tag: FENCED_WRITE_COMMENT, caller?: <the caller's own comment> }`.
 * In `$currentOp` match it with FENCED_WRITE_OP_MATCH, or test a comment with isFencedWriteComment.
 */
export const FENCED_WRITE_COMMENT = 'pipeshub:fenced-write';
export const FENCED_WRITE_OP_MATCH = {
  'command.comment.tag': FENCED_WRITE_COMMENT,
};
export const isFencedWriteComment = (c: unknown): boolean =>
  typeof c === 'object' &&
  c !== null &&
  (c as { tag?: unknown }).tag === FENCED_WRITE_COMMENT;
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
      // the driver sends no `comment` with these two commands, so the cutover could not see them in `$currentOp`
      if (mode === 'draining' && (method === 'drop' || method === 'rename'))
        throw new DocStoreFencedError(
          `${method} cannot be tagged, so it is refused while the store is being moved`,
        );
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
      const comment = isFencedWriteComment(opts.comment)
        ? opts.comment
        : {
            tag: FENCED_WRITE_COMMENT,
            ...(opts.comment === undefined ? {} : { caller: opts.comment }),
          };
      args[optsAt] = {
        ...opts,
        comment,
        ...(bounded ? { timeoutMS: WRITE_TIMEOUT_MS } : {}),
      };
      return original.apply(this, args);
    };
  }
}
