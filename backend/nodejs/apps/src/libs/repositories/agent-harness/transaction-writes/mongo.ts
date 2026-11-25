import { assertIdFilter, idFieldsOf } from '../../id-filter-guard/mongo';
import mongoose, { ClientSession, Model } from 'mongoose';
import type {
  ApplyBasicCreateCasting,
  DeepPartial,
  Require_id,
} from 'mongoose';
import {
  AnyClientBulkWriteModel,
  MongoClientBulkWriteError,
  MongoServerError,
  type Document,
} from 'mongodb';
import { fencedClientBulkWrite } from '../../fence';
import { mongoHello } from '../../mongo';

/** An update a transaction's batch takes. */
export interface QueuedUpdate {
  name: 'updateOne' | 'updateMany';
  filter: Document;
  update: Document;
  upsert?: boolean;
}

// What Mongoose's own `Connection.bulkWrite` casts its updates with (in place); its typings leave it out.

const { cast: castUpdate } =
  // eslint-disable-next-line @typescript-eslint/no-require-imports -- a Mongoose internal with no typings or ESM entry
  require('mongoose/lib/helpers/model/castBulkWrite') as {
    cast: Record<
      QueuedUpdate['name'],
      (
        model: Model<unknown>,
        op: QueuedUpdate,
        options: object,
        now: Date,
      ) => unknown
    >;
  };

/** MongoDB 8.0's wire version: the first with the client-level `bulkWrite` command. */
export const BULK_WRITE_WIRE_VERSION = 25;

/** Whether the server takes writes to several collections in one command. */
export async function serverHasBulkWrite(): Promise<boolean> {
  const db = mongoose.connection.db;
  if (db === undefined) return false;
  const hello = await mongoHello(db);
  return Number(hello['maxWireVersion']) >= BULK_WRITE_WIRE_VERSION;
}

/**
 * The inserts (PERF-E2) and updates (PERF-E3) of one transaction that
 * nothing in it reads back, sent to MongoDB in one `bulkWrite` command
 * instead of one command each.
 * A store opens a batch on its transaction's session and flushes it last in
 * the transaction; the adapters it calls queue through here, and write at
 * once, as they always did, when no batch is open. A queued document is cast,
 * defaulted and validated by its model when it is queued, as `create` does,
 * so what is stored and what is refused do not change. A server before 8.0
 * has no such command: it is asked once per process and every write goes on
 * its own.
 */
export class TransactionWrites {
  private readonly open = new WeakMap<
    ClientSession,
    AnyClientBulkWriteModel<Document>[]
  >();
  private readonly kept = new WeakMap<ClientSession, Map<string, unknown>>();
  private supported: Promise<boolean> | null = null;

  constructor(
    private readonly probe: () => Promise<boolean> = serverHasBulkWrite,
  ) {}

  /**
   * Opens an empty batch on `session` when the server can take one; a retried
   * transaction starts again from nothing. An unanswered probe is asked again
   * next time.
   */
  async begin(session: ClientSession): Promise<void> {
    this.open.delete(session);
    this.kept.delete(session);
    this.supported ??= this.probe().catch(() => {
      this.supported = null;
      return false;
    });
    if (await this.supported) {
      this.open.set(session, []);
      this.kept.set(session, new Map());
    }
  }

  /**
   * What this attempt of `session`'s transaction kept under `key` (a document
   * it wrote, as it left it). Undefined without a batch open: a retried
   * attempt starts with nothing kept.
   */
  // eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- the caller names the shape it kept under the key
  recall<T>(session: ClientSession, key: string): T | undefined {
    return this.kept.get(session)?.get(key) as T | undefined;
  }

  keep(session: ClientSession, key: string, value: unknown): void {
    this.kept.get(session)?.set(key, value);
  }

  /** `write` is the same insert sent on its own, for a session with no batch open. */
  async insert<T>(
    session: ClientSession | null,
    model: Model<T>,
    documents: readonly object[],
    write: () => Promise<unknown>,
  ): Promise<void> {
    const queued = session === null ? undefined : this.open.get(session);
    if (queued === undefined) {
      await write();
      return;
    }
    const namespace = `${model.db.name}.${model.collection.collectionName}`;
    for (const document of documents) {
      queued.push({
        name: 'insertOne',
        namespace,
        document: await stored(model, document),
      });
    }
  }

