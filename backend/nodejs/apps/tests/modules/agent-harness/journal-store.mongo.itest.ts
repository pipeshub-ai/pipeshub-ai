/**
 * The JournalStore contract (J-1..J-4) and Mongo-only journal behaviour on a
 * real replica set (run instructions in execution-store.mongo.itest.ts).
 */
import 'reflect-metadata';
import { SnapshotRecordingJournalStore } from '../../../src/modules/agent-harness/application/snapshot-recording-journal-store';
import { MongoSandboxSnapshotIndex } from '../../../src/libs/repositories/agent-harness/sandbox-snapshots/mongo';
import { expect } from 'chai';
import { ClientSession, Model } from 'mongoose';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { inMongoTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import {
  AgentJournalPayloadModel,
  SessionEventModel,
  SessionStreamCounterModel,
} from '../../../src/libs/repositories/agent-harness/journal/schema/journal.schema';
import { MongoJournalStore } from '../../../src/libs/repositories/agent-harness/journal/mongo';
import { MongoPayloadStore } from '../../../src/libs/repositories/agent-harness/payload-store/mongo';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';
import { position, turnStarted } from './support/journal-fixtures';
import { describeJournalStoreContract, JournalBackend } from './support/journal-store-contract';
import { AuditedJournalStore } from '../../../src/modules/agent-harness/application/audited-journal-store';
import { PayloadReferencingJournalStore } from '../../../src/modules/agent-harness/application/payload-referencing-journal-store';
import { SealedJournalStore } from '../../../src/modules/agent-harness/application/sealed-journal-store';
import { MongoAuditChainStore } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { JournalStore } from '../../../src/modules/agent-harness/ports/journal-store';
import { testCipher } from './support/data-protection';

registerHarnessModels([
  SessionEventModel,
  SessionStreamCounterModel,
  AgentJournalPayloadModel,
] as unknown as Array<Model<never>>);

const payloadId = (ref: string): string => ref.replace(/^mongo:/, '');

describe('agent harness journal on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  const mongoJournalBackend = (
    wrap: (inner: MongoJournalStore, payloads: MongoPayloadStore) => JournalStore<ClientSession> = (inner) => inner,
  ): JournalBackend<ClientSession> => {
    const payloads = new MongoPayloadStore();
    return {
      journal: wrap(new MongoJournalStore(), payloads),
      payloads,
      transact: (work) => inMongoTransaction(work),
      corruptPayload: async (_orgId, ref) => {
        await AgentJournalPayloadModel.updateOne(
          { _id: payloadId(ref) },
          { $set: { bytes: Buffer.from('{"tampered":true}') } },
        );
      },
      deletePayload: async (_orgId, ref) => {
        await AgentJournalPayloadModel.deleteOne({ _id: payloadId(ref) });
      },
      reset: resetHarnessMongo,
    };
  };

  describeJournalStoreContract('mongo replica set', () => mongoJournalBackend());

  describeJournalStoreContract('production decorator stack on mongo (O-1c, O-2a, T5, OWD-12)', () =>
    mongoJournalBackend(
      (inner, payloads) =>
        new AuditedJournalStore(
          new SnapshotRecordingJournalStore(
            new PayloadReferencingJournalStore(new SealedJournalStore(inner, testCipher()), payloads),
            new MongoSandboxSnapshotIndex(),
            'default',
          ),
          new MongoAuditChainStore(),
        ),
    ),
  );

  describe('Mongo-only journal behaviour', () => {
    beforeEach(resetHarnessMongo);

    it('J-1: concurrent transactions on one stream stay gap-free and ordered', async () => {
      const journal = new MongoJournalStore();
      const service = new JournalService(new MongoPayloadStore(), testCipher());
      const writers = Array.from({ length: 8 }, (_, writer) => writer);
      await Promise.all(
        writers.map(async (writer) => {
          const prepared = await service.prepare({
            ...position({ runId: `run-${String(writer)}` }),
            writer: 'worker',
            events: [turnStarted(1), turnStarted(2), turnStarted(3)],
          });
          await inMongoTransaction((session) =>
            journal.append(prepared, session),
          );
        }),
      );
      const events = await journal.readStream('org-1', 'session-1', 0, 100);
      expect(events.map((event) => event.seq)).to.deep.equal(
        Array.from({ length: 24 }, (_, index) => index + 1),
      );
      // Each batch landed contiguously: seq order equals commit order.
      for (let index = 0; index < 24; index += 3) {
        const batch = events.slice(index, index + 3);
        expect(new Set(batch.map((event) => event.runId)).size).to.equal(1);
      }
      expect(await journal.lastSeq('org-1', ['session-1'])).to.deep.equal({
        'session-1': 24,
      });
    });

    it('an aborted transaction leaves no events and no counter advance', async () => {
      const journal = new MongoJournalStore();
      const prepared = await new JournalService(new MongoPayloadStore(), testCipher()).prepare({
        ...position(),
        writer: 'worker',
        events: [turnStarted(1)],
      });
      try {
        await inMongoTransaction(async (session) => {
          await journal.append(prepared, session);
          throw new Error('abort');
        });
      } catch (error) {
        expect((error as Error).message).to.equal('abort');
      }
      expect(await SessionEventModel.countDocuments()).to.equal(0);
      expect(await journal.lastSeq('org-1', ['session-1'])).to.deep.equal({
        'session-1': 0,
      });
    });

    it('J-8: reads go to the primary with majority read concern', () => {
      const journal = new MongoJournalStore();
      const query = journal.durableRead(SessionEventModel.find({}));
      const options = query.getOptions() as Record<string, unknown>;
      expect(JSON.stringify(options.readPreference)).to.contain('primary');
      expect(options.readConcern).to.deep.equal({ level: 'majority' });
    });

    it('declares the (orgId, streamId, seq) unique and (orgId, runId, seq) indexes', async () => {
      const indexes = await SessionEventModel.collection.indexes();
      const keys = indexes.map((index) => ({
        key: JSON.stringify(index.key),
        unique: index.unique === true,
      }));
      expect(keys).to.deep.include({
        key: JSON.stringify({ orgId: 1, streamId: 1, seq: 1 }),
        unique: true,
      });
      expect(keys).to.deep.include({
        key: JSON.stringify({ orgId: 1, runId: 1, seq: 1 }),
        unique: false,
      });
    });
  });
});
