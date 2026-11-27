import mongoose from 'mongoose';
import {
  MongoError,
  MongoNetworkError,
  MongoNotConnectedError,
  MongoServerError,
  MongoServerSelectionError,
  MongoTopologyClosedError,
  type Db,
} from 'mongodb';
import { JournalCapability } from '../../../../modules/agent-harness/config';
import { AgentRunModel } from '../execution/schema/execution.schema';
import { HARNESS_MIGRATIONS, migrateHarnessIndexes } from '../migrations/mongo';
import type { HarnessMigration } from '../../../utils/mongo-index-migration';
import { mongoHello } from '../../mongo';
import { docStore } from '../../store-select';

/** The pre-P0.6 idempotency namespace; admission receipts replaced it. */
const LEGACY_RUN_INDEX_KEY = { orgId: 1, sessionId: 1, idempotencyKey: 1 };
/** Written by the pre-P0.6 relay; nothing reads it now. */
export const LEGACY_DISPATCH_OUTBOX = 'agentDispatchOutbox';
const DURABILITY_PROBE_COLLECTION = 'agentHarnessProbes';
const DURABILITY_PROBE_TIMEOUT_MS = 5_000;

export interface HarnessStoreMigration {
  droppedIndexes: string[];
  /** `collection.index` built by this boot's migrations; empty once up to date. */
  createdIndexes: string[];
  /** Left in place for forensics; never dispatched. */
  orphanedDispatchRows: number;
}

export const database = (): Db => {
  const db = mongoose.connection.db;
  if (!db) throw new MongoNotConnectedError('MongoDB is not connected');
  return db;
};

/**
 * A boot step that migrates the harness's Mongo collections (indexes, or documents stored before a fix). Under
 * `DOC_STORE=postgres` it answers `none`: the migrate job owns the tables and indexes, and no harness data moves
 * from MongoDB (backend/db/README.md "No data move"), so there is nothing to migrate and no Mongo connection to ask.
 * `db` reads the connection when the step asks for it, inside the promise: a missing one fails the step, never the
 * boot that started it.
 */
export async function mongoBootMigration<T>(
  none: NoInfer<T>,
  migrate: (db: () => Db) => Promise<T>,
): Promise<T> {
  if (docStore() === 'postgres') return none;
  return migrate(database);
}

// No primary to write to: shutting down, stepped down, not writable, interrupted by an election.
const NO_PRIMARY_CODES: ReadonlySet<number> = new Set([
  91, 189, 10107, 11600, 11602, 13435,
]);

/** Whether a writable primary answers; false on any error (no primary is reachable). */
export async function writablePrimary(db?: Db): Promise<boolean> {
  try {
    const hello = await mongoHello(db ?? database());
    return hello['isWritablePrimary'] === true;
  } catch {
    return false;
  }
}

/** MongoDB is away for the whole application, not refusing the harness alone; read through wrapping errors' `cause`. */
export const storeUnreachable = (error: unknown): boolean =>
  error instanceof MongoNetworkError ||
  error instanceof MongoServerSelectionError ||
  error instanceof MongoNotConnectedError ||
  error instanceof MongoTopologyClosedError ||
  (error instanceof MongoServerError &&
    typeof error.code === 'number' &&
    NO_PRIMARY_CODES.has(error.code)) ||
  (error instanceof Error &&
    storeUnreachable((error as { cause?: unknown }).cause));

// Labels the driver puts on an error a retry of the same command can answer (a failover, a reset pool).
const TRANSIENT_LABELS = [
  'TransientTransactionError',
  'RetryableWriteError',
  'ResetPool',
] as const;

/**
 * Whether a retry can answer `error` (CH-06): the store is away, or the driver labelled it transient. A
 * route answers it 503 so the worker and the chat retry it, instead of 500 `INTERNAL_ERROR`.
 */
export const storeTransient = (error: unknown): boolean =>
  storeUnreachable(error) ||
  (error instanceof MongoError &&
    TRANSIENT_LABELS.some((label) => error.hasErrorLabel(label))) ||
  (error instanceof Error &&
    storeTransient((error as { cause?: unknown }).cause));

/** The 503 a route answers for a transient store error; the driver's message never leaves the server. */
export const STORE_UNAVAILABLE_MESSAGE =
  'The agent store is not reachable right now. Try again shortly.';

const sameKey = (
  left: Record<string, unknown>,
  right: Record<string, unknown>,
): boolean => JSON.stringify(left) === JSON.stringify(right);

const collectionExists = async (db: Db, name: string): Promise<boolean> =>
  (await db.listCollections({ name }, { nameOnly: true }).toArray()).length > 0;

/**
 * Idempotent startup step: drops the legacy unique run index (it would
 * reject a second run in a session that reuses a client idempotency key),
 * reports orphaned v1 dispatch rows, and runs every registered index
 * migration (the journal's gap-free seq relies on their uniqueness).
 */
export async function migrateHarnessStore(
  db: Db = database(),
  migrations: readonly HarnessMigration[] = HARNESS_MIGRATIONS,
): Promise<HarnessStoreMigration> {
  const droppedIndexes: string[] = [];
  if (await collectionExists(db, AgentRunModel.collection.collectionName)) {
    const runs = db.collection(AgentRunModel.collection.collectionName);
    for (const index of await runs.indexes()) {
      if (
        index.name !== undefined &&
        sameKey(index.key, LEGACY_RUN_INDEX_KEY)
      ) {
        try {
          await runs.dropIndex(index.name);
          droppedIndexes.push(index.name);
        } catch (error) {
          // IndexNotFound: another replica dropped it first.
          if ((error as { code?: number }).code !== 27) throw error;
        }
      }
    }
  }
  const orphanedDispatchRows = (await collectionExists(
    db,
    LEGACY_DISPATCH_OUTBOX,
  ))
    ? await db.collection(LEGACY_DISPATCH_OUTBOX).estimatedDocumentCount()
    : 0;
  const { created: createdIndexes } = await migrateHarnessIndexes(
    db,
    migrations,
  );
  return { droppedIndexes, createdIndexes, orphanedDispatchRows };
}

/**
 * Whether journal commits can be majority-acknowledged here. A standalone
 * server has no majority; a set with an arbiter (PSA) acknowledges w:1 by
 * default and blocks majority writes when its secondary is down, so both
 * report `durable: false` and the harness stays off [R-4, R-7].
 */
export async function probeJournalDurability(
  db: Db = database(),
  timeoutMs = DURABILITY_PROBE_TIMEOUT_MS,
): Promise<JournalCapability> {
  const hello = await mongoHello(db);
  if (typeof hello['setName'] !== 'string') {
    return { durable: false, reason: 'standalone' };
  }
  const arbiters = hello['arbiters'] as unknown;
  if (Array.isArray(arbiters) && arbiters.length > 0) {
    return { durable: false, reason: 'arbiter' };
  }
  try {
    await db
      .collection<{ _id: string; at: Date }>(DURABILITY_PROBE_COLLECTION)
      .updateOne(
        { _id: 'journal-durability' },
        { $set: { at: new Date() } },
        {
          upsert: true,
          writeConcern: { w: 'majority', wtimeoutMS: timeoutMs },
        },
      );
  } catch {
    return { durable: false, reason: 'majority_unavailable' };
  }
  return { durable: true, reason: 'majority' };
}
