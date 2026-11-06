import 'reflect-metadata';
import { once } from 'events';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import {
  OutboxDispatcher,
  OutboxInsertStream,
  openOutboxInserts,
  wakeOutbox,
} from '../../../../src/libs/services/outbox/outbox.dispatcher';
import { OutboxEvent } from '../../../../src/libs/repositories/outbox/schema/outbox.schema';

const uri = process.env.PCC_MONGO_URI;

/** A poll so long that only a wake can deliver within a test. */
const NEVER = 60 * 60 * 1000;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

(uri ? describe : describe.skip)('outbox dispatcher against a real MongoDB replica set', function () {
  this.timeout(30_000);
  // Its own database: a started dispatcher claims every due row it can see.
  const dbName = `outbox_dispatch_${new mongoose.Types.ObjectId().toHexString()}`;
  let published: Array<{ key: string; at: number }>;
  let dispatcher: OutboxDispatcher | undefined;

  const producer = () => ({
    isConnected: () => true,
    connect: async () => undefined,
    publish: async (_topic: string, message: { key: string }) => {
      published.push({ key: message.key, at: Date.now() });
    },
  });
  const logger = () => ({ info: sinon.stub(), debug: sinon.stub(), warn: sinon.stub(), error: sinon.stub() });
  const doc = (key: string) => ({
    topic: 'notification',
    key,
    orderingKey: key,
    value: '{}',
    status: 'pending' as const,
    attempts: 0,
    nextAttemptAt: new Date(),
  });
  const publishedAt = async (key: string, withinMs: number) => {
    const deadline = Date.now() + withinMs;
    while (Date.now() < deadline) {
      const hit = published.find((p) => p.key === key);
      if (hit) return hit.at;
      await sleep(5);
    }
    return undefined;
  };

  before(async function () {
    await mongoose.connect(uri as string, { dbName });
    const hello = (await mongoose.connection.db!.admin().command({ hello: 1 })) as { setName?: string };
    if (!hello.setName) this.skip();
    await OutboxEvent.init();
  });
  after(async () => {
    await mongoose.connection.db?.dropDatabase();
    await mongoose.disconnect();
  });
  beforeEach(() => {
    published = [];
  });
  afterEach(async () => {
    await dispatcher?.stop();
    dispatcher = undefined;
  });

  it('publishes a row another instance wrote in moments, through the insert stream, with no wake and no poll', async () => {
    let stream: OutboxInsertStream | undefined;
    dispatcher = new OutboxDispatcher(producer() as never, logger() as never, NEVER, {
      inserts: () => {
        stream = openOutboxInserts();
        return stream;
      },
    });
    dispatcher.start();
    // The stream has its starting point once MongoDB first answers it.
    await once(stream as unknown as NodeJS.EventEmitter, 'resumeTokenChanged');

    const latencies: number[] = [];
    for (let i = 0; i < 10; i += 1) {
      const key = `other-instance-${i}`;
      const writtenAt = Date.now();
      await OutboxEvent.create([doc(key)]);
      const at = await publishedAt(key, 3_000);
      expect(at, key).to.be.a('number');
      latencies.push(at! - writtenAt);
    }
    latencies.sort((a, b) => a - b);
    // Recorded for the report; the poll it replaces is 2 s.
    console.log(`      insert stream, write to publish (ms): p50 ${latencies[4]} p95 ${latencies[9]} all ${latencies.join(',')}`);
    expect(latencies[9]).to.be.below(1_000);
  });

  it('a row written in a transaction is published right after the commit, never before it', async () => {
    dispatcher = new OutboxDispatcher(producer() as never, logger() as never, NEVER, { inserts: null });
    dispatcher.start();

    const latencies: number[] = [];
    for (let i = 0; i < 10; i += 1) {
      const key = `in-transaction-${i}`;
      const session = await mongoose.startSession();
      let committedAt: number;
      try {
        await session.withTransaction(async () => {
          await OutboxEvent.create([doc(key)], { session });
          wakeOutbox(session);
          // Still uncommitted: nothing can be claimed yet, and the wake waits.
          await sleep(100);
          expect(published.map((p) => p.key)).to.not.include(key);
        });
        committedAt = Date.now();
      } finally {
        await session.endSession();
      }
      const at = await publishedAt(key, 3_000);
      expect(at, key).to.be.a('number');
      expect(at!).to.be.at.least(committedAt);
      latencies.push(at! - committedAt);
    }
    latencies.sort((a, b) => a - b);
    console.log(`      in-process wake, commit to publish (ms): p50 ${latencies[4]} p95 ${latencies[9]} all ${latencies.join(',')}`);
    expect(latencies[9]).to.be.below(1_000);
  });
});
