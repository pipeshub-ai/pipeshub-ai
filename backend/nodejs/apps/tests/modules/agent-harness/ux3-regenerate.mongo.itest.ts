/**
 * UX3 RG-3..RG-6 on a MongoDB replica set: Regenerate is a durable turn. In
 * the admission's transaction the conversation's last answer and its question
 * leave the conversation, the stored question is asked again as the run's
 * input, and the next import replaces the journal's history, so the model
 * never sees the answer it is replacing.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { sessionMessages } from './support/session-messages';
import sinon from 'sinon';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { appendMessages } from '../../../src/modules/enterprise_search/utils/utils';
import { IMessage } from '../../../src/modules/enterprise_search/types/conversation.interfaces';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { HarnessConflictError, HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import { inlineContent } from '../../../src/modules/agent-harness/projection/timeline';
import { admittedHistory, contractClock, ContractHarness, createContractHarness } from './support/execution-store-contract';
import { connectHarnessMongo, disconnectHarnessMongo, mongoBackend, resetHarnessMongo } from './support/harness-mongo';
import { HARNESS_ENABLED } from './support/rollout';
import { workerEvent } from './support/journal-fixtures';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };

const text = (query: string) => ({
  input: { query, recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
});

describe('UX3 durable regenerate on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);
  afterEach(() => sinon.restore());

  const harness = (): ContractHarness => {
    const clock = contractClock();
    const h = createContractHarness(mongoBackend({ clock }), clock);
    h.setRollout(V2);
    return h;
  };

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

  const messages = async (sessionId: string) =>
    ((await sessionMessages(sessionId)) as Array<IMessage & { _id: unknown }>).map((m) => [m.messageType, m.content]);

  const lastAnswerId = async (sessionId: string): Promise<string> => {
    const all = (await sessionMessages(sessionId)) as Array<IMessage & { _id: unknown }>;
    return String([...all].reverse().find((m) => m.messageType === 'bot_response')?._id);
  };

  /** A durable conversation with two answered turns. */
  const conversation = async (h: ContractHarness): Promise<string> => {
    const first = await h.admit(text('What is the leave policy?'));
    await answer(h, first.run, 'Twenty-five days.');
    const second = await h.admit({ sessionId: first.sessionId, ...text('And for contractors?') });
    await answer(h, second.run, 'Ten days.');
    return first.sessionId;
  };

  it('RG-3: the last turn is asked again from what is stored; the old answer is gone and the history no longer holds it', async () => {
    const h = harness();
    const sessionId = await conversation(h);
    const regenerated = await h.admit({
      sessionId,
      regenerate: { messageId: await lastAnswerId(sessionId) },
      ...text('something the browser made up'),
    });
    expect(regenerated.run.input?.query).to.equal('And for contractors?');
    expect(await messages(sessionId)).to.deep.equal([
      ['user_query', 'What is the leave policy?'],
      ['bot_response', 'Twenty-five days.'],
      ['user_query', 'And for contractors?'],
    ]);
    const stream = await h.backend.journal.readStream(h.tenant.orgId, sessionId, 0, 500);
    const imported = stream.filter((e) => e.type === 'session.imported' && e.runId === regenerated.run.runId);
    expect(imported).to.have.length(1);
    expect(imported[0]?.data?.['replacesHistory']).to.equal(true);
    const transcript = JSON.stringify(inlineContent(imported[0]?.data?.['transcriptRef']));
    expect(transcript).to.contain('Twenty-five days.');
    expect(transcript).not.to.contain('Ten days.');
    const history = JSON.stringify(await admittedHistory(h.backend, regenerated.run));
    expect(history).to.contain('Twenty-five days.');
    expect(history).not.to.contain('Ten days.');
  });

  it('RG-4: an answer that is not the last one is refused and nothing is removed', async () => {
    const h = harness();
    const sessionId = await conversation(h);
    const all = (await sessionMessages(sessionId)) as Array<IMessage & { _id: unknown }>;
    const firstAnswer = String(all.find((m) => m.messageType === 'bot_response')?._id);
    const before = await messages(sessionId);
    const refused = await h.submit({ sessionId, regenerate: { messageId: firstAnswer }, ...text('q') }).catch((e: unknown) => e);
    expect((refused as HarnessConflictError).code).to.equal('REGENERATE_NOT_LAST');
    expect(await messages(sessionId)).to.deep.equal(before);
  });

  it('RG-4: another session’s answer is not found (QA-26: before anything about this session’s runs)', async () => {
    const h = harness();
    const sessionId = await conversation(h);
    const other = await ChatSession.create({ orgId: h.tenant.orgId, userId: h.tenant.actorId, initiator: h.tenant.actorId, title: 'x', sessionType: 'chat', status: 'Complete' });
    await appendMessages(String(other._id), h.tenant.orgId, [
      { messageType: 'user_query', content: 'q' },
      { messageType: 'bot_response', content: 'a' },
    ] as unknown as IMessage[]);
    const refused = await h
      .submit({ sessionId, regenerate: { messageId: await lastAnswerId(String(other._id)) }, ...text('q') })
      .catch((e: unknown) => e);
    expect(refused).to.be.instanceOf(HarnessNotFoundError);
  });

  it('RG-5: while a run is active, regenerate is refused, never queued as steering', async () => {
    const h = harness();
    const sessionId = await conversation(h);
    const answerId = await lastAnswerId(sessionId);
    await h.admit({ sessionId, ...text('A third question') });
    const refused = await h.submit({ sessionId, regenerate: { messageId: answerId }, ...text('q') }).catch((e: unknown) => e);
    expect((refused as HarnessConflictError).code).to.equal('RUN_ACTIVE');
  });

  it('RG-6: the same regenerate replayed answers its first receipt and removes nothing more', async () => {
    const h = harness();
    const sessionId = await conversation(h);
    const request = { sessionId, idempotencyKey: 'regen-1', regenerate: { messageId: await lastAnswerId(sessionId) }, ...text('q') };
    const first = await h.admit(request);
    const again = await h.admit(request);
    expect(again.run.runId).to.equal(first.run.runId);
    expect((await messages(sessionId)).filter(([type]) => type === 'user_query')).to.have.length(2);
  });

  /** As a worker ends a run whose model call failed: its error answer, stored as the turn's error row. */
  const MODEL_FAILED = "The AI model's provider isn't responding right now. Please try again in a minute.";
  const fail = async (h: ContractHarness, run: HarnessRun): Promise<void> => {
    const claimed = await h.claim(run);
    await h.service.commit({
      commandId: `${run.runId}:failed`,
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'worker-a',
      attemptId: 'attempt-worker-a',
      fence: claimed.fence,
      events: [
        workerEvent(
          'x.pipeshub.answer.finalized',
          {
            outcome: 'error',
            answer: MODEL_FAILED,
            partTexts: [MODEL_FAILED],
            citations: [],
            confidence: 'Low',
            answerMatchType: 'Error',
            errorCode: 'model_failed',
          },
          { critical: false },
        ),
      ],
      legacyEvents: [],
      transition: { status: 'failed', errorType: 'model_failed' },
      outbox: [],
    });
  };

  const errorRowId = async (sessionId: string): Promise<string> => {
    const all = (await sessionMessages(sessionId)) as Array<IMessage & { _id: unknown }>;
    return String([...all].reverse().find((m) => m.messageType === 'error')?._id);
  };

  it("Try again on a failed answer: the failed run's error row is redone in its place, from the question as stored", async () => {
    const h = harness();
    const first = await h.admit(text('What is the leave policy?'));
    await answer(h, first.run, 'Twenty-five days.');
    const second = await h.admit({ sessionId: first.sessionId, ...text('And for contractors?') });
    await fail(h, second.run);
    expect(await messages(first.sessionId)).to.deep.equal([
      ['user_query', 'What is the leave policy?'],
      ['bot_response', 'Twenty-five days.'],
      ['user_query', 'And for contractors?'],
      ['error', MODEL_FAILED],
    ]);

    const again = await h.admit({
      sessionId: first.sessionId,
      regenerate: { messageId: await errorRowId(first.sessionId) },
      ...text('something the browser made up'),
    });
    expect(again.run.input?.query).to.equal('And for contractors?');
    expect(await messages(first.sessionId), 'the failed turn left; its question is asked again').to.deep.equal([
      ['user_query', 'What is the leave policy?'],
      ['bot_response', 'Twenty-five days.'],
      ['user_query', 'And for contractors?'],
    ]);
    expect(JSON.stringify(await admittedHistory(h.backend, again.run)), 'the run is not given the failure').not.to.contain(
      MODEL_FAILED,
    );
    await answer(h, again.run, 'Ten days.');
    expect(await messages(first.sessionId), 'the new answer stands where the failure was').to.deep.equal([
      ['user_query', 'What is the leave policy?'],
      ['bot_response', 'Twenty-five days.'],
      ['user_query', 'And for contractors?'],
      ['bot_response', 'Ten days.'],
    ]);
  });

  it('a failed answer that a later answer followed is not the last one: refused, and nothing is removed', async () => {
    const h = harness();
    const first = await h.admit(text('What is the leave policy?'));
    await fail(h, first.run);
    const second = await h.admit({ sessionId: first.sessionId, ...text('And for contractors?') });
    await answer(h, second.run, 'Ten days.');
    const before = await messages(first.sessionId);
    const refused = await h
      .submit({ sessionId: first.sessionId, regenerate: { messageId: await errorRowId(first.sessionId) }, ...text('q') })
      .catch((e: unknown) => e);
    expect((refused as HarnessConflictError).code).to.equal('REGENERATE_NOT_LAST');
    expect(await messages(first.sessionId)).to.deep.equal(before);
  });
});
