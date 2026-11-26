/**
 * LC2FIX on a MongoDB replica set: a durable run's error answer is saved with
 * its run id and its code. LIFECYCLE-2 saw the run id stamped on the answer
 * before it instead (the latest `bot_response`), so the chat read the error as
 * an old-harness answer and greyed /compact.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { sessionMessages } from './support/session-messages';

import { IMessage } from '../../../src/modules/enterprise_search/types/conversation.interfaces';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { contractClock, ContractHarness, createContractHarness } from './support/execution-store-contract';
import { connectHarnessMongo, disconnectHarnessMongo, mongoBackend, resetHarnessMongo } from './support/harness-mongo';
import { HARNESS_ENABLED } from './support/rollout';
import { workerEvent } from './support/journal-fixtures';
import { ORG, USER } from './support/chat-turn-harness';

const text = (query: string) => ({
  orgId: ORG,
  actorId: USER,
  input: { query, recordIds: [], filters: {}, attachments: [], model: { chatMode: 'internal_search' } },
});

describe('LC2FIX: a durable error answer keeps its run id, on a MongoDB replica set', function () {
  this.timeout(60_000);
  let h: ContractHarness;

  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(async () => {
    await resetHarnessMongo();
    const clock = contractClock();
    clock.set(Date.now());
    h = createContractHarness(mongoBackend({ clock }), clock);
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
  });

  const end = async (run: HarnessRun, answer: Record<string, unknown>, transition: Record<string, unknown>) => {
    const claimed = await h.claim(run);
    await h.service.commit({
      commandId: `${run.runId}:end`,
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'worker-a',
      attemptId: 'attempt-worker-a',
      fence: claimed.fence,
      events: [workerEvent('x.pipeshub.answer.finalized', answer, { critical: false })],
      legacyEvents: [],
      transition: transition as never,
      outbox: [],
    });
  };

  it('the error answer of a budget-exhausted run names its run and code; the answer before keeps its own run', async () => {
    const first = await h.admit(text('Write the notes.'));
    await end(
      first.run,
      { outcome: 'answered', answer: 'Done.', partTexts: ['Done.'], citations: [], confidence: null },
      { status: 'completed', outcome: 'answer' },
    );
    const second = await h.admit({ sessionId: first.sessionId, ...text('usage 260000') });
    const words = 'This reply stopped because it reached its spending limit.';
    await end(
      second.run,
      {
        outcome: 'error',
        answer: words,
        partTexts: [words],
        citations: [],
        confidence: 'Low',
        answerMatchType: 'Error',
        errorCode: 'budget_exhausted',
      },
      { status: 'failed', errorType: 'budget_exhausted' },
    );
    const saved = (await sessionMessages(first.sessionId)) as IMessage[];
    expect(saved.map((m) => [m.messageType, m.runId, m.errorCode])).to.deep.equal([
      ['user_query', undefined, undefined],
      ['bot_response', first.run.runId, undefined],
      ['user_query', undefined, undefined],
      ['error', second.run.runId, 'budget_exhausted'],
    ]);
  });
});
