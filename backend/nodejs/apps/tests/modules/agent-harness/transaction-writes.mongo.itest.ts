/**
 * PERF-E2: a transaction's inserts sent in one `bulkWrite` (MongoDB 8) store
 * what each insert sent on its own stored, and fail the way it failed. Runs
 * on a replica set as execution-store.mongo.itest.ts says.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose from 'mongoose';
import { OutboxEvent } from '../../../src/libs/repositories/outbox/schema/outbox.schema';
import { AgentCommandReceiptModel } from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import { AgentJournalPayloadModel } from '../../../src/libs/repositories/agent-harness/journal/schema/journal.schema';
import { inMongoTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import {
  TransactionWrites,
  transactionWrites,
} from '../../../src/libs/repositories/agent-harness/transaction-writes/mongo';
import { connectHarnessMongo, disconnectHarnessMongo, resetHarnessMongo } from './support/harness-mongo';
import { isDuplicateKeyError } from '../../../src/libs/utils/mongo.utils';
import { without } from './support/without';

const RAW = 'perfe2RawInserts';

const outboxRow = (key: string) => ({
  topic: 'harness-dispatch',
  key,
  orderingKey: 'org-1',
  value: JSON.stringify({ key }),
  status: 'pending' as const,
  attempts: 0,
  nextAttemptAt: new Date(1_000),
});

const receiptRow = (commandId: string) => ({
  orgId: 'org-1',
  runId: 'run-1',
  commandId,
  payloadHash: 'h',
  receipt: { commandId, journal: null },
  createdAt: 5,
});

/** A stored document less what differs between any two inserts (its id and the insert's own time). */
const shape = (document: Record<string, unknown> | null): Record<string, unknown> => {
  const { _id, createdAt, updatedAt, ...rest } = document ?? {};
  _id;
  return { ...rest, createdAt: typeof createdAt, updatedAt: typeof updatedAt };
};

