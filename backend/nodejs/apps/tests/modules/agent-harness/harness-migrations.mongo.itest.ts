/**
 * D2 MG-1..MG-7 and C-15d on a real replica set (run instructions in
 * execution-store.mongo.itest.ts). Works in its own database, `pxh_d2_<time>_<pid>` by
 * default, through the driver, so Mongoose's own index building cannot hide
 * a migration gap.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose, { Connection, Model } from 'mongoose';
import { Db, MongoClient } from 'mongodb';
import {
  HARNESS_MIGRATIONS,
  migrateHarnessIndexes,
  revertHarnessIndexes,
} from '../../../src/libs/repositories/agent-harness/migrations/mongo';
import {
  IndexDefinition,
  indexLabel,
} from '../../../src/libs/utils/mongo-index-migration';
import { migrateHarnessStore } from '../../../src/libs/repositories/agent-harness/journal-startup/mongo';
import * as executionModels from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import * as journalModels from '../../../src/libs/repositories/agent-harness/journal/schema/journal.schema';
import * as auditModels from '../../../src/libs/repositories/agent-harness/audit/schema/audit.schema';
import * as dataProtectionModels from '../../../src/libs/repositories/agent-harness/data-protection/schema/data-protection.schema';
import * as erasureModels from '../../../src/libs/repositories/agent-harness/session-erasure/schema/session-erasure.schema';
import * as workspaceModels from '../../../src/libs/repositories/agent-harness/workspace/schema/workspace.schema';
import * as livenessModels from '../../../src/libs/repositories/agent-harness/control-plane-liveness/mongo';
import * as budgetModels from '../../../src/libs/repositories/agent-harness/budget-ledger/schema/budget-ledger.schema';
import * as sessionForkModels from '../../../src/libs/repositories/agent-harness/session-forks/schema/session-forks.schema';
import * as scheduleModels from '../../../src/libs/repositories/agent-harness/schedules/schema/schedules.schema';
import * as memoryModels from '../../../src/libs/repositories/agent-harness/memory/schema/memory.schema';
import * as backfillModels from '../../../src/libs/repositories/agent-harness/erasure-backfill/schema/erasure-backfill.schema';
import * as sandboxSnapshotModels from '../../../src/libs/repositories/agent-harness/sandbox-snapshots/schema/sandbox-snapshots.schema';
import * as inboxModels from '../../../src/libs/repositories/agent-harness/inbox/schema/inbox.schema';
import { harnessMongoUri } from './support/harness-mongo';
import { AUDIT_PENDING } from '../../../src/libs/repositories/agent-harness/audit-linking/mongo';

// Per process: the lanes share one MongoDB, and a fixed name let one gate drop the database another was migrating.
const DATABASE = process.env.D2_IT_DB ?? `pxh_d2_${Date.now()}_${process.pid}`;

const harnessModels = (): Array<Model<unknown>> =>
  [
    executionModels,
    journalModels,
    auditModels,
    dataProtectionModels,
    erasureModels,
    workspaceModels,
    livenessModels,
    budgetModels,
    sessionForkModels,
    scheduleModels,
    memoryModels,
    backfillModels,
    sandboxSnapshotModels,
    inboxModels,
  ].flatMap((module) =>
    Object.values(module).filter(
      (value): value is Model<unknown> =>
        typeof value === 'function' &&
        typeof (value as Partial<Model<unknown>>).modelName === 'string',
    ),
  );

const expected = (): IndexDefinition[] =>
  HARNESS_MIGRATIONS.flatMap((migration) => [...migration.indexes]);

const collections = (): string[] => [
  ...new Set(expected().map((index) => index.collection)),
];

/** `collection.name` → what the index enforces, for every non-_id index present. */
async function actualIndexes(db: Db): Promise<Map<string, string>> {
  const found = new Map<string, string>();
  for (const collection of collections()) {
    const exists =
      (await db.listCollections({ name: collection }).toArray()).length > 0;
    if (!exists) continue;
    for (const index of await db.collection(collection).indexes()) {
      if (index.name === '_id_') continue;
      found.set(
        `${collection}.${String(index.name)}`,
        JSON.stringify({
          key: Object.entries(index.key),
          unique: index.unique === true,
          partialFilterExpression: index.partialFilterExpression ?? null,
          expireAfterSeconds: index.expireAfterSeconds ?? null,
        }),
      );
    }
  }
  return found;
}

