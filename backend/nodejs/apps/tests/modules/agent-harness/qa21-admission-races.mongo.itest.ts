/**
 * QA-21: races and failures at admission, on a real MongoDB replica set.
 * Concurrent turns are forced at the store seam (support/store-race.ts);
 * failures are injected at each operation of the admission transaction.
 * Run instructions in execution-store.mongo.itest.ts.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import mongoose from 'mongoose';
import { BSON } from 'mongodb';
import {
  ContractBackend,
  ContractHarness,
  contractClock,
  createContractHarness,
} from './support/execution-store-contract';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  mongoBackend,
  MongoBackendOptions,
  resetHarnessMongo,
} from './support/harness-mongo';
import { HARNESS_ENABLED } from './support/rollout';
import { workerEvent } from './support/journal-fixtures';
import { testCipher } from './support/data-protection';
import {
  byParty,
  codeOf,
  expectForced,
  expectValidHistory,
  ordersOf,
  partyNames,
  statusOf,
  winners,
} from './support/race-assertions';
import { auditLinksIdle } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { asParty, failAt, Gate, injectedFailure, probeStore, StoreRace } from './support/store-race';
import { PARTY_HEADER, sseFrames, startChatTurnHarness } from './support/chat-turn-harness';
import { MongoPayloadStore } from '../../../src/libs/repositories/agent-harness/payload-store/mongo';
import { MongoRetentionStore } from '../../../src/libs/repositories/agent-harness/retention-store/mongo';
import { MongoExecutionStore } from '../../../src/libs/repositories/agent-harness/execution/mongo';
import {
  AgentAdmissionReceiptModel,
  AgentRunModel,
} from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import { AgentJournalPayloadModel } from '../../../src/libs/repositories/agent-harness/journal/schema/journal.schema';
import { SessionImportContent } from '../../../src/modules/agent-harness/application/session-import-content';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { SubmitDurableRun } from '../../../src/modules/agent-harness/application/run-admission-service';
import { RunAdmissionResult } from '../../../src/modules/agent-harness/domain/admission';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { inlineContentRef } from '../../../src/modules/agent-harness/domain/run-content';
import { ADMISSION_QUOTAS, AdmissionQuotas } from '../../../src/modules/agent-harness/domain/admission-quotas';
import { HarnessAgentAccessPolicy } from '../../../src/modules/enterprise_search/adapters/harness-agent-access';
import { stableObjectIdHexForExternalEmail } from '../../../src/modules/enterprise_search/utils/scoped-request';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { appendMessages } from '../../../src/modules/enterprise_search/utils/utils';
import { IMessage } from '../../../src/modules/enterprise_search/types/conversation.interfaces';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };

const text = (query: string): Pick<SubmitDurableRun, 'input'> => ({
  input: { query, recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
});

/** Every document of every collection, so "nothing is visible" compares the whole database. */
async function databaseState(): Promise<Record<string, string[]>> {
  // The audit entries of what just committed are linked onto the chain a moment later (PERF-C).
  await auditLinksIdle();
  const db = mongoose.connection.db!;
  const state: Record<string, string[]> = {};
  for (const collection of await db.collections()) {
    const documents = await collection.find({}).toArray();
    state[collection.collectionName] = documents.map((doc) => BSON.EJSON.stringify(doc)).sort();
  }
  return state;
}

