/**
 * WP-36 G-13: the questions the viewer's own runs asked them that nobody
 * answered, which they answer with their next message in that conversation
 * (IQ-1..IQ-3); the Inbox lists them.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { QuestionInboxService } from '../../../src/modules/agent-harness/application/question-inbox';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { SessionExecutionScope } from '../../../src/modules/agent-harness/ports/chat-session-repository';
import { UnansweredQuestionFilter } from '../../../src/modules/agent-harness/ports/execution-store';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { assertAnswersContract } from './support/api-contracts';

const ORG = 'org-1';

let seq = 0;
const event = (runId: string, type: string, data: Record<string, unknown>, writer = 'worker'): JournalEvent =>
  ({
    orgId: ORG,
    sessionId: `s-${runId}`,
    streamId: `s-${runId}`,
    seq: (seq += 1),
    runId,
    attemptId: `${runId}.a1`,
    fence: 1,
    type,
    schemaVersion: 1,
    critical: !type.startsWith('x.'),
    ts: '2026-10-31T12:00:00.000Z',
    writer,
    visibility: 'owner',
    data,
  }) as JournalEvent;

const run = (runId: string, extra: Partial<HarnessRun> = {}): HarnessRun =>
  ({
    runId,
    orgId: ORG,
    sessionId: `s-${runId}`,
    actorId: 'u-member',
    harnessMajor: 2,
    status: 'waiting_approval',
    outcome: null,
    revision: 3,
    createdAt: 100,
    updatedAt: 100,
    openInteractions: [],
    ...extra,
  }) as HarnessRun;

/** A run that ended asking its person a question. */
const asking = (runId: string, toolData: unknown, status = 'success'): JournalEvent[] => [
  event(runId, 'turn.started', { turn: 1 }),
  event(runId, 'x.pipeshub.question.asked', { callId: `ask.${runId}`, status, toolData }),
];

const card = (question: string, options: string[], more = 0) => ({
  name: 'ask_user_question',
  userIntent: '',
  questions: [
    { uuid: 'q1', question, options: options.map((label) => ({ id: `opt_${label}`, label, isUserInput: false })), multiSelect: false },
    ...Array.from({ length: more }, (_, i) => ({ uuid: `q${String(i + 2)}`, question: `more ${String(i)}`, options: [], multiSelect: true })),
  ],
});

function questionWorld() {
  const runs: HarnessRun[] = [
    run('q-chat', { status: 'completed', outcome: 'needs_input', openInteractions: [], updatedAt: 300 }),
    run('q-agent', { status: 'completed', outcome: 'needs_input', openInteractions: [], updatedAt: 200, agentKey: 'agent-a', agentVersion: 4 }),
    run('q-string', { status: 'completed', outcome: 'needs_input', openInteractions: [], updatedAt: 150 }),
    run('q-error', { status: 'completed', outcome: 'needs_input', openInteractions: [], updatedAt: 120 }),
    run('q-gone', { status: 'completed', outcome: 'needs_input', openInteractions: [], updatedAt: 110 }),
    run('q-colleague', { status: 'completed', outcome: 'needs_input', openInteractions: [], updatedAt: 100, actorId: 'u-colleague' }),
  ];
  const journals: Record<string, JournalEvent[]> = {
    'q-chat': asking('q-chat', card('Which quarter should the report cover?', ['Q3', 'Q4'], 1)),
    'q-agent': asking('q-agent', card('Send it to #ops or #exec?', ['#ops', '#exec'])),
    'q-string': asking('q-string', 'What is the repository called?'),
    'q-error': asking('q-error', 'bad', 'error'),
    'q-gone': asking('q-gone', 'Still there?'),
    'q-colleague': asking('q-colleague', 'Yours?'),
  };
  const access = new Set(['s-q-chat', 's-q-agent', 's-q-string', 's-q-error', 's-q-colleague']);
  const queries: UnansweredQuestionFilter[] = [];
  const inbox = new QuestionInboxService(
    {
      unansweredQuestionRuns: async (orgId: string, filter: UnansweredQuestionFilter) => {
        queries.push(filter);
        return runs.filter(
          (r) =>
            r.orgId === orgId &&
            r.actorId === filter.actorId &&
            r.outcome === 'needs_input' &&
            (filter.agentKey === undefined || r.agentKey === filter.agentKey),
        );
      },
    },
    {
      findStreamAccess: async (scope: SessionExecutionScope) =>
        access.has(scope.sessionId) && scope.actorId === runs.find((r) => r.sessionId === scope.sessionId)?.actorId
          ? ({ audience: 'owner' } as never)
          : null,
    },
    { questions: async (r: HarnessRun) => foldTimeline(journals[r.runId] ?? [], r.runId).questions },
  );
  return { inbox, queries, runs };
}

describe('WP-36 G-13 the inbox lists the questions waiting on the viewer (IQ)', () => {
  it('IQ-1: the viewer sees their own runs’ unanswered questions with the card’s first question and its options', async () => {
    const { inbox, queries } = questionWorld();
    const questions = await inbox.pending({ orgId: ORG, actorId: 'u-member', limit: 50 });
    assertAnswersContract('GET', '/api/v1/agent-harness/approvals/pending', 200, { items: [], questions });
    expect(questions.map((q) => q.runId)).to.deep.equal(['q-chat', 'q-agent', 'q-string']);
    expect(questions[0]).to.deep.equal({
      runId: 'q-chat',
      sessionId: 's-q-chat',
      runUpdatedAt: 300,
      callId: 'ask.q-chat',
      question: 'Which quarter should the report cover?',
      options: ['Q3', 'Q4'],
      multiSelect: false,
      moreQuestions: 1,
    });
    expect(questions[1]).to.include({ agentKey: 'agent-a', question: 'Send it to #ops or #exec?' });
    expect(questions[2]).to.include({ question: 'What is the repository called?', moreQuestions: 0 });
    expect(queries).to.deep.equal([{ actorId: 'u-member', limit: 50 }]);
  });

  it('IQ-2: a question is only ever its own person’s; another person’s, an errored card and a conversation they can no longer open are not listed', async () => {
    const { inbox } = questionWorld();
    expect((await inbox.pending({ orgId: ORG, actorId: 'u-approver', limit: 50 })).map((q) => q.runId)).to.deep.equal([]);
    expect((await inbox.pending({ orgId: ORG, actorId: 'u-colleague', limit: 50 })).map((q) => q.runId)).to.deep.equal(['q-colleague']);
    expect((await inbox.pending({ orgId: 'org-2', actorId: 'u-member', limit: 50 })).map((q) => q.runId)).to.deep.equal([]);
  });

  it('IQ-3: the agent filter narrows the questions as it narrows the approvals', async () => {
    const { inbox, queries } = questionWorld();
    expect((await inbox.pending({ orgId: ORG, actorId: 'u-member', agentKey: 'agent-a', limit: 10 })).map((q) => q.runId)).to.deep.equal([
      'q-agent',
    ]);
    expect(queries).to.deep.equal([{ actorId: 'u-member', agentKey: 'agent-a', limit: 10 }]);
  });
});