  /** An insert into a collection no model describes: the document is stored as given. */
  async insertRaw(
    session: ClientSession,
    collection: string,
    document: Document,
    write: () => Promise<unknown>,
  ): Promise<void> {
    const queued = this.open.get(session);
    if (queued === undefined) {
      await write();
      return;
    }
    queued.push({
      name: 'insertOne',
      namespace: `${mongoose.connection.name}.${collection}`,
      document,
    });
  }

  /**
   * An update, cast as `model.updateOne` / `updateMany` cast it (timestamps,
   * defaults on insert) when it is queued (PERF-E3). Only for documents
   * nothing later in the transaction reads, and where nothing depends on how
   * many matched: a queued update reports nothing.
   */
  async update<T>(
    session: ClientSession | null,
    model: Model<T>,
    op: QueuedUpdate,
    write: () => Promise<unknown>,
  ): Promise<void> {
    // A queued update goes to the driver, past the model's query middleware.
    assertIdFilter(op.filter, idFieldsOf(model.schema));
    const queued = session === null ? undefined : this.open.get(session);
    if (queued === undefined) {
      await write();
      return;
    }
    const cast = { ...op };
    castUpdate[op.name](
      model as unknown as Model<unknown>,
      cast,
      {},
      model.base.now(),
    );
    queued.push({
      ...cast,
      namespace: `${model.db.name}.${model.collection.collectionName}`,
    });
  }

  /**
   * Sends what `session` queued, in order, and closes its batch. A failure is
   * thrown as the one write that failed would have thrown it on its own, so a
   * duplicate key is still code 11000 and a write conflict still carries the
   * label that has the driver retry the transaction.
   */
  async flush(session: ClientSession): Promise<void> {
    const queued = this.open.get(session);
    this.open.delete(session);
    this.kept.delete(session);
    if (queued === undefined || queued.length === 0) return;
    try {
      const db = mongoose.connection.db;
      if (db === undefined) throw new Error('MongoDB is not connected');
      await fencedClientBulkWrite(mongoose.connection.getClient(), db, queued, {
        session,
        ordered: true,
      });
    } catch (error) {
      throw asSingleWriteError(error);
    }
  }
}

/** What `Model.create` would store for `document`: cast, defaulted, timestamped, versioned. */
async function stored<T>(model: Model<T>, document: object): Promise<Document> {
  const doc = document instanceof model ? document : new model(document);
  const options = model.schema.options;
  // Mongoose's own bulkWrite does this; its typings leave the method out.
  if (options.timestamps) {
    (doc as unknown as { initializeTimestamps(): void }).initializeTimestamps();
  }
  await doc.validate();
  const bson = doc.toBSON() as Document;
  const versionKey = options.versionKey;
  if (typeof versionKey === 'string' && bson[versionKey] === undefined) {
    bson[versionKey] = 0;
  }
  return bson;
}

export function asSingleWriteError(error: unknown): unknown {
  if (!(error instanceof MongoClientBulkWriteError)) return error;
  if (error.cause instanceof MongoServerError) return error.cause;
  const first = [...error.writeErrors.values()][0];
  if (first === undefined) return error;
  return new MongoServerError({
    ...first,
    message: first.message,
    errmsg: first.message,
  });
}

export const transactionWrites = new TransactionWrites();

/** `model.create(documents)` in `session`'s transaction, queued when it has a batch open. */
export const createInTransaction = <T>(
  session: ClientSession,
  model: Model<T>,
  documents: readonly object[],
): Promise<void> =>
  transactionWrites.insert(session, model, documents, () =>
    // Mongoose 9 types create()'s documents from T's casting, which a generic T cannot be related to.
    model.create(
      [...documents] as Array<
        DeepPartial<ApplyBasicCreateCasting<Require_id<T>>>
      >,
      {
        session,
        ordered: true,
      },
    ),
  );