describe('QA-21 races and failures at admission (MongoDB replica set)', function () {
  this.timeout(180_000);
  let h: ContractHarness;
  let backend: ContractBackend;

  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  const harness = async (options: Omit<MongoBackendOptions, 'clock'> = {}) => {
    await resetHarnessMongo();
    const clock = contractClock();
    backend = mongoBackend({ ...options, clock });
    h = createContractHarness(backend, clock);
    h.setRollout(V2);
  };
  beforeEach(() => harness());

  const expectValid = (run: Pick<HarnessRun, 'orgId' | 'sessionId'>) => expectValidHistory(backend, run);
  const runsOf = (sessionId: string) => AgentRunModel.find({ sessionId }).lean();
  const cancel = (run: HarnessRun) =>
    h.service.control({
      orgId: run.orgId,
      runId: run.runId,
      expectedRevision: run.revision,
      action: 'cancel',
      commandId: `cancel-${run.runId}`,
      dispatchCommandId: `cancel-${run.runId}:dispatch`,
    });
  /** A session whose first run answered: the next turn binds it, reads its history and imports nothing new. */
  const idleSession = async (): Promise<string> => {
    const { run, sessionId } = await h.admit(text('first question'));
    const claimed = await h.claim(run, 'worker-a', 5_000);
    await h.service.commit({
      commandId: 'first-answer',
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'worker-a',
      attemptId: claimed.attemptId as string,
      fence: claimed.fence,
      events: [
        workerEvent('message.completed', {
          role: 'assistant',
          textRef: inlineContentRef(Buffer.from('first answer')),
          isFinal: true,
        }),
      ],
      legacyEvents: [],
      transition: { status: 'completed', outcome: 'answer' },
      outbox: [],
    });
    return sessionId;
  };

  describe('QA21-1 the same turn admitted at once (ADM-16, ADM-G2, IX-RTE-ADM-3)', () => {
    for (const release of ordersOf('first', 'resend')) {
      it(`one run and one conversation; the other is a replay of it (release ${String(release)})`, async () => {
        const race = new StoreRace(['first', 'resend'], { release });
        const outcomes = await race.run<RunAdmissionResult>({
          first: () => h.admit({ idempotencyKey: 'turn-1', ...text('q') }),
          resend: () => h.admit({ idempotencyKey: 'turn-1', ...text('q') }),
        });
        expectForced(race, ['first', 'resend'], outcomes);
        expect(winners(outcomes), outcomes.map(codeOf).join()).to.have.length(2);
        const results = outcomes.map((outcome) => (outcome as { value: RunAdmissionResult }).value);
        expect(results.filter((result) => result.created)).to.have.length(1);
        if (Array.isArray(release)) {
          const first = byParty(outcomes)[release[0]!] as { value: RunAdmissionResult };
          expect(first.value.created, 'the first released admits; the other replays it').to.equal(true);
        }
        expect(new Set(results.map((result) => result.run.runId)).size).to.equal(1);
        expect(new Set(results.map((result) => result.sessionId)).size).to.equal(1);
        expect(await ChatSession.countDocuments({ orgId: h.tenant.orgId })).to.equal(1);
        expect(await AgentRunModel.countDocuments({ orgId: h.tenant.orgId })).to.equal(1);
        expect(await AgentAdmissionReceiptModel.countDocuments({})).to.equal(1);
        expect(await backend.dispatches(h.tenant.orgId, results[0]!.run.runId)).to.have.length(1);
        await expectValid(results[0]!.run);
      });
    }

    it('50 identical admissions at once: one run, 49 replays of it', async () => {
      const names = partyNames('c', 50);
      const race = new StoreRace(names);
      const outcomes = await race.run<RunAdmissionResult>(
        Object.fromEntries(names.map((name) => [name, () => h.admit({ idempotencyKey: 'turn-50', ...text('q') })])),
      );
      expectForced(race, names, outcomes);
      expect(winners(outcomes), outcomes.map(codeOf).join()).to.have.length(50);
      const results = outcomes.map((outcome) => (outcome as { value: RunAdmissionResult }).value);
      expect(results.filter((result) => result.created)).to.have.length(1);
      expect(new Set(results.map((result) => result.run.runId)).size).to.equal(1);
      expect(await ChatSession.countDocuments({ orgId: h.tenant.orgId })).to.equal(1);
      expect(await AgentRunModel.countDocuments({ orgId: h.tenant.orgId })).to.equal(1);
      expect(await backend.dispatches(h.tenant.orgId, results[0]!.run.runId)).to.have.length(1);
      await expectValid(results[0]!.run);
    });

    it('the same key with another question at once: one run, the other refused IDEMPOTENCY_CONFLICT', async () => {
      const race = new StoreRace(['a', 'b']);
      const outcomes = await race.run<RunAdmissionResult>({
        a: () => h.admit({ idempotencyKey: 'turn-x', ...text('one question') }),
        b: () => h.admit({ idempotencyKey: 'turn-x', ...text('another question') }),
      });
      expectForced(race, ['a', 'b'], outcomes);
      expect(winners(outcomes)).to.have.length(1);
      const lost = outcomes.find((outcome) => !outcome.ok)!;
      expect(codeOf(lost)).to.equal('IDEMPOTENCY_CONFLICT');
      expect(statusOf(lost)).to.equal(409);
      expect(await AgentRunModel.countDocuments({ orgId: h.tenant.orgId })).to.equal(1);
    });
  });

  describe('QA21-2 distinct turns at once on one idle conversation (ADM-16, ST-1)', () => {
    for (const release of ordersOf('a', 'b')) {
      it(`one starts the run, the other joins it as queued input (release ${String(release)})`, async () => {
        const sessionId = await idleSession();
        const race = new StoreRace(['a', 'b'], { release });
        const outcomes = byParty(
          await race.run<RunAdmissionResult>({
            a: () => h.admit({ sessionId, ...text('from tab a') }),
            b: () => h.admit({ sessionId, ...text('from tab b') }),
          }),
        );
        expectForced(race, ['a', 'b'], outcomes);
        const a = (outcomes['a'] as { value: RunAdmissionResult }).value;
        const b = (outcomes['b'] as { value: RunAdmissionResult }).value;
        expect([a, b].filter((result) => result.queued === undefined)).to.have.length(1);
        expect([a, b].filter((result) => result.queued?.messageSeq === 1)).to.have.length(1);
        expect(a.run.runId).to.equal(b.run.runId);
        if (Array.isArray(release)) {
          expect((release[0] === 'a' ? a : b).queued, 'the first released starts the run').to.equal(undefined);
        }
        const active = (await runsOf(sessionId)).filter((run) => run.status === 'queued');
        expect(active).to.have.length(1);
        await expectValid(a.run);
      });
    }

    it('50 turns at once: one run, queued inputs numbered without a gap, the rest refused 429 at the rate window', async () => {
      const sessionId = await idleSession();
      const names = partyNames('t', 50);
      const race = new StoreRace(names);
      const outcomes = await race.run<RunAdmissionResult>(
        Object.fromEntries(names.map((name) => [name, () => h.admit({ sessionId, ...text(`turn ${name}`) })])),
      );
      expectForced(race, names, outcomes);
      // The idle session's first turn counted too: the window admits exactly what is left of it.
      const admittable = ADMISSION_QUOTAS.admissionsPerUser - 1;
      const admitted = winners(outcomes);
      expect(admitted, outcomes.map(codeOf).join()).to.have.length(admittable);
      expect(new Set(outcomes.filter((outcome) => !outcome.ok).map(statusOf))).to.deep.equal(new Set([429]));
      const results = admitted.map((outcome) => (outcome as { value: RunAdmissionResult }).value);
      expect(results.filter((result) => result.queued === undefined)).to.have.length(1);
      expect(results.flatMap((result) => result.queued?.messageSeq ?? []).sort((x, y) => x - y)).to.deep.equal(
        Array.from({ length: admittable - 1 }, (_, i) => i + 1),
      );
      expect(new Set(results.map((result) => result.run.runId)).size).to.equal(1);
      expect((await runsOf(sessionId)).filter((run) => run.status === 'queued')).to.have.length(1);
      await expectValid(results[0]!.run);
    });
  });

  describe('QA21-4 an idempotent resend racing the original on the chat route (LAT1 Idempotency-Key)', () => {
    for (const release of ordersOf('original', 'resend')) {
      it(`both responses stream the one run (release ${String(release)})`, async () => {
        const http = await startChatTurnHarness({
          publicSurface: 'web',
          stores: { admission: backend.admissionStore, runs: backend.executionStore, inputs: backend.inputs },
        });
        try {
          const send = (party: string) => async () => {
            const reply = await http.post(
              '/conversations/stream',
              'session',
              { query: 'What is the refund policy?', chatMode: 'internal_search' },
              { 'idempotency-key': 'resend-1', [PARTY_HEADER]: party },
            );
            return { status: reply.status, frames: sseFrames(await reply.text()) };
          };
          const race = new StoreRace(['original', 'resend'], { release });
          const outcomes = await race.run({ original: send('original'), resend: send('resend') });
          expectForced(race, ['original', 'resend'], outcomes);
          const replies = outcomes.map((outcome) => (outcome as { value: { status: number; frames: ReturnType<typeof sseFrames> } }).value);
          expect(replies.map((reply) => reply.status)).to.deep.equal([200, 200]);
          const created = replies.map((reply) => reply.frames[0]?.data['value'] as Record<string, unknown>);
          expect(created[0]?.['runId']).to.be.a('string');
          expect(created[1]).to.deep.equal(created[0]);
          expect(replies.map((reply) => reply.frames.at(-1)?.data['type'])).to.deep.equal(['RUN_FINISHED', 'RUN_FINISHED']);
          const accepted = http.lines.filter(([, message]) => message === 'admission.accepted').map(([, , meta]) => meta['replayed']);
          expect(accepted.sort()).to.deep.equal([false, true]);
          expect(await AgentRunModel.countDocuments({})).to.equal(1);
          expect(await ChatSession.countDocuments({})).to.equal(1);
        } finally {
          await http.close();
        }
      });
    }
  });

  describe('QA21-9 a failure at each operation of the admission transaction (ADM-17, ADM-G4)', () => {
    // One admission per operation of the transaction: more than a user's rate window holds, which is not what is tested here.
    beforeEach(() =>
      harness({ quotas: { ...ADMISSION_QUOTAS, admissionsPerUser: 10_000, admissionsPerOrg: 10_000 } as AdmissionQuotas }),
    );

    /** The in-transaction operations of one turn on an idle session that read history. */
    const operationsOfOneTurn = async (sessionId: string) => {
      const counting = failAt('turn', Number.MAX_SAFE_INTEGER, 'after', () => new Error('never'));
      try {
        const admitted = await asParty('turn', () => h.admit({ sessionId, ...text('counted') }));
        await cancel(admitted.run);
        return counting.ops.length;
      } finally {
        counting.disarm();
      }
    };

    it('failed after any operation, nothing is visible; the retry admits once', async () => {
      const sessionId = await idleSession();
      const total = await operationsOfOneTurn(sessionId);
      // What is left after PERF-E3 put the session's writes and the stored history in the transaction's batch.
      expect(total, 'operations in the admission transaction').to.be.at.least(9);
      for (let nth = 1; nth <= total; nth += 1) {
        const before = await databaseState();
        const failpoint = failAt('turn', nth, 'after', () => injectedFailure(false));
        const key = `fail-${String(nth)}`;
        try {
          const failed = await asParty('turn', () => h.admit({ sessionId, idempotencyKey: key, ...text(`try ${String(nth)}`) })).then(
            () => null,
            (error: unknown) => error,
          );
          expect(failpoint.fired, `failpoint ${String(nth)} fired`).to.equal(true);
          expect(failed, `operation ${String(nth)}: ${failpoint.ops[nth - 1]?.collection}.${failpoint.ops[nth - 1]?.method}`).to.not.equal(null);
        } finally {
          failpoint.disarm();
        }
        expect(await databaseState(), `nothing visible after operation ${String(nth)}`).to.deep.equal(before);
        const retried = await h.admit({ sessionId, idempotencyKey: key, ...text(`try ${String(nth)}`) });
        expect(retried.created).to.equal(true);
        expect(await AgentRunModel.countDocuments({ sessionId, runId: retried.run.runId })).to.equal(1);
        await cancel(retried.run);
      }
      await expectValid({ orgId: h.tenant.orgId, sessionId });
    });

    it('a transient failure after any operation is retried by the transaction: one run, one receipt', async () => {
      const sessionId = await idleSession();
      const total = await operationsOfOneTurn(sessionId);
      for (let nth = 1; nth <= total; nth += 1) {
        const failpoint = failAt('turn', nth, 'after', () => injectedFailure(true));
        let admitted: RunAdmissionResult;
        try {
          admitted = await asParty('turn', () => h.admit({ sessionId, idempotencyKey: `transient-${String(nth)}`, ...text(`t ${String(nth)}`) }));
        } finally {
          failpoint.disarm();
        }
        expect(failpoint.fired).to.equal(true);
        expect(admitted.created).to.equal(true);
        expect(await AgentRunModel.countDocuments({ sessionId, runId: admitted.run.runId })).to.equal(1);
        expect(await AgentAdmissionReceiptModel.countDocuments({ 'result.run.runId': admitted.run.runId })).to.equal(1);
        expect(await backend.dispatches(h.tenant.orgId, admitted.run.runId)).to.have.length(1);
        await cancel(admitted.run);
      }
      await expectValid({ orgId: h.tenant.orgId, sessionId });
    });
  });

  describe('QA21-10 the imported history blob of a retried admission (ADM-18, R3 M8, IX-ADM-JRN-4, IX-G21)', () => {
    const payloads = new MongoPayloadStore();
    /** A conversation the legacy path wrote, long enough to be sealed into the payload store on import. */
    const legacyConversation = async (): Promise<string> => {
      const session = await ChatSession.create({
        orgId: h.tenant.orgId,
        userId: h.tenant.actorId,
        initiator: h.tenant.actorId,
        title: 'Legacy conversation',
        sessionType: 'chat',
        status: 'Complete',
      });
      const sessionId = String(session._id);
      await appendMessages(sessionId, h.tenant.orgId, [
        { messageType: 'user_query', content: 'Summarise the handbook' },
        { messageType: 'bot_response', content: 'x'.repeat(4096), citations: [] },
      ] as unknown as IMessage[]);
      return sessionId;
    };
    /**
     * The admission transaction fails right after the blob was written in it, once: after the write that carried
     * it (the transaction's batch since PERF-E3, the last of its operations).
     */
    const failAfterBlob = (transient: boolean) => {
      let fired = false;
      const remove = probeStore((op, phase) => {
        if (op.party !== 'turn' || fired || !op.inTransaction || phase !== 'after') return;
        if (op.write && op.collections.includes('agentJournalPayloads')) {
          fired = true;
          throw injectedFailure(transient);
        }
      });
      return { remove, fired: () => fired };
    };
    const blobsOf = (sessionId: string) =>
      AgentJournalPayloadModel.find({ 'owner.sessionId': sessionId }, { referenced: 1, owner: 1 }).lean();
    const collect = () => new MongoRetentionStore().collectPayloads(Date.now() + 60_000, 100);

    beforeEach(() =>
      harness({ payloadReferences: true, importContent: new SessionImportContent(testCipher(), payloads, 64) }),
    );

    it('a transaction retried after the blob write keeps one blob, the cited one, marked referenced; the sweep keeps it after the run ended', async () => {
      const sessionId = await legacyConversation();
      const failing = failAfterBlob(true);
      let admitted: RunAdmissionResult;
      try {
        admitted = await asParty('turn', () => h.admit({ sessionId, ...text('Shorter, please') }));
      } finally {
        failing.remove();
      }
      expect(failing.fired()).to.equal(true);
      expect(admitted.imported?.sealed).to.equal(true);
      const blobs = await blobsOf(sessionId);
      // The import the journal cites, and the history the run names (PERF-B): one of each, never a retry's leftover.
      expect(blobs.map((blob) => [blob.owner?.runId, blob.referenced])).to.deep.equal([
        [admitted.run.runId, true],
        [admitted.run.runId, true],
      ]);
      const cited = (await backend.journal.readStream(h.tenant.orgId, sessionId, 0, 50)).find(
        (event) => event.type === 'session.imported',
      );
      const importRef = (cited?.data?.['transcriptRef'] as { ref: string }).ref;
      const named = (await backend.executionStore.readClaimSubject(h.tenant.orgId, admitted.run.runId))?.history?.parts ?? [];
      expect(blobs.filter((blob) => importRef.includes(blob._id))).to.have.length(1);
      expect(blobs.filter((blob) => named.some((part) => part.includes(blob._id)))).to.have.length(1);
      await cancel(admitted.run);
      expect(await collect(), 'the imported history outlives the run that imported it').to.equal(0);
      expect(await blobsOf(sessionId)).to.have.length(2);
      await expectValid(admitted.run);
    });

    it('an admission that failed after the blob write leaves no run and no blob', async () => {
      const sessionId = await legacyConversation();
      const failing = failAfterBlob(false);
      try {
        const refused = await asParty('turn', () => h.admit({ sessionId, ...text('Shorter, please') })).then(
          () => null,
          (error: unknown) => error,
        );
        expect(refused).to.not.equal(null);
      } finally {
        failing.remove();
      }
      expect(failing.fired()).to.equal(true);
      expect(await runsOf(sessionId)).to.deep.equal([]);
      expect(await blobsOf(sessionId)).to.deep.equal([]);
    });

    it('a message joining the running run, claim-checked in the admission transaction: referenced, kept after the run ended', async () => {
      await harness({ payloadReferences: true, journalInlineMaxBytes: 1_024 });
      const { run, sessionId } = await h.admit(text('first question'));
      const long = 'please also cover exchanges '.repeat(100);
      const queued = await h.admit({ sessionId, ...text(long) });
      expect(queued.queued?.messageSeq).to.equal(1);
      const [event] = (await backend.journal.readStream(h.tenant.orgId, sessionId, 0, 50)).filter(
        (each) => each.type === 'input.queued',
      );
      expect(event?.payloadRef, 'the message is claim-checked').to.not.equal(undefined);
      expect((await blobsOf(sessionId)).map((blob) => blob.referenced)).to.deep.equal([true]);
      await cancel((await h.service.get(run.orgId, run.runId)) as HarnessRun);
      expect(await collect()).to.equal(0);
      const [resolved] = await new JournalService(payloads, testCipher()).resolve([event!]);
      expect(resolved?.data?.['messageSeq'], 'the message reads back').to.equal(1);
      await expectValid(run);
    });
  });

  describe('QA21-11 the agent access check failing at admission refuses, never admits (IX-RTE-ADM-4, IX-G24)', () => {
    let server: Server;
    let answer: (res: express.Response) => void;

    before(async () => {
      const app = express();
      app.get('/api/v1/agent/:agentKey/run-definition', (_req, res) => answer(res));
      server = await new Promise<Server>((resolve) => {
        const listening = app.listen(0, '127.0.0.1', () => resolve(listening));
      });
    });
    after(() => new Promise<void>((resolve) => server.close(() => resolve())));

    const ANSWERS: Record<string, (res: express.Response) => void> = {
      'a 500': (res) => res.status(500).json({ error: 'boom' }),
      'a 200 without a definition': (res) => res.status(200).json({}),
      'another agent': (res) =>
        res.status(200).json({ definition: { agentKey: 'someone-else', tools: [], grants: {} } }),
      'a dropped connection': (res) => res.socket?.destroy(),
    };

    for (const [name, respond] of Object.entries(ANSWERS)) {
      it(`${name}: 503 unavailable, and the database is as it was`, async () => {
        answer = respond;
        const base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
        h.setAgentAccess(new HarnessAgentAccessPolicy(() => base));
        const before = await databaseState();
        const outcome = await h
          .submit({ agentKey: 'agent-1', authorization: 'Bearer user', ...text('q') })
          .then(
            () => ({ party: 'turn', ok: true as const, value: null }),
            (error: unknown) => ({ party: 'turn', ok: false as const, error }),
          );
        expect(outcome.ok).to.equal(false);
        expect(statusOf(outcome)).to.equal(503);
        expect(await databaseState()).to.deep.equal(before);
      });
    }
  });

  describe('QA21-12 one service identity serving many people (ADM-21, ADM-G7, R1 F35)', () => {
    it('a Slack bot’s turns count against each person’s own quota: 31 people in a minute are all admitted', async () => {
      const people = partyNames('person', ADMISSION_QUOTAS.admissionsPerUser + 1).map((name) =>
        stableObjectIdHexForExternalEmail(`${name}@slack.example`),
      );
      for (const actorId of people) {
        const admitted = await h.admit({ actorId, via: 'service', ...text('hello') });
        expect(admitted.created).to.equal(true);
      }
      expect(await AgentRunModel.countDocuments({ orgId: h.tenant.orgId })).to.equal(people.length);
    });

    it('one person (a PAT, whoever calls with it) past 30 turns in a minute is refused 429 with Retry-After', async () => {
      const sessions: string[] = [];
      for (let i = 0; i < ADMISSION_QUOTAS.admissionsPerUser; i += 1) {
        const admitted = await h.admit({ via: 'pat', ...text(`turn ${String(i)}`) });
        sessions.push(admitted.sessionId);
        await cancel(admitted.run);
      }
      const refused = await h.admit({ via: 'pat', ...text('one too many') }).then(
        () => ({ party: 'pat', ok: true as const, value: null }),
        (error: unknown) => ({ party: 'pat', ok: false as const, error }),
      );
      expect(statusOf(refused)).to.equal(429);
      expect(codeOf(refused)).to.not.equal('ok');
      expect(await AgentRunModel.countDocuments({ orgId: h.tenant.orgId })).to.equal(sessions.length);
    });
  });

  describe('QA21-13 suggested next steps settling while a new turn arrives (UX5)', () => {
    const FOLLOWUPS = [{ text: 'What about refunds?' }, { text: 'And exchanges?' }];
    const executions = () => backend.executionStore as MongoExecutionStore;

    for (const release of ordersOf('worker', 'user')) {
      it(`a completed run keeps its suggestions; the new turn starts a run of its own (release ${String(release)})`, async () => {
        const sessionId = await idleSession();
        const [ended] = await runsOf(sessionId);
        // The hand-over is one write outside any transaction; the turn is held at its transaction's first read.
        const gate: Gate = (op) => op.inTransaction || (op.collection === 'agentRuns' && op.write);
        const race = new StoreRace(['worker', 'user'], { release, gate });
        const outcomes = byParty(
          await race.run<unknown>({
            worker: () => h.service.storeFollowups(h.tenant.orgId, ended!.runId, FOLLOWUPS),
            user: () => h.admit({ sessionId, ...text('next question') }),
          }),
        );
        expectForced(race, ['worker', 'user'], outcomes);
        expect(outcomes['worker']!.ok && outcomes['user']!.ok, `${codeOf(outcomes['worker']!)} ${codeOf(outcomes['user']!)}`).to.equal(true);
        const next = (outcomes['user'] as { value: RunAdmissionResult }).value;
        expect(next.created && next.queued === undefined).to.equal(true);
        const after = await executions().get(h.tenant.orgId, ended!.runId);
        expect(after?.followups).to.deep.equal(FOLLOWUPS);
        expect(after?.revision, 'suggestions never move the run').to.equal(ended!.revision);
        expect((await executions().get(h.tenant.orgId, next.run.runId))?.followups).to.equal(undefined);
        await expectValid(next.run);
      });

      it(`a turn sent as the run completes: taken by it or a run of its own, never lost; suggestions only for a completed run (release ${String(release)})`, async () => {
        const { run, sessionId } = await h.admit(text('first question'));
        const claimed = await h.claim(run, 'worker-a', 5_000);
        const complete = () =>
          h.service.commit({
            commandId: 'complete-1',
            orgId: run.orgId,
            runId: run.runId,
            workerId: 'worker-a',
            attemptId: claimed.attemptId as string,
            fence: claimed.fence,
            events: [],
            legacyEvents: [],
            transition: { status: 'completed', outcome: 'answer' },
            outbox: [],
          });
        const race = new StoreRace(['worker', 'user'], { release });
        const outcomes = byParty(
          await race.run<unknown>({ worker: complete, user: () => h.admit({ sessionId, ...text('one more thing') }) }),
        );
        expectForced(race, ['worker', 'user'], outcomes);
        expect(outcomes['user']!.ok, codeOf(outcomes['user']!)).to.equal(true);
        const user = outcomes['user'] as { ok: true; value: RunAdmissionResult };
        const worker = outcomes['worker']!;
        const settle = await h.service.storeFollowups(run.orgId, run.runId, FOLLOWUPS).then(
          () => ({ party: 'worker', ok: true as const, value: null }),
          (error: unknown) => ({ party: 'worker', ok: false as const, error }),
        );
        if (worker.ok) {
          expect(user.value.queued, 'after the ending the turn starts a run').to.equal(undefined);
          expect(user.value.run.runId).to.not.equal(run.runId);
          expect(settle.ok).to.equal(true);
        } else {
          expect(codeOf(worker)).to.equal('INPUT_PENDING');
          expect(user.value.queued?.messageSeq).to.equal(1);
          expect(user.value.run.runId).to.equal(run.runId);
          expect(codeOf(settle)).to.equal('RUN_NOT_COMPLETED');
        }
        if (Array.isArray(release)) expect(worker.ok).to.equal(release[0] === 'worker');
        await expectValid(run);
      });
    }
  });
});
