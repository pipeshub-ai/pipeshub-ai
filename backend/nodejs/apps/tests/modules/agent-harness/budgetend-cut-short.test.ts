/**
 * BUDGETEND: a run whose spending limit, turn limit or working time ran out answers from what it gathered on one
 * answer-only turn and completes, its answer flagged `truncatedBy`. The worker asks for the completion with
 * `truncatedBy` (Node journals it on `run.completed` and keeps it on the run) and records it on
 * `x.pipeshub.answer.finalized`; the transcript, the final AG-UI snapshot and the saved message carry it to the web
 * chat and the Slack bot, which show a notice with the answer.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import {
  HarnessRun,
  transitionJournalEvents,
  validateCommitBatch,
} from '../../../src/modules/agent-harness/domain/execution';
import { JournalEvent, validateJournalAppend } from '../../../src/modules/agent-harness/domain/journal';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { sharedCompletion } from '../../../src/modules/agent-harness/projection/audiences';
import { RunProjector } from '../../../src/modules/agent-harness/projection/run-projector';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { buildCompletion, buildParts, runTranscript } from '../../../src/modules/agent-harness/projection/transcript';
import { buildAIResponseMessage } from '../../../src/modules/enterprise_search/utils/utils';
import { IAIResponse } from '../../../src/modules/enterprise_search/types/conversation.interfaces';
import { journalOnlySteps, projectorContext, readJournal, runSteps } from '../../contracts/support/durable';

const RUN = 'run-1';

function journal(answer: Record<string, unknown>, completed: Record<string, unknown>): JournalEvent[] {
  const specs: Array<[string, Record<string, unknown>, 'node' | 'worker']> = [
    ['run.admitted', { runKind: 'interactive', principalRef: 'p_user' }, 'node'],
    ['run.claimed', { attempt: 1, fence: 1, leaseMs: 60000 }, 'node'],
    ['x.pipeshub.answer.finalized', answer, 'worker'],
    ['run.completed', completed, 'node'],
  ];
  return specs.map(
    ([type, data, writer], index) =>
      ({
        orgId: 'org-1',
        sessionId: 'ses-1',
        streamId: 'ses-1',
        seq: index + 1,
        runId: RUN,
        attemptId: writer === 'worker' ? `${RUN}.a1` : undefined,
        fence: writer === 'worker' ? 1 : undefined,
        type,
        schemaVersion: 1,
        critical: !type.startsWith('x.'),
        ts: '2026-10-22T12:00:00.000Z',
        writer,
        visibility: 'owner',
        data,
      }) as JournalEvent,
  );
}

const ANSWER = 'Here is what I found in Slack so far. I stopped early because this reply reached its spending limit.';
const answered = (extra: Record<string, unknown> = {}) => ({
  outcome: 'answered',
  answer: ANSWER,
  partTexts: [ANSWER],
  citations: [],
  confidence: 'Medium',
  ...extra,
});
const cutShort = journal(answered({ truncatedBy: 'budget' }), { outcome: 'answered', truncatedBy: 'budget' });

const appended = (type: string, data: Record<string, unknown>, writer: 'node' | 'worker' = 'worker') => () =>
  validateJournalAppend({
    orgId: 'org-1',
    sessionId: 'ses-1',
    streamId: 'ses-1',
    runId: RUN,
    writer,
    events: [{ type, schemaVersion: 1, critical: !type.startsWith('x.'), visibility: 'owner', data }],
  } as never);

function run(overrides: Partial<HarnessRun> = {}): HarnessRun {
  return {
    runId: RUN,
    orgId: 'org-1',
    sessionId: 'ses-1',
    actorId: 'user-1',
    userMessageKey: 'm-1',
    idempotencyKey: 'k-1',
    payloadHash: 'h',
    harnessMajor: 2,
    profile: 'inline',
    status: 'running',
    outcome: null,
    revision: 1,
    fence: 1,
    attemptId: 'att-1',
    workerId: 'w-1',
    leaseExpiresAt: Date.now() + 60_000,
    absoluteDeadlineAt: Date.now() + 3_600_000,
    checkpoint: null,
    attempts: 1,
    createdAt: 0,
    updatedAt: 0,
    ...overrides,
  } as HarnessRun;
}

const batch = (transition: Record<string, unknown>) =>
  ({
    commandId: 'c-1',
    orgId: 'org-1',
    runId: RUN,
    workerId: 'w-1',
    attemptId: 'att-1',
    fence: 1,
    events: [],
    legacyEvents: [],
    outbox: [],
    transition,
  }) as never;

describe('BUDGETEND: an answer a limit cut short', () => {
  describe('the journal', () => {
    it('the worker records which limit on its answer; an unknown one is refused', () => {
      expect(appended('x.pipeshub.answer.finalized', answered({ truncatedBy: 'budget' }))).to.not.throw();
      expect(appended('x.pipeshub.answer.finalized', answered({ truncatedBy: 'turns' }))).to.not.throw();
      expect(appended('x.pipeshub.answer.finalized', answered({ truncatedBy: 'money' }))).to.throw();
    });

    it('the completion the worker asks for is journaled on run.completed', () => {
      const [event] = transitionJournalEvents(run(), { status: 'completed', outcome: 'answer', truncatedBy: 'time' });
      expect(event).to.deep.include({ type: 'run.completed' });
      expect(event!.data).to.deep.equal({ outcome: 'answered', truncatedBy: 'time' });
      const [plain] = transitionJournalEvents(run(), { status: 'completed', outcome: 'answer' });
      expect(plain!.data).to.deep.equal({ outcome: 'answered' });
    });

    it('only an answered completion is cut short', () => {
      expect(() => validateCommitBatch(run(), batch({ status: 'completed', outcome: 'answer', truncatedBy: 'budget' }))).to.not.throw();
      expect(() =>
        validateCommitBatch(run(), batch({ status: 'completed', outcome: 'needs_input', truncatedBy: 'budget' })),
      ).to.throw(HarnessConflictError);
      expect(() =>
        validateCommitBatch(run(), batch({ status: 'failed', errorType: 'budget_exhausted', truncatedBy: 'budget' })),
      ).to.throw(HarnessConflictError);
    });
  });

  describe('the transcript', () => {
    it('the completion and its final text part say which limit; the answer is the run’s own', () => {
      const timeline = foldTimeline(cutShort, RUN);
      expect(timeline.end).to.deep.equal({ status: 'completed', outcome: 'answered', truncatedBy: 'budget' });
      const completion = buildCompletion(timeline)!;
      expect(completion['truncatedBy']).to.equal('budget');
      expect(completion['answer']).to.equal(ANSWER);
      expect(completion['answerMatchType']).to.equal(undefined);
      const final = buildParts(timeline).find((part) => part['isFinal'] === true);
      expect(final).to.deep.include({ type: 'text', content: ANSWER, truncatedBy: 'budget' });
      const transcript = runTranscript(cutShort, RUN);
      expect(transcript?.kind).to.equal('answer');
    });

    it('an answer no limit cut short carries nothing', () => {
      const timeline = foldTimeline(journal(answered(), { outcome: 'answered' }), RUN);
      expect(buildCompletion(timeline)).to.not.have.property('truncatedBy');
      expect(buildParts(timeline).find((part) => part['isFinal'] === true)).to.not.have.property('truncatedBy');
    });

    it('an error answer is never flagged, whatever the worker wrote', () => {
      const timeline = foldTimeline(
        journal(answered({ outcome: 'error', answerMatchType: 'Error', errorCode: 'budget_exhausted', truncatedBy: 'budget' }), {
          outcome: 'answered',
        }),
        RUN,
      );
      expect(buildCompletion(timeline)).to.not.have.property('truncatedBy');
    });

    it('a shared viewer (Slack, MCP, a shared conversation) sees the notice too', () => {
      const completion = buildCompletion(foldTimeline(cutShort, RUN))!;
      const seen = sharedCompletion(
        { ...completion, final: true },
        { citations: (c) => c, messages: (m) => m },
        { card: (c) => c, text: (t) => t, messages: (m) => m, completion: (c) => c },
      );
      expect(seen['truncatedBy']).to.equal('budget');
      expect((seen['parts'] as Array<Record<string, unknown>>)[0]).to.deep.include({ truncatedBy: 'budget' });
    });
  });

  describe('WP-51: which shared budget ran out', () => {
    const shared = (scope: string) =>
      journal(answered({ truncatedBy: 'budget', budgetScope: scope }), { outcome: 'answered', truncatedBy: 'budget' });

    it('C9.06: the worker names the budget on its answer; a value not shaped as a scope is refused', () => {
      expect(appended('x.pipeshub.answer.finalized', answered({ truncatedBy: 'budget', budgetScope: 'pool-a' }))).to.not.throw();
      expect(appended('x.pipeshub.answer.finalized', answered({ truncatedBy: 'budget', budgetScope: 'Pool A' }))).to.throw();
    });

    it('C9.06: the completion, its final part, a shared viewer and the saved message say which budget', () => {
      for (const scope of ['pool-a', 'pool_b', 'p']) {
        const timeline = foldTimeline(shared(scope), RUN);
        const completion = buildCompletion(timeline)!;
        expect(completion).to.deep.include({ truncatedBy: 'budget', budgetScope: scope });
        expect(buildParts(timeline).find((part) => part['isFinal'] === true)).to.deep.include({ budgetScope: scope });
        const seen = sharedCompletion(
          { ...completion, final: true },
          { citations: (c) => c, messages: (m) => m },
          { card: (c) => c, text: (t) => t, messages: (m) => m, completion: (c) => c },
        );
        expect(seen['budgetScope']).to.equal(scope);
        const saved = buildAIResponseMessage({ statusCode: 200, data: { ...completion, citations: [] } as unknown as IAIResponse });
        expect(saved).to.deep.include({ truncatedBy: 'budget', budgetScope: scope });
      }
    });

    it('C9.06: a run’s own spending limit, another limit or a value not shaped as a scope names no budget', () => {
      expect(buildCompletion(foldTimeline(cutShort, RUN))).to.not.have.property('budgetScope');
      const turns = foldTimeline(
        journal(answered({ truncatedBy: 'turns', budgetScope: 'pool-a' }), { outcome: 'answered', truncatedBy: 'turns' }),
        RUN,
      );
      expect(buildCompletion(turns)).to.not.have.property('budgetScope');
      const saved = (extra: Record<string, unknown>) =>
        buildAIResponseMessage({ statusCode: 200, data: { answer: ANSWER, citations: [], ...extra } as unknown as IAIResponse });
      expect(saved({ truncatedBy: 'budget', budgetScope: 'Pool A' })).to.not.have.property('budgetScope');
      expect(saved({ truncatedBy: 'time', budgetScope: 'pool-a' })).to.not.have.property('budgetScope');
    });
  });

  describe('the live stream', () => {
    it('the final AG-UI snapshot carries it', () => {
      const golden = readJournal('agent_quick_final_answer');
      const events = golden.events.map((event) => {
        if (event.type === 'x.pipeshub.answer.finalized') {
          return { ...event, data: { ...(event.data as Record<string, unknown>), truncatedBy: 'turns' } };
        }
        if (event.type === 'run.completed') {
          return { ...event, data: { ...(event.data as Record<string, unknown>), truncatedBy: 'turns' } };
        }
        return event;
      }) as JournalEvent[];
      const flagged = { ...golden, events };
      const frames = runSteps(new RunProjector(projectorContext(flagged)), journalOnlySteps(flagged));
      const final = frames
        .map((f) => f.frame as Record<string, unknown>)
        .find((frame) => frame['type'] === 'STATE_SNAPSHOT' && (frame['snapshot'] as Record<string, unknown>)?.['final'] === true);
      expect((final!['snapshot'] as Record<string, unknown>)['truncatedBy']).to.equal('turns');
    });
  });

  describe('the saved message', () => {
    const response = (extra: Record<string, unknown>) =>
      buildAIResponseMessage({ statusCode: 200, data: { answer: ANSWER, citations: [], ...extra } as unknown as IAIResponse });

    it('a durable or legacy completion that names a limit is saved with it', () => {
      expect(response({ truncatedBy: 'budget' }).truncatedBy).to.equal('budget');
      expect(response({ truncatedBy: 'time' }).messageType).to.equal('bot_response');
    });

    it('an unknown limit, or none, saves nothing', () => {
      expect(response({ truncatedBy: 'money' })).to.not.have.property('truncatedBy');
      expect(response({})).to.not.have.property('truncatedBy');
    });

    it('an error answer is not flagged', () => {
      expect(
        response({ answerMatchType: 'Error', errorCode: 'budget_exhausted', truncatedBy: 'budget' }),
      ).to.not.have.property('truncatedBy');
    });
  });
});
