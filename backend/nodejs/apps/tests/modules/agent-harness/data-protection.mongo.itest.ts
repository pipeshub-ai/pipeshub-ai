/**
 * O-1 on a real MongoDB replica set: what a raw document shows, crypto-
 * shredding through the Mongo key store, and replicas converging on one key
 * (run instructions in execution-store.mongo.itest.ts).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Model } from 'mongoose';
import { inMongoTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import {
  AgentJournalPayloadModel,
  SessionEventModel,
  SessionStreamCounterModel,
} from '../../../src/libs/repositories/agent-harness/journal/schema/journal.schema';
import { AgentHarnessDataKeyModel } from '../../../src/libs/repositories/agent-harness/data-protection/schema/data-protection.schema';
import { MongoDataKeyStore } from '../../../src/libs/repositories/agent-harness/data-protection/mongo';
import { MongoJournalStore } from '../../../src/libs/repositories/agent-harness/journal/mongo';
import { MongoPayloadStore } from '../../../src/libs/repositories/agent-harness/payload-store/mongo';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { SealedJournalStore } from '../../../src/modules/agent-harness/application/sealed-journal-store';
import { JournalPayloadMissingError } from '../../../src/modules/agent-harness/domain/errors';
import { DATA_KEY_SEAL_RESERVATION } from '../../../src/modules/agent-harness/domain/data-protection';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { RunContentService } from '../../../src/modules/agent-harness/application/run-content-service';
import {
  JOURNAL_INLINE_PAYLOAD_MAX_BYTES,
  JournalEventInput,
} from '../../../src/modules/agent-harness/domain/journal';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';
import { dataKeyRing } from './support/data-protection';
import { position, turnStarted, workerEvent } from './support/journal-fixtures';
import { describeJournalStoreContract } from './support/journal-store-contract';

registerHarnessModels([
  SessionEventModel,
  SessionStreamCounterModel,
  AgentJournalPayloadModel,
  AgentHarnessDataKeyModel,
] as unknown as Array<Model<never>>);

const SENTINEL = 'SENTINEL-mongo-7d2e-customer-text';

const secretOutcome = (text = SENTINEL): JournalEventInput =>
  workerEvent('tool.outcome', {
    invocationId: 'inv-1',
    status: 'ok',
    receipt: { note: text },
    labels: { integrity: 'external' },
  });

describe('O-1 envelope encryption on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  const sealed = () => {
    const ring = dataKeyRing({ store: new MongoDataKeyStore() });
    return {
      ...ring,
      journal: new SealedJournalStore(new MongoJournalStore(), ring.cipher),
      service: new JournalService(new MongoPayloadStore(), ring.cipher),
    };
  };

  describeJournalStoreContract('sealed mongo replica set', () => {
    const { journal } = sealed();
    return {
      journal,
      payloads: new MongoPayloadStore(),
      transact: (work) => inMongoTransaction(work),
      corruptPayload: async (_orgId, ref) => {
        await AgentJournalPayloadModel.updateOne(
          { _id: ref.replace(/^mongo:/, '') },
          { $set: { bytes: Buffer.from('{"tampered":true}') } },
        );
      },
      deletePayload: async (_orgId, ref) => {
        await AgentJournalPayloadModel.deleteOne({
          _id: ref.replace(/^mongo:/, ''),
        });
      },
      reset: resetHarnessMongo,
    };
  });

  describe('raw documents, shredding and replicas', () => {
    beforeEach(resetHarnessMongo);

    const append = async (
      h: ReturnType<typeof sealed>,
      events: JournalEventInput[],
      orgId = 'org-1',
    ) => {
      const prepared = await h.service.prepare({
        ...position({ orgId, fence: 4 }),
        writer: 'worker',
        events,
      });
      await inMongoTransaction((session) =>
        h.journal.append(prepared, session),
      );
    };

    it('O-1a/O-1b: a raw event or payload document holds no content; the envelope stays queryable', async () => {
      const h = sealed();
      const big = secretOutcome(
        `${SENTINEL}${'y'.repeat(JOURNAL_INLINE_PAYLOAD_MAX_BYTES)}`,
      );
      await append(h, [turnStarted(1), secretOutcome(), big]);
      const raw = await SessionEventModel.collection
        .find({
          orgId: 'org-1',
          type: 'tool.outcome',
          fence: 4,
          writer: 'worker',
        })
        .sort({ seq: 1 })
        .toArray();
      expect(raw.map((doc) => doc['seq'])).to.deep.equal([2, 3]);
      expect(JSON.stringify(raw)).not.to.contain(SENTINEL);
      expect(raw[0]?.['data']).to.equal(undefined);
      expect(raw[0]?.['sealed']).to.include({ v: 1, kv: 1 });
      const payloads = await AgentJournalPayloadModel.collection
        .find({})
        .toArray();
      expect(payloads).to.have.length(1);
      const bytes = (payloads[0]?.['bytes'] as { buffer: Uint8Array }).buffer;
      expect(Buffer.from(bytes).toString('latin1')).not.to.contain(SENTINEL);
      const keys = await AgentHarnessDataKeyModel.collection.find({}).toArray();
      expect(keys).to.have.length(1);
      expect(keys[0]?.['wrapped']).to.be.a('string');
      expect(Object.keys(keys[0] ?? {})).not.to.include('key');

      const read = await h.service.resolve(
        await h.journal.readStream('org-1', 'session-1', 0, 10),
      );
      expect(read.map((event) => event.data)).to.deep.equal([
        turnStarted(1).data,
        secretOutcome().data,
        big.data,
      ]);
    });

    it('O-1d: shredding the Mongo-held keys makes the org unreadable, other orgs not', async () => {
      const h = sealed();
      await append(h, [secretOutcome()]);
      await append(h, [secretOutcome()], 'org-2');
      expect(await h.keys.shred('org-1')).to.equal(1);
      expect(
        await AgentHarnessDataKeyModel.countDocuments({ orgId: 'org-1' }),
      ).to.equal(0);
      try {
        await h.journal.readStream('org-1', 'session-1', 0, 10);
        expect.fail('expected the shredded org to be unreadable');
      } catch (error) {
        expect(error).to.be.instanceOf(JournalPayloadMissingError);
        expect((error as JournalPayloadMissingError).reason).to.equal(
          'shredded',
        );
      }
      // A fresh replica (nothing cached) cannot read it either.
      const other = sealed();
      try {
        await other.journal.readStream('org-1', 'session-1', 0, 10);
        expect.fail('expected the shredded org to be unreadable');
      } catch (error) {
        expect((error as JournalPayloadMissingError).reason).to.equal(
          'shredded',
        );
      }
      expect(
        await other.journal.readStream('org-2', 'session-1', 0, 10),
      ).to.have.length(1);
    });

    it('O-1h/O-1e: replicas converge on one first key (unique index) and read a version another replica rotated to', async () => {
      const replicas = Array.from({ length: 6 }, () => sealed());
      const keys = await Promise.all(
        replicas.map((replica) => replica.keys.active('org-1')),
      );
      expect(new Set(keys.map((key) => key.kid)).size).to.equal(1);
      expect(
        await AgentHarnessDataKeyModel.countDocuments({ orgId: 'org-1' }),
      ).to.equal(1);

      const [writer, reader] = replicas;
      await writer!.keys.rotate('org-1');
      await append(writer!, [secretOutcome()]);
      const [event] = await reader!.journal.readStream(
        'org-1',
        'session-1',
        0,
        1,
      );
      expect(event?.data).to.deep.equal(secretOutcome().data);
      const raw = await SessionEventModel.collection.findOne({
        orgId: 'org-1',
      });
      expect((raw?.['sealed'] as { kv: number }).kv).to.equal(2);
    });

    it('CS-8: a sealed content blob holds no plaintext in Mongo; a shred makes it unreadable', async () => {
      const ring = dataKeyRing({ store: new MongoDataKeyStore() });
      const run = {
        orgId: 'org-1',
        runId: 'run-1',
        sessionId: 'session-1',
        status: 'running',
        workerId: 'worker-a',
        attemptId: 'attempt-1',
        fence: 3,
      } as HarnessRun;
      const attempt = {
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'worker-a',
        attemptId: 'attempt-1',
        fence: 3,
      };
      const content = new RunContentService(
        {
          get: async (orgId, runId) =>
            orgId === run.orgId && runId === run.runId ? run : null,
        },
        ring.cipher,
        new MongoPayloadStore(),
      );
      const plaintext = Buffer.from(`${SENTINEL}${'c'.repeat(64 * 1024)}`);
      const { ref } = await content.put(attempt, plaintext);
      const stored = await AgentJournalPayloadModel.collection
        .find({})
        .toArray();
      expect(stored).to.have.length(1);
      const bytes = Buffer.from(
        (stored[0]?.['bytes'] as { buffer: Uint8Array }).buffer,
      );
      expect(bytes.toString('latin1')).not.to.contain(SENTINEL);
      expect(bytes.length).to.be.greaterThan(plaintext.length);
      expect((await content.get(attempt, ref)).equals(plaintext)).to.equal(
        true,
      );

      await ring.keys.shred('org-1');
      const fresh = new RunContentService(
        { get: async () => run },
        dataKeyRing({ store: new MongoDataKeyStore() }).cipher,
        new MongoPayloadStore(),
      );
      for (const reader of [content, fresh]) {
        try {
          await reader.get(attempt, ref);
          expect.fail('expected shredded content to be unreadable');
        } catch (error) {
          expect(error).to.be.instanceOf(JournalPayloadMissingError);
          expect((error as JournalPayloadMissingError).reason).to.equal(
            'shredded',
          );
        }
      }
    });

    it('KR-1/KR-2: the seal counter lives on the key document; replicas rotating by count converge', async () => {
      const store = new MongoDataKeyStore();
      const first = await dataKeyRing({ store }).keys.active('org-1');
      await store.reserveSeals('org-1', first.kid, 20);
      const counted = await AgentHarnessDataKeyModel.findById(first.kid).lean();
      expect(counted?.seals).to.equal(DATA_KEY_SEAL_RESERVATION + 20);

      await resetHarnessMongo();
      const keyOptions = { rotateAfterSeals: 10, sealReservation: 3 };
      const replicas = Array.from({ length: 4 }, () =>
        dataKeyRing({ store, keyOptions }),
      );
      const sealedBy = await Promise.all(
        Array.from({ length: 24 }, (_, index) =>
          replicas[index % replicas.length]!.cipher.seal(
            'org-1',
            'journal.data',
            { n: index },
            Buffer.from(SENTINEL),
          ),
        ),
      );
      const versions = await AgentHarnessDataKeyModel.find({ orgId: 'org-1' })
        .sort({ version: 1 })
        .lean();
      expect(versions.map((key) => key.version)).to.deep.equal(
        versions.map((_, index) => index + 1),
      );
      expect(versions.length).to.be.greaterThan(2);
      for (const key of versions) {
        const used = sealedBy.filter((sealed) => sealed.kid === key._id).length;
        expect(used).to.be.at.most(10);
        expect(key.seals ?? 0).to.be.at.least(used);
      }
      const reader = dataKeyRing({ store });
      const opened = await Promise.all(
        sealedBy.map((sealed, index) =>
          reader.cipher.open('org-1', 'journal.data', { n: index }, sealed),
        ),
      );
      expect(opened.every((bytes) => bytes.toString() === SENTINEL)).to.equal(
        true,
      );
    });
  });
});
