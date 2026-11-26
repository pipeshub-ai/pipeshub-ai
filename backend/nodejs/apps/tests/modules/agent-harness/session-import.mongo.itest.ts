/**
 * P1b T3 on a MongoDB replica set (run as execution-store.mongo.itest.ts
 * says): a durable admission imports the conversation's legacy turns into the
 * session journal (`session.imported`) in its transaction, and the legacy
 * path reads a durable session from the conversation it left (rollback).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { sessionMessages } from './support/session-messages';
import sinon from 'sinon';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import { appendMessages, formatPreviousConversations, updateMessageById } from '../../../src/modules/enterprise_search/utils/utils';
import { IMessage } from '../../../src/modules/enterprise_search/types/conversation.interfaces';
import * as controller from '../../../src/modules/enterprise_search/controller/es_controller';
import { ProjectService } from '../../../src/modules/projects/services/project.service';
import { Logger } from '../../../src/libs/services/logger.service';
import { HarnessRun, RunTransition } from '../../../src/modules/agent-harness/domain/execution';
import { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { RunAdmissionResult } from '../../../src/modules/agent-harness/domain/admission';
import { canonicalJsonBytes } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { inlineContent } from '../../../src/modules/agent-harness/projection/timeline';
import { SessionImportContent } from '../../../src/modules/agent-harness/application/session-import-content';
import { RunContentService } from '../../../src/modules/agent-harness/application/run-content-service';
import { MongoPayloadStore } from '../../../src/libs/repositories/agent-harness/payload-store/mongo';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import {
  admittedHistory,
  contractClock,
  ContractHarness,
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
import { createHash } from 'crypto';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };

const text = (query: string) => ({
  input: {
    query,
    recordIds: [],
    filters: {},
    attachments: [],
    model: { chatMode: 'auto' },
  },
});

type Turn = [question: string, answer: string];

const legacyMessages = (turns: Turn[]): IMessage[] =>
  turns.flatMap(
    ([question, answer]) =>
      [
        { messageType: 'user_query', content: question },
        { messageType: 'bot_response', content: answer, citations: [] },
      ] as unknown as IMessage[],
  );

/** What the legacy stream sends Python as history for this conversation now. */
const legacyHistory = async (sessionId: string) =>
  JSON.parse(
    JSON.stringify(formatPreviousConversations(await sessionMessages(sessionId))),
  ) as Array<Record<string, unknown>>;

interface Imported {
  event: JournalEvent;
  transcript: Array<Record<string, unknown>>;
}

