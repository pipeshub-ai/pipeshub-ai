/**
 * J-9 and the durability probe on a real replica set (run instructions in
 * execution-store.mongo.itest.ts). Set MONGO_STANDALONE_IT_URI to a
 * standalone server to also check that it reports `durable: false`.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose from 'mongoose';
import { MongoClient } from 'mongodb';
import { AgentRunModel } from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import {
  LEGACY_DISPATCH_OUTBOX,
  migrateHarnessStore,
  probeJournalDurability,
} from '../../../src/libs/repositories/agent-harness/journal-startup/mongo';
import { SessionEventModel } from '../../../src/libs/repositories/agent-harness/journal/schema/journal.schema';
import { OutboxDispatcher } from '../../../src/libs/services/outbox/outbox.dispatcher';
import { OutboxEvent } from '../../../src/libs/repositories/outbox/schema/outbox.schema';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  mongoBackend,
  resetHarnessMongo,
} from './support/harness-mongo';
import {
  contractClock,
  createContractHarness,
} from './support/execution-store-contract';

const LEGACY_KEY = { orgId: 1, sessionId: 1, idempotencyKey: 1 };

describe('J-9 harness store migration on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  const db = () => mongoose.connection.db!;

  it('drops the legacy run index idempotently and ignores orphaned dispatch rows', async () => {
    await db()
      .collection('agentRuns')
      .createIndex(LEGACY_KEY, { unique: true, name: 'legacy_idempotency' });
    await db()
      .collection(LEGACY_DISPATCH_OUTBOX)
      .insertMany([
        {
          commandId: 'old-1',
          runId: 'run-old',
          kind: 'run_ready',
          status: 'pending',
        },
        {
          commandId: 'old-2',
          runId: 'run-old',
          kind: 'run_resume',
          status: 'pending',
        },
      ]);

    const first = await migrateHarnessStore();
    expect(first.droppedIndexes).to.deep.equal(['legacy_idempotency']);
    expect(first.orphanedDispatchRows).to.equal(2);
    const second = await migrateHarnessStore();
    expect(second.droppedIndexes).to.deep.equal([]);
    expect(second.createdIndexes).to.deep.equal([]);

    const keys = (await db().collection('agentRuns').indexes()).map((index) =>
      JSON.stringify(index.key),
    );
    expect(keys).to.not.include(JSON.stringify(LEGACY_KEY));
    // Orphans stay put and are never dispatched by the shared outbox.
    expect(
      await db().collection(LEGACY_DISPATCH_OUTBOX).countDocuments(),
    ).to.equal(2);
    const producer = {
      isConnected: () => true,
      connect: async () => undefined,
      publish: async () => {
        throw new Error('nothing should be published');
      },
    };
    const logger = { info() {}, debug() {}, warn() {}, error() {} };
    expect(
      await new OutboxDispatcher(producer as never, logger as never).drain(),
    ).to.equal(0);
    expect(await OutboxEvent.countDocuments()).to.equal(0);
  });

  it('lets a session reuse a client idempotency key once the legacy index is gone', async () => {
    await db()
      .collection('agentRuns')
      .createIndex(LEGACY_KEY, { unique: true, name: 'legacy_idempotency' });
    await migrateHarnessStore();
    const clock = contractClock();
    const h = createContractHarness(mongoBackend({ clock }), clock);
    const first = await h.admit({ idempotencyKey: 'same-key' });
    await h.service.control({
      commandId: 'cancel',
      dispatchCommandId: 'cancel-dispatch',
      orgId: first.run.orgId,
      runId: first.run.runId,
      expectedRevision: first.run.revision,
      action: 'cancel',
    });
    // Same (org, session, idempotencyKey) under a different actor-scoped receipt.
    const again = await h.admit({
      sessionId: first.sessionId,
      idempotencyKey: 'same-key',
      input: {
        query: 'second question',
        recordIds: [],
        filters: {},
        attachments: [],
        model: { chatMode: 'auto' },
      },
    });
    expect(again.created).to.equal(true);
    expect(await AgentRunModel.countDocuments()).to.equal(2);
  });

  it('builds the journal indexes on an empty database', async () => {
    await SessionEventModel.collection.drop().catch(() => undefined);
    await migrateHarnessStore();
    const keys = (await SessionEventModel.collection.indexes()).map((index) =>
      JSON.stringify(index.key),
    );
    expect(keys).to.include(JSON.stringify({ orgId: 1, streamId: 1, seq: 1 }));
  });

  it('reports a majority-capable replica set as durable', async () => {
    expect(await probeJournalDurability()).to.deep.equal({
      durable: true,
      reason: 'majority',
    });
  });

  const standaloneUri = process.env.MONGO_STANDALONE_IT_URI;
  (standaloneUri ? it : it.skip)(
    'O-3: reports a standalone server as not durable',
    async () => {
      const client = await MongoClient.connect(standaloneUri as string, {
        directConnection: true,
      });
      try {
        expect(await probeJournalDurability(client.db('probe'))).to.deep.equal({
          durable: false,
          reason: 'standalone',
        });
      } finally {
        await client.close();
      }
    },
  );
});
