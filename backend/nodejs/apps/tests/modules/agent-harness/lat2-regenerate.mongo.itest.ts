/**
 * LAT2 RQ-7..RQ-9 on a MongoDB replica set: a regenerate sent to the
 * existing `…/message/:messageId/regenerate` route is admitted and streamed
 * on the same response; the stored question is asked again and the old
 * answer leaves the conversation, as UX3's two-request regenerate did.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { sessionMessages } from './support/session-messages';
import sinon from 'sinon';
import { Types } from 'mongoose';
import { appendMessages } from '../../../src/modules/enterprise_search/utils/utils';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { IMessage } from '../../../src/modules/enterprise_search/types/conversation.interfaces';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { contractClock, ContractHarness, createContractHarness } from './support/execution-store-contract';
import { connectHarnessMongo, disconnectHarnessMongo, mongoBackend, resetHarnessMongo } from './support/harness-mongo';
import { HARNESS_ENABLED } from './support/rollout';
import { workerEvent } from './support/journal-fixtures';
import { testRunKeys } from './support/data-protection';
import { ChatTurnHarness, logged, ORG, sseFrames, startChatTurnHarness, USER } from './support/chat-turn-harness';
import { persistedRun } from '../../contracts/support/surfaces';
import { SessionRunService } from '../../../src/modules/agent-harness/application/session-run-service';
import { ChatSessionRepository } from '../../../src/modules/agent-harness/ports/chat-session-repository';

const TURN = '3f1c2a8e-9d0b-4c5e-8a7f-112233445566';

const text = (query: string) => ({
  orgId: ORG,
  actorId: USER,
  input: { query, recordIds: [], filters: {}, attachments: [], model: { chatMode: 'internal_search' } },
});

describe('LAT2 regenerate on the existing route, on a MongoDB replica set', function () {
  this.timeout(60_000);
  let conversation: Record<string, unknown>;
  let h: ContractHarness;
  let http: ChatTurnHarness | undefined;

  before(async () => {
    conversation = (await persistedRun('chat_answer_with_citations')).conversation;
    sinon.restore();
  });
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(async () => {
    await resetHarnessMongo();
    const clock = contractClock();
    clock.set(Date.now());
    h = createContractHarness(mongoBackend({ clock }), clock);
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
    http = await startChatTurnHarness({
      conversation,
      publicSurface: 'web',
      stores: { admission: h.backend.admissionStore, runs: h.backend.executionStore, journal: h.backend.journal, inputs: h.backend.inputs },
    });
  });
  afterEach(async () => {
    await http?.close();
    http = undefined;
    sinon.restore();
  });

  const answer = async (run: HarnessRun, reply: string): Promise<void> => {
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

  const messages = async (sessionId: string) =>
    ((await sessionMessages(sessionId)) as Array<IMessage & { _id: unknown }>).map((m) => [m.messageType, m.content]);

  const lastAnswerId = async (sessionId: string): Promise<string> => {
    const all = (await sessionMessages(sessionId)) as Array<IMessage & { _id: unknown }>;
    return String([...all].reverse().find((m) => m.messageType === 'bot_response')?._id);
  };

  /** A durable conversation with two answered turns. */
  const twoTurns = async (): Promise<string> => {
    const first = await h.admit(text('What is the leave policy?'));
    await answer(first.run, 'Twenty-five days.');
    const second = await h.admit({ sessionId: first.sessionId, ...text('And for contractors?') });
    await answer(second.run, 'Ten days.');
    return first.sessionId;
  };

  const regenerate = (sessionId: string, messageId: string, headers: Record<string, string> = {}) =>
    http!.post(
      `/conversations/${sessionId}/message/${messageId}/regenerate`,
      'session',
      { chatMode: 'internal_search', modelKey: 'model-1', runId: TURN },
      headers,
    );

  it('RQ-7: the stored question is asked again on the same response; the old answer leaves the conversation', async () => {
    const sessionId = await twoTurns();
    const reply = await regenerate(sessionId, await lastAnswerId(sessionId));
    expect(reply.status).to.equal(200);
    const frames = sseFrames(await reply.text());
    expect(frames[0]?.data).to.deep.include({ type: 'CUSTOM', name: 'conversation_created' });
    const { runId } = frames[0]?.data['value'] as { runId: string };
    expect(frames[frames.length - 1]?.data['type']).to.equal('RUN_FINISHED');
    expect(http!.legacy.called).to.equal(false);
    const run = await h.service.get(ORG, runId);
    expect(run?.input?.query).to.equal('And for contractors?');
    // Admission stores the turn's key digested (QA-15F).
    expect(run?.idempotencyKey).to.equal(testRunKeys().digest(ORG, `turn:${TURN}`));
    expect(await messages(sessionId)).to.deep.equal([
      ['user_query', 'What is the leave policy?'],
      ['bot_response', 'Twenty-five days.'],
      ['user_query', 'And for contractors?'],
    ]);
  });

  it('RQ-8: the same regenerate resent streams the same run and removes nothing more', async () => {
    const sessionId = await twoTurns();
    const answerId = await lastAnswerId(sessionId);
    const first = sseFrames(await (await regenerate(sessionId, answerId)).text());
    const again = sseFrames(await (await regenerate(sessionId, answerId)).text());
    expect(again[0]?.data['value']).to.deep.equal(first[0]?.data['value']);
    expect(logged(http!.lines, 'admission.accepted').map((line) => line['replayed'])).to.deep.equal([false, true]);
    expect((await messages(sessionId)).filter(([type]) => type === 'user_query')).to.have.length(2);
  });

  it('RQ-9: while a run is active, regenerate answers 409 RUN_ACTIVE before any SSE byte', async () => {
    const sessionId = await twoTurns();
    const answerId = await lastAnswerId(sessionId);
    await h.admit({ sessionId, ...text('A third question') });
    const reply = await regenerate(sessionId, answerId);
    expect(reply.status).to.equal(409);
    expect(JSON.stringify(await reply.json())).to.contain('RUN_ACTIVE');
    expect(http!.legacy.called).to.equal(false);
  });

  /** An answer in a conversation of another org (QA-26's replay). */
  const foreignAnswer = async (): Promise<string> => {
    const orgId = new Types.ObjectId().toHexString();
    const userId = new Types.ObjectId().toHexString();
    const other = await ChatSession.create({ orgId, userId, initiator: userId, title: 'x', sessionType: 'chat', status: 'Complete' });
    await appendMessages(String(other._id), orgId, [
      { messageType: 'user_query', content: 'q' },
      { messageType: 'bot_response', content: 'their answer' },
    ] as unknown as IMessage[]);
    return lastAnswerId(String(other._id));
  };

  it('RQ-11 (found by QA-26): another org’s answer id, or one never held, is not found, whatever runs here', async () => {
    const sessionId = await twoTurns();
    await h.admit({ sessionId, ...text('A third question') });
    const before = await messages(sessionId);
    for (const answerId of [await foreignAnswer(), new Types.ObjectId().toHexString()]) {
      const reply = await regenerate(sessionId, answerId);
      expect(reply.status, answerId).to.equal(404);
      expect(JSON.stringify(await reply.json())).not.to.contain('RUN_ACTIVE');
    }
    expect(await messages(sessionId)).to.deep.equal(before);
    expect(http!.legacy.called).to.equal(false);
  });

  it('RQ-6: an answer of this conversation that is not its last answers 409 REGENERATE_NOT_LAST', async () => {
    const sessionId = await twoTurns();
    const all = (await sessionMessages(sessionId)) as Array<IMessage & { _id: unknown }>;
    const firstAnswer = String(all.find((m) => m.messageType === 'bot_response')?._id);
    const reply = await regenerate(sessionId, firstAnswer);
    expect(reply.status).to.equal(409);
    expect(JSON.stringify(await reply.json())).to.contain('REGENERATE_NOT_LAST');
  });

  it('CX-6b (found while building): the same Stop sent again, after it moved the run, answers its first receipt', async () => {
    const admitted = await h.admit(text('A long question'));
    await h.claim(admitted.run);
    const runs = new SessionRunService(h.service, { findStreamAccess: async () => ({ audience: 'owner' }) } as unknown as ChatSessionRepository);
    const stop = { orgId: ORG, actorId: USER, sessionId: admitted.sessionId, runId: admitted.run.runId, idempotencyKey: `stop-${admitted.run.runId}`, action: 'cancel' as const };
    const first = await runs.control(stop);
    expect(first.run.status).to.equal('cancelling');
    const again = await runs.control(stop);
    expect(again.replayed).to.equal(true);
    expect(again.run.revision).to.equal(first.run.revision);
  });
});