describe('PERFE2: a transaction\'s inserts in one command', function () {
  this.timeout(60_000);
  const commands: string[] = [];
  const count = (event: { commandName: string }): void => {
    commands.push(event.commandName);
  };

  before(connectHarnessMongo);
  after(async () => {
    await mongoose.connection.db!.collection(RAW).drop().catch(() => undefined);
    await disconnectHarnessMongo();
  });
  beforeEach(async () => {
    await resetHarnessMongo();
    await mongoose.connection.db!.collection(RAW).deleteMany({});
    commands.length = 0;
    mongoose.connection.getClient().on('commandStarted', count);
  });
  afterEach(() => {
    mongoose.connection.getClient().off('commandStarted', count);
  });

  const batched = (work: (session: mongoose.ClientSession) => Promise<void>) =>
    inMongoTransaction(work, { batchWrites: true });

  it('PERFE2-1: stores what create stores, in one command, in the order queued', async () => {
    await inMongoTransaction(async (session) => {
      await OutboxEvent.create([outboxRow('alone')], { session, ordered: true });
      await AgentCommandReceiptModel.create([receiptRow('alone')], { session });
    });
    commands.length = 0;
    await batched(async (session) => {
      const rows = [outboxRow('batched-1'), outboxRow('batched-2')];
      await transactionWrites.insert(session, OutboxEvent, rows, () => Promise.reject(new Error('not batched')));
      const receipts = [receiptRow('batched')];
      await transactionWrites.insert(session, AgentCommandReceiptModel, receipts, () =>
        Promise.reject(new Error('not batched')),
      );
      const raw = { a: 1, nested: { b: null } };
      await transactionWrites.insertRaw(session, RAW, raw, () => Promise.reject(new Error('not batched')));
    });
    expect(commands.filter((name) => !['commitTransaction', 'hello'].includes(name))).to.deep.equal(['bulkWrite']);

    const alone = await OutboxEvent.findOne({ key: 'alone' }).lean();
    const batchedRow = await OutboxEvent.findOne({ key: 'batched-1' }).lean();
    expect(shape({ ...batchedRow, key: 'alone', value: JSON.stringify({ key: 'alone' }) })).to.deep.equal(shape(alone));
    const order = await OutboxEvent.find({ key: /^batched/ }).sort({ _id: 1 }).lean();
    expect(order.map((row) => row.key)).to.deep.equal(['batched-1', 'batched-2']);

    const receiptAlone = await AgentCommandReceiptModel.findOne({ commandId: 'alone' }).lean();
    const receiptBatched = await AgentCommandReceiptModel.findOne({ commandId: 'batched' }).lean();
    expect(shape({ ...receiptBatched, commandId: 'alone', receipt: { commandId: 'alone', journal: null } })).to.deep.equal(
      shape(receiptAlone as unknown as Record<string, unknown>),
    );
    expect(await mongoose.connection.db!.collection(RAW).findOne({}, { projection: { _id: 0 } })).to.deep.equal({
      a: 1,
      nested: { b: null },
    });
  });

  it('PERFE2-2: a document its model refuses is refused when queued, before anything is sent', async () => {
    const error = await batched(async (session) => {
      await transactionWrites.insert(session, OutboxEvent, [{ topic: 't' }], () => Promise.resolve());
    }).catch((caught: unknown) => caught);
    expect((error as Error).name).to.equal('ValidationError');
    expect(commands).to.not.include('bulkWrite');
  });

  it('PERFE2-3: a duplicate key is code 11000 as on its own, and the transaction keeps nothing', async () => {
    await AgentCommandReceiptModel.create([receiptRow('taken')]);
    const error = await batched(async (session) => {
      await transactionWrites.insert(session, OutboxEvent, [outboxRow('before-duplicate')], () => Promise.resolve());
      await transactionWrites.insert(session, AgentCommandReceiptModel, [receiptRow('taken')], () =>
        Promise.resolve(),
      );
    }).catch((caught: unknown) => caught);
    expect(isDuplicateKeyError(error), String(error)).to.equal(true);
    expect(await OutboxEvent.countDocuments({ key: 'before-duplicate' })).to.equal(0);
  });

  it('PERFE2-4: a write conflict keeps its transient label, so the transaction is retried', async () => {
    const holder = await mongoose.startSession();
    holder.startTransaction({ readConcern: { level: 'snapshot' }, writeConcern: { w: 'majority' } });
    await mongoose.connection.db!.collection(RAW).insertOne({ _id: 'contended' as never }, { session: holder });
    let attempts = 0;
    await batched(async (session) => {
      attempts += 1;
      if (attempts === 2) await holder.abortTransaction();
      await transactionWrites.insertRaw(session, RAW, { _id: 'contended' }, () => Promise.resolve());
    });
    await holder.endSession();
    expect(attempts).to.equal(2);
    expect(await mongoose.connection.db!.collection(RAW).countDocuments({ _id: 'contended' as never })).to.equal(1);
  });

  it('PERFE2-5: with no batch open, or a server without the command, each insert goes on its own', async () => {
    const before = new TransactionWrites(() => Promise.resolve(false));
    let written = 0;
    await inMongoTransaction(async (session) => {
      await transactionWrites.insert(session, OutboxEvent, [outboxRow('x')], async () => {
        written += 1;
      });
      await before.begin(session);
      await before.insert(session, OutboxEvent, [outboxRow('y')], async () => {
        written += 1;
      });
      await before.flush(session);
    });
    expect(written).to.equal(2);
    expect(commands).to.not.include('bulkWrite');
  });

  it("PERFE3-6: queued updates (with timestamps, an upsert, an updateMany) store what the model's own updates store", async () => {
    const [alone, queued] = await OutboxEvent.create([outboxRow('alone'), outboxRow('queued')]);
    const retried = { $set: { attempts: 2, nextAttemptAt: new Date(2_000) } };
    const payload = (hash: string) => ({
      $setOnInsert: { orgId: 'org-1', hash, size: 1, bytes: Buffer.from('x'), createdAt: new Date(5), owner: { sessionId: 's' }, referenced: false },
    });
    const marked = (hash: string) => ({ _id: { $in: [`org-1:${hash}`] }, orgId: 'org-1', referenced: false });
    await inMongoTransaction(async (session) => {
      await OutboxEvent.updateOne({ _id: alone!._id }, retried, { session });
      await AgentJournalPayloadModel.updateOne({ _id: 'org-1:alone' }, payload('alone'), { upsert: true, session });
      await AgentJournalPayloadModel.updateMany(marked('alone'), { $set: { referenced: true } }, { session });
    });
    commands.length = 0;
    const never = () => Promise.reject(new Error('not batched'));
    await batched(async (session) => {
      await transactionWrites.update(session, OutboxEvent, { name: 'updateOne', filter: { _id: queued!._id }, update: retried }, never);
      await transactionWrites.update(
        session,
        AgentJournalPayloadModel,
        { name: 'updateOne', filter: { _id: 'org-1:queued' }, update: payload('queued'), upsert: true },
        never,
      );
      await transactionWrites.update(
        session,
        AgentJournalPayloadModel,
        { name: 'updateMany', filter: marked('queued'), update: { $set: { referenced: true } } },
        never,
      );
    });
    expect(commands.filter((name) => name !== 'commitTransaction'), 'one command').to.deep.equal(['bulkWrite']);
    const outbox = await OutboxEvent.find({ key: { $in: ['alone', 'queued'] } }).sort({ key: 1 }).lean();
    expect(outbox.map((row) => row.updatedAt! > row.createdAt!), 'the update moved updatedAt').to.deep.equal([true, true]);
    expect(shape({ ...outbox[1], key: 'alone', value: outbox[0]!.value })).to.deep.equal(shape(outbox[0] as never));
    const payloads = await AgentJournalPayloadModel.find({}).sort({ _id: 1 }).lean();
    expect(payloads.map((payload) => without(payload, '_id', 'hash'))).to.deep.equal([
      { orgId: 'org-1', size: 1, bytes: payloads[0]!.bytes, createdAt: new Date(5), owner: { sessionId: 's' }, referenced: true },
      { orgId: 'org-1', size: 1, bytes: payloads[0]!.bytes, createdAt: new Date(5), owner: { sessionId: 's' }, referenced: true },
    ]);
  });
});