const declared = (): Map<string, string> =>
  new Map(
    expected().map((index) => [
      indexLabel(index),
      JSON.stringify({
        key: Object.entries(index.key),
        unique: index.unique === true,
        partialFilterExpression: index.partialFilterExpression ?? null,
        expireAfterSeconds: index.expireAfterSeconds ?? null,
      }),
    ]),
  );

const sorted = (map: Map<string, string>): Array<[string, string]> =>
  [...map.entries()].sort(([a], [b]) => a.localeCompare(b));

/**
 * Every collection but the pending audit appends: those are on their way onto
 * the chain, and their migration's down puts them there (PERFC-30).
 */
const seededCollections = (): string[] => collections().filter((collection) => collection !== AUDIT_PENDING);

/** One document per collection, each distinct on every unique key. */
async function seed(db: Db): Promise<number> {
  for (const [i, collection] of seededCollections().entries()) {
    await db.collection(collection).insertOne({
      orgId: `org-${i}`,
      runId: `run-${i}`,
      sessionId: `session-${i}`,
      streamId: `stream-${i}`,
      workspaceId: `ws-${i}`,
      scopeKey: `scope-${i}`,
      commandId: `command-${i}`,
      seq: i,
      eventSeq: i,
      version: i,
      revision: i,
      status: 'completed',
      createdAt: new Date(0),
    });
  }
  return seededCollections().length;
}

const documents = async (db: Db): Promise<number> => {
  let total = 0;
  for (const collection of seededCollections()) {
    total += await db.collection(collection).countDocuments();
  }
  return total;
};

describe('D2 harness index migrations on a MongoDB replica set', function () {
  this.timeout(120_000);
  let client: MongoClient;
  let db: Db;

  before(async () => {
    client = await MongoClient.connect(harnessMongoUri());
    db = client.db(DATABASE);
  });
  beforeEach(async () => {
    await db.dropDatabase();
  });
  after(async () => {
    await db.dropDatabase();
    await client.close();
  });

  it('MG-1 + MG-2 (C-15d): up builds exactly the declared set, once', async () => {
    const up = await migrateHarnessIndexes(db);
    expect(up.created).to.have.length(expected().length);
    expect(sorted(await actualIndexes(db))).to.deep.equal(sorted(declared()));
    expect((await migrateHarnessIndexes(db)).created).to.deep.equal([]);
  });

  it('MG-3 + MG-4 (C-15d): down drops what up built and keeps documents; up again', async () => {
    const up = await migrateHarnessIndexes(db);
    const seeded = await seed(db);

    const down = await revertHarnessIndexes(db);
    expect([...down.dropped].sort()).to.deep.equal([...up.created].sort());
    expect(await actualIndexes(db)).to.have.property('size', 0);
    expect(await documents(db)).to.equal(seeded);
    expect((await revertHarnessIndexes(db)).dropped).to.deep.equal([]);

    const again = await migrateHarnessIndexes(db);
    expect([...again.created].sort()).to.deep.equal([...up.created].sort());
    expect(sorted(await actualIndexes(db))).to.deep.equal(sorted(declared()));
    expect(await documents(db)).to.equal(seeded);
  });

  describe('through Mongoose with the platform connection options', () => {
    let connection: Connection;
    beforeEach(async () => {
      connection = await mongoose
        .createConnection(harnessMongoUri(), {
          dbName: DATABASE,
          autoCreate: true,
          autoIndex: true,
        })
        .asPromise();
    });
    afterEach(async () => {
      await connection.close();
    });

    const bind = (): Array<Model<unknown>> =>
      harnessModels().map(
        (model): Model<unknown> =>
          (connection.models[model.modelName] as Model<unknown> | undefined) ??
          (connection.model(model.modelName, model.schema) as Model<unknown>),
      );

    it('MG-5: up finds the indexes the base release built (default names) and builds none', async () => {
      await Promise.all(bind().map((model) => model.createIndexes()));
      expect(sorted(await actualIndexes(db))).to.deep.equal(sorted(declared()));
      expect((await migrateHarnessIndexes(db)).created).to.deep.equal([]);
    });

    it('MG-7: the harness models build no index of their own', async () => {
      await Promise.all(bind().map((model) => model.init()));
      expect(await actualIndexes(db)).to.have.property('size', 0);
    });
  });

  it('MG-6: the startup step runs every registered migration', async () => {
    const result = await migrateHarnessStore(db);
    expect(result.createdIndexes).to.have.length(expected().length);
    expect(sorted(await actualIndexes(db))).to.deep.equal(sorted(declared()));
    expect((await migrateHarnessStore(db)).createdIndexes).to.deep.equal([]);
  });
});