describe('P1b T3 session import and rollback on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);
  afterEach(() => sinon.restore());

  const harness = (options: Omit<MongoBackendOptions, 'clock'> = {}): ContractHarness => {
    const clock = contractClock();
    const h = createContractHarness(mongoBackend({ ...options, clock }), clock);
    h.setRollout(V2);
    return h;
  };

  /** A conversation legacy started and wrote, never pinned to the harness. */
  const legacySession = async (h: ContractHarness, turns: Turn[]): Promise<string> => {
    const session = await ChatSession.create({
      orgId: h.tenant.orgId,
      userId: h.tenant.actorId,
      initiator: h.tenant.actorId,
      title: 'Legacy conversation',
      sessionType: 'chat',
      status: 'Complete',
    });
    const sessionId = String(session._id);
    await legacyTurns(h, sessionId, turns);
    return sessionId;
  };

  const legacyTurns = (h: ContractHarness, sessionId: string, turns: Turn[]) =>
    appendMessages(sessionId, h.tenant.orgId, legacyMessages(turns));

  const imports = async (h: ContractHarness, sessionId: string): Promise<Imported[]> =>
    (await h.backend.journal.readStream(h.tenant.orgId, sessionId, 0, 500))
      .filter((event) => event.type === 'session.imported')
      .map((event) => {
        const raw = inlineContent(event.data?.['transcriptRef']);
        return {
          event,
          transcript: raw === null ? [] : (JSON.parse(raw) as Array<Record<string, unknown>>),
        };
      });

  const streamTypes = async (h: ContractHarness, sessionId: string) =>
    (await h.backend.journal.readStream(h.tenant.orgId, sessionId, 0, 500)).map((e) => e.type);

  /** The run claimed and completed with `answer` (the transcript lands in the commit). */
  const answer = async (h: ContractHarness, run: HarnessRun, reply: string): Promise<void> => {
    const claimed = await h.claim(run);
    await h.service.commit({
      commandId: `${run.runId}:answer`,
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'worker-a',
      attemptId: 'attempt-worker-a',
      fence: claimed.fence,
      events: [
        workerEvent(
          'x.pipeshub.answer.finalized',
          { outcome: 'answered', answer: reply, partTexts: [reply], citations: [], confidence: null },
          { critical: false },
        ),
      ],
      legacyEvents: [],
      transition: { status: 'completed', outcome: 'answer' },
      outbox: [],
    });
  };

  const ledgerOf = async (sessionId: string) =>
    ((await ChatSession.findById(sessionId).select('+harnessLedger').lean()) as {
      harnessLedger?: { seq: number; digest: string; diverged?: boolean };
    } | null)?.harnessLedger;

  describe('import on the first durable run in a legacy session (MG-1)', () => {
    it('MG-1a: journals session.imported before run.admitted, with the history legacy sends, its digest and its count', async () => {
      const h = harness();
      const sessionId = await legacySession(h, [
        ['What is the leave policy?', 'Twenty-five days.'],
        ['And for contractors?', 'Fifteen days.'],
      ]);
      const expected = await legacyHistory(sessionId);
      const admitted = await h.admit({ sessionId, ...text('And sick leave?') });

      expect(await streamTypes(h, sessionId)).to.deep.equal([
        'session.imported',
        'x.pipeshub.history.labelled',
        'run.admitted',
      ]);
      const [imported] = await imports(h, sessionId);
      expect(imported!.transcript).to.deep.equal(expected);
      expect(await admittedHistory(h.backend, admitted.run)).to.deep.equal(expected);
      const bytes = canonicalJsonBytes(expected);
      const digest = `sha256:${createHash('sha256').update(bytes).digest('hex')}`;
      expect(imported!.event.data).to.deep.include({
        upToMessageSeq: 4,
        transcriptDigest: digest,
        messages: 4,
      });
      expect(imported!.event.data?.['replacesHistory']).to.equal(undefined);
      expect(imported!.event.writer).to.equal('node');
      expect(imported!.event.runId).to.equal(admitted.run.runId);
      expect(admitted.imported).to.deep.include({ upToMessageSeq: 4, messages: 4, digest, sealed: false });
      // The new user turn is accounted for with the import: the ledger covers seq 5.
      expect((await ledgerOf(sessionId))?.seq).to.equal(5);
      // UX2 VC-5: legacy answers recorded nothing of what their model read, so the import is unknown context.
      const label = (await h.backend.journal.readStream(h.tenant.orgId, sessionId, 0, 100)).find(
        (e) => e.type === 'x.pipeshub.history.labelled',
      );
      expect(label?.writer).to.equal('node');
      expect(label?.critical).to.equal(false);
      expect(label?.data).to.deep.equal({ transcriptDigest: digest, label: { complete: false, sources: [] } });
    });

    it('UX2 VC-5: an import whose answers a durable run wrote carries their labels, read from Mongo', async () => {
      const h = harness();
      const sessionId = await legacySession(h, [['What is the leave policy?', 'Twenty-five days.']]);
      await ChatSessionMessage.updateMany(
        { sessionId, messageType: 'bot_response' },
        { $set: { label: { complete: true, sources: [{ kind: 'indexed', recordId: 'rec-1' }] } } },
      );
      await h.admit({ sessionId, ...text('And sick leave?') });
      const label = (await h.backend.journal.readStream(h.tenant.orgId, sessionId, 0, 100)).find(
        (e) => e.type === 'x.pipeshub.history.labelled',
      );
      expect(label?.data?.['label']).to.deep.equal({ complete: true, sources: [{ kind: 'indexed', recordId: 'rec-1' }] });
    });

    it('MG-1a: a new session and a legacy session with nothing a model sees journal no import', async () => {
      const h = harness();
      const fresh = await h.admit(text('First question'));
      expect(await streamTypes(h, fresh.sessionId)).to.deep.equal(['run.admitted']);
      expect(fresh.imported).to.equal(undefined);

      const empty = await legacySession(h, []);
      await appendMessages(empty, h.tenant.orgId, [
        { messageType: 'error', content: 'The model was unavailable' } as unknown as IMessage,
      ]);
      const admitted = await h.admit({ sessionId: empty, ...text('Try again') });
      expect(await streamTypes(h, empty)).to.deep.equal(['run.admitted']);
      expect(admitted.imported).to.equal(undefined);
    });

    it('MG-1c: a history over the inline limit is sealed content the run reads back; the digest is its hash', async () => {
      const payloads = new MongoPayloadStore();
      const h = harness({ importContent: new SessionImportContent(testCipher(), payloads, 64) });
      const sessionId = await legacySession(h, [['Summarise the handbook', 'x'.repeat(4096)]]);
      const expected = await legacyHistory(sessionId);
      const admitted = await h.admit({ sessionId, ...text('Shorter, please') });
      const [event] = (await h.backend.journal.readStream(h.tenant.orgId, sessionId, 0, 10)).filter(
        (e) => e.type === 'session.imported',
      );
      const ref = event!.data?.['transcriptRef'] as { ref: string; hash: string; size: number };
      expect(ref.ref.startsWith('inline:')).to.equal(false);
      expect(admitted.imported?.sealed).to.equal(true);

      const claimed = await h.claim(admitted.run);
      const content = new RunContentService(h.service, testCipher(), payloads);
      const opened = await content.get(
        {
          orgId: claimed.orgId,
          runId: claimed.runId,
          attemptId: claimed.attemptId!,
          fence: claimed.fence,
          workerId: claimed.workerId!,
        },
        ref.ref,
      );
      expect(JSON.parse(opened.toString('utf8'))).to.deep.equal(expected);
      expect(`sha256:${createHash('sha256').update(opened).digest('hex')}`).to.equal(ref.hash);
      expect(event!.data?.['transcriptDigest']).to.equal(ref.hash);
      expect(ref.size).to.equal(opened.length);
    });

    it('MG-1c: an import that cannot be stored refuses the admission; the transaction leaves nothing', async () => {
      // No content store: a transcript over the inline limit cannot be kept.
      const h = harness();
      const sessionId = await legacySession(h, [['Summarise the handbook', 'x'.repeat(200 * 1024)]]);
      let refused: unknown = null;
      try {
        await h.admit({ sessionId, ...text('Shorter, please') });
      } catch (error) {
        refused = error;
      }
      expect(refused).to.be.instanceOf(HarnessConflictError);
      expect((refused as HarnessConflictError).code).to.equal('SESSION_HISTORY_TOO_LARGE');
      expect(await streamTypes(h, sessionId)).to.deep.equal([]);
      expect(await ChatSessionMessage.countDocuments({ sessionId })).to.equal(2);
      expect(await ledgerOf(sessionId)).to.equal(undefined);
    });
  });

  describe('idempotent under retry and re-claim; mismatches re-import (MG-2)', () => {
    it('MG-2a: an admission retried with the same key replays its receipt: one import', async () => {
      const h = harness();
      const sessionId = await legacySession(h, [['q1', 'a1']]);
      const request = { sessionId, idempotencyKey: 'retry-1', ...text('q2') };
      const first = await h.admit(request);
      const replay = await h.admit(request);
      expect(replay.created).to.equal(false);
      expect(replay.run.runId).to.equal(first.run.runId);
      expect(await imports(h, sessionId)).to.have.length(1);
    });

    it('MG-2a: two first messages at once: one run, one import', async () => {
      const h = harness();
      const sessionId = await legacySession(h, [['q1', 'a1']]);
      const outcomes = await Promise.allSettled([
        h.admit({ sessionId, ...text('q2 from tab one') }),
        h.admit({ sessionId, ...text('q2 from tab two') }),
      ]);
      const admitted = outcomes
        .filter((o): o is PromiseFulfilledResult<RunAdmissionResult> => o.status === 'fulfilled')
        .map((o) => o.value);
      const started = admitted.filter((a) => a.queued === undefined);
      expect(started).to.have.length(1);
      for (const other of outcomes.filter((o) => o.status === 'rejected')) {
        expect((other as PromiseRejectedResult).reason).to.be.instanceOf(HarnessConflictError);
      }
      expect(await imports(h, sessionId)).to.have.length(1);
      expect(await streamTypes(h, sessionId).then((t) => t.filter((x) => x === 'run.admitted'))).to.have.length(1);
    });

    it('MG-2b: a re-claimed run reads the same journal; nothing is imported again', async () => {
      const h = harness();
      const sessionId = await legacySession(h, [['q1', 'a1']]);
      const admitted = await h.admit({ sessionId, ...text('q2') });
      const before = await h.backend.journal.readStream(h.tenant.orgId, sessionId, 0, 100);
      await h.claim(admitted.run, 'worker-a', 100);
      // The contract clock starts at 1_000: the lease lapses at 1_100.
      h.setNow(1_101);
      expect(await h.service.sweep(10)).to.equal(1);
      const reclaimed = await h.claim(admitted.run, 'worker-b');
      expect(reclaimed.workerId).to.equal('worker-b');
      const after = await h.backend.journal.readStream(h.tenant.orgId, sessionId, 0, 100);
      expect(after.filter((e) => e.type === 'session.imported')).to.deep.equal(
        before.filter((e) => e.type === 'session.imported'),
      );
    });

    it('MG-2c: a covered answer legacy regenerated: import_mismatch{changed}; the next run gets the conversation as it is, once', async () => {
      const h = harness();
      const sessionId = await legacySession(h, [['q1', 'a1'], ['q2', 'a2']]);
      const first = await h.admit({ sessionId, ...text('q3') });
      await answer(h, first.run, 'a3');
      const a2 = await ChatSessionMessage.findOne({ sessionId, seq: 4 }).lean();
      await updateMessageById(String(a2!._id), a2!.orgId, {
        messageType: 'bot_response',
        content: 'a2, regenerated',
        citations: [],
      } as unknown as IMessage);

      const expected = await legacyHistory(sessionId);
      const second = await h.admit({ sessionId, ...text('q4') });
      expect(second.imported).to.deep.include({ mismatch: 'changed', replacesHistory: true, messages: expected.length });
      const all = await imports(h, sessionId);
      expect(all).to.have.length(2);
      expect(all[1]!.event.data?.['replacesHistory']).to.equal(true);
      expect(all[1]!.transcript).to.deep.equal(expected);
      expect(all[1]!.transcript.map((t) => t['content'])).to.include('a2, regenerated');

      await answer(h, second.run, 'a4');
      const third = await h.admit({ sessionId, ...text('q5') });
      expect(third.imported).to.equal(undefined);
      expect(await imports(h, sessionId)).to.have.length(2);
    });

    it('MG-2c: a covered message removed re-imports the conversation without it', async () => {
      const h = harness();
      const sessionId = await legacySession(h, [['q1', 'a1'], ['q2', 'a2']]);
      const first = await h.admit({ sessionId, ...text('q3') });
      await answer(h, first.run, 'a3');
      await ChatSessionMessage.deleteMany({ sessionId, seq: { $in: [3, 4] } });
      const second = await h.admit({ sessionId, ...text('q4') });
      expect(second.imported?.mismatch).to.equal('changed');
      const [, reimported] = await imports(h, sessionId);
      expect(reimported!.transcript.map((t) => t['content'])).to.deep.equal(['q1', 'a1', 'q3', 'a3']);
    });

    it('MG-2d: a legacy turn written while a durable run was active: interleaved, re-imported whole', async () => {
      const h = harness();
      const first = await h.admit(text('q1'));
      const sessionId = first.sessionId;
      await legacyTurns(h, sessionId, [['legacy q', 'legacy a']]);
      await answer(h, first.run, 'a1');
      expect((await ledgerOf(sessionId))?.diverged).to.equal(true);

      const expected = await legacyHistory(sessionId);
      const second = await h.admit({ sessionId, ...text('q2') });
      expect(second.imported).to.deep.include({ mismatch: 'interleaved', replacesHistory: true });
      const [reimported] = await imports(h, sessionId);
      expect(reimported!.transcript).to.deep.equal(expected);
      expect((await ledgerOf(sessionId))?.diverged).to.equal(undefined);
    });

    it('MG-2d: a durable session from before T3 (no ledger): untracked, re-imported whole', async () => {
      const h = harness();
      const first = await h.admit(text('q1'));
      await answer(h, first.run, 'a1');
      await ChatSession.updateOne({ _id: first.sessionId }, { $unset: { harnessLedger: 1 } });
      const second = await h.admit({ sessionId: first.sessionId, ...text('q2') });
      expect(second.imported).to.deep.include({ mismatch: 'untracked', replacesHistory: true, messages: 2 });
    });
  });

  describe('rollback and back (MG-3, MG-4, MG-5)', () => {
    const owner = (h: ContractHarness, sessionId: string): never =>
      ({
        headers: { authorization: `Bearer ${h.tenant.actorId}` },
        params: { conversationId: sessionId },
        body: {},
        query: {},
        user: { userId: h.tenant.actorId, orgId: h.tenant.orgId, email: 'owner@example.com' },
        context: { requestId: 'req-rollback' },
      }) as never;

    /** The conversation GET, which never consults the rollout, as the page receives it. */
    const conversationGet = async (h: ContractHarness, sessionId: string) => {
      sinon.restore();
      sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([]);
      sinon.stub(Logger.getInstance(), 'debug');
      let body: unknown;
      const res = {
        status: () => res,
        json: (value: unknown) => {
          body = value;
          return res;
        },
      };
      const next = sinon.stub();
      await controller.getConversationById({ aiBackend: 'http://ai.test' } as never)(owner(h, sessionId), res as never, next as never);
      expect(next.called, String(next.firstCall?.args[0])).to.equal(false);
      return (
        body as {
          conversation: { status: string; modelInfo?: { chatMode?: string }; messages: Array<Record<string, unknown>> };
        }
      ).conversation;
    };

    const legacyGet = async (h: ContractHarness, sessionId: string) => {
      const conversation = await conversationGet(h, sessionId);
      return {
        status: conversation.status,
        messages: conversation.messages.map((m) => ({ messageType: m['messageType'], content: m['content'], parts: m['parts'] })),
      };
    };

    it('MG-3f: an attachment crosses the boundary in history: legacy to the durable import, durable to legacy', async () => {
      const h = harness();
      const file = { recordId: 'rec-1', recordName: 'handbook.pdf', mimeType: 'application/pdf', extension: 'pdf', source: 'upload' };
      const sessionId = await legacySession(h, []);
      await appendMessages(sessionId, h.tenant.orgId, [
        { messageType: 'user_query', content: 'Summarise this', attachments: [file] },
        { messageType: 'bot_response', content: 'It covers leave.', citations: [] },
      ] as unknown as IMessage[]);
      const first = await h.admit({ sessionId, ...text('And travel?') });
      const [imported] = await imports(h, sessionId);
      expect(imported!.transcript[0]).to.deep.include({ role: 'user_query', attachments: [file] });

      const second = { ...text('Compare with this one'), sessionId };
      second.input = { ...second.input, attachments: [{ ...file, recordId: 'rec-2', recordName: 'policy.pdf' }] as never };
      await answer(h, first.run, 'Travel is booked centrally.');
      await h.admit(second);
      const history = await legacyHistory(sessionId);
      expect(history.at(-1)).to.deep.include({
        role: 'user_query',
        content: 'Compare with this one',
        attachments: [{ ...file, recordId: 'rec-2', recordName: 'policy.pdf' }],
      });
    });

    it('QA6-F5-3: a conversation that stored a mode no route accepts reads in the default mode; the document keeps its own', async () => {
      const h = harness();
      const served = async (stored: string): Promise<string | undefined> => {
        const sessionId = await legacySession(h, [['q1', 'a1']]);
        await ChatSession.updateOne({ _id: sessionId }, { $set: { modelInfo: { modelKey: 'm1', chatMode: stored } } });
        const read = (await conversationGet(h, sessionId)).modelInfo?.chatMode;
        const kept = await ChatSession.findById(sessionId).lean();
        expect((kept?.modelInfo as { chatMode?: string } | undefined)?.chatMode, 'the stored mode').to.equal(stored);
        return read;
      };
      expect(await served('image')).to.equal('internal_search');
      expect(await served('agent:planExecute')).to.equal('agent');
      expect(await served('web_search')).to.equal('web_search');
      expect(await served('quick')).to.equal('quick');
    });

    it('MG-4a: back on durable with no legacy turn meanwhile: no import', async () => {
      const h = harness();
      const sessionId = await legacySession(h, [['q1', 'a1']]);
      const first = await h.admit({ sessionId, ...text('q2') });
      await answer(h, first.run, 'a2');
      const second = await h.admit({ sessionId, ...text('q3') });
      expect(second.imported).to.equal(undefined);
      expect(await imports(h, sessionId)).to.have.length(1);
    });

    it('MG-3d: a run suspended at rollback reads as in progress; the legacy turn meanwhile reaches the next durable run', async () => {
      const h = harness();
      const first = await h.admit(text('Delete the old drafts'));
      const sessionId = first.sessionId;
      const claimed = await h.claim(first.run);
      await h.service.commit({
        commandId: 'suspend-1',
        orgId: claimed.orgId,
        runId: claimed.runId,
        workerId: 'worker-a',
        attemptId: 'attempt-worker-a',
        fence: claimed.fence,
        events: [
          workerEvent('interaction.requested', {
            interactionId: 'ix-1',
            kind: 'approval',
            bindingDigest: `sha256:${'1'.repeat(64)}`,
          }),
        ],
        legacyEvents: [],
        transition: {
          status: 'waiting_approval',
          detail: { kind: 'suspend', reason: 'approval', interactionId: 'ix-1' },
        } as RunTransition,
        outbox: [],
      });
      expect((await legacyGet(h, sessionId)).status).to.equal('Inprogress');

      // Rolled back: legacy answers the next message in the same conversation.
      await legacyTurns(h, sessionId, [['What is on the calendar?', 'Two meetings.']]);
      const run = await h.service.get(first.run.orgId, first.run.runId);
      await h.service.control({
        commandId: 'cancel-suspended',
        dispatchCommandId: 'unused-dispatch',
        orgId: first.run.orgId,
        runId: first.run.runId,
        expectedRevision: run!.revision,
        action: 'cancel',
      });

      const next = await h.admit({ sessionId, ...text('Anything else?') });
      const last = (await imports(h, sessionId)).at(-1)!;
      expect(next.imported).to.not.equal(undefined);
      expect(last.transcript.map((t) => t['content'])).to.include.members([
        'What is on the calendar?',
        'Two meetings.',
      ]);
    });

    it('MG-5: legacy → durable → legacy → durable reads the same at every step; each run is given the conversation once', async () => {
      const h = harness();
      const sessionId = await legacySession(h, [['q1', 'a1']]);
      const snapshots = [await legacyGet(h, sessionId)];

      const first = await h.admit({ sessionId, ...text('q2') });
      await answer(h, first.run, 'a2');
      snapshots.push(await legacyGet(h, sessionId));

      await legacyTurns(h, sessionId, [['q3 on legacy', 'a3 on legacy']]);
      snapshots.push(await legacyGet(h, sessionId));

      const historyBefore = await legacyHistory(sessionId);
      const second = await h.admit({ sessionId, ...text('q4') });
      await answer(h, second.run, 'a4');
      snapshots.push(await legacyGet(h, sessionId));

      for (let i = 1; i < snapshots.length; i += 1) {
        const [earlier, later] = [snapshots[i - 1]!, snapshots[i]!];
        expect(later.messages.slice(0, earlier.messages.length), `step ${i}`).to.deep.equal(earlier.messages);
        expect(later.status).to.equal('Complete');
      }
      expect(snapshots.at(-1)!.messages.map((m) => m.content)).to.deep.equal([
        'q1', 'a1', 'q2', 'a2', 'q3 on legacy', 'a3 on legacy', 'q4', 'a4',
      ]);

      // The model is given each turn once: the legacy turns imported where
      // the run that first needed them starts; durable turns from the journal.
      const all = await imports(h, sessionId);
      expect(all.map((i) => i.transcript.map((t) => t['content']))).to.deep.equal([
        ['q1', 'a1'],
        ['q3 on legacy', 'a3 on legacy'],
      ]);
      expect(all.map((i) => i.event.data?.['replacesHistory'])).to.deep.equal([undefined, undefined]);
      expect(await admittedHistory(h.backend, second.run)).to.deep.equal(historyBefore);
      const order = (await h.backend.journal.readStream(h.tenant.orgId, sessionId, 0, 500))
        .filter((e) => ['session.imported', 'run.admitted', 'run.completed'].includes(e.type))
        .map((e) => [e.type, e.runId === first.run.runId ? 'first' : 'second']);
      expect(order).to.deep.equal([
        ['session.imported', 'first'],
        ['run.admitted', 'first'],
        ['run.completed', 'first'],
        ['session.imported', 'second'],
        ['run.admitted', 'second'],
        ['run.completed', 'second'],
      ]);
    });
  });
});
