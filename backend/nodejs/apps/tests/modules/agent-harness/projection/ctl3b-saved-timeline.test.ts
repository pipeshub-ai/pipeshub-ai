import { expect } from 'chai';
import { canonicalJsonBytes } from '../../../../src/modules/agent-harness/contracts/canonical-json';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { inlineContentRef } from '../../../../src/modules/agent-harness/domain/run-content';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { buildParts } from '../../../../src/modules/agent-harness/projection/transcript';

/**
 * CTL-3b: a saved answer keeps what a reload needs to show its timeline the way the live stream did: the run's
 * final plan, each step's duration and the run's total, all read from the journal (event `ts` is commit time).
 */
const RUN = 'run-ctl3b';
let seq = 0;
const event = (at: number, type: string, data: Record<string, unknown>): JournalEvent =>
  ({
    orgId: 'org-1',
    sessionId: 'ses-1',
    streamId: 'ses-1',
    seq: (seq += 1),
    runId: RUN,
    attemptId: `${RUN}.a1`,
    fence: 1,
    type,
    schemaVersion: 1,
    critical: !type.startsWith('x.'),
    ts: new Date(Date.UTC(2026, 9, 15, 12, 0, 0) + at).toISOString(),
    writer: 'worker',
    visibility: 'owner',
    data,
  }) as JournalEvent;

const response = (at: number, text: string, calls: string[]): JournalEvent =>
  event(at, 'model.response', {
    messageRef: inlineContentRef(
      canonicalJsonBytes({ text, toolCalls: calls.map((id) => ({ id, name: 'knowledgegraph__search_entities', args: {} })) }),
    ),
    toolCalls: calls.map((id) => ({ callId: id, modelName: 'knowledgegraph__search_entities', argsDigest: `sha256:${'c'.repeat(64)}` })),
    usage: { inputTokens: 0, outputTokens: 0 },
    provider: 'p',
    transport: 't',
  });

function journal(): JournalEvent[] {
  seq = 0;
  return [
    event(0, 'run.admitted', { runKind: 'interactive' }),
    event(500, 'run.claimed', { attempt: 1, fence: 1, leaseMs: 30000 }),
    event(1_000, 'task.created', { taskId: 't1', title: 'Find the policy', status: 'in_progress', deps: [] }),
    event(1_000, 'task.created', { taskId: 't2', title: 'Summarise it', status: 'pending', deps: ['t1'] }),
    event(8_000, 'turn.started', { turn: 1 }),
    response(8_000, 'Looking it up.', ['c1']),
    event(8_000, 'x.pipeshub.message.presented', { text: 'Looking it up.' }),
    event(8_000, 'tool.intent', { invocationId: 't1.0', effect: 'read' }),
    event(11_500, 'tool.outcome', { invocationId: 't1.0', status: 'ok' }),
    event(11_500, 'x.pipeshub.tool.result_presented', { callId: 'c1', status: 'completed', content: 'hits' }),
    event(11_500, 'task.updated', { taskId: 't1', title: 'Find the policy', status: 'completed', deps: [] }),
    event(11_500, 'task.updated', { taskId: 't2', title: 'Summarise it', status: 'completed', deps: ['t1'] }),
    event(20_000, 'turn.started', { turn: 2 }),
    response(20_000, 'Book 14 days ahead.', []),
    event(20_000, 'x.pipeshub.message.presented', { text: 'Book 14 days ahead.' }),
    event(20_100, 'x.pipeshub.answer.finalized', { outcome: 'answered', answer: 'Book 14 days ahead.', partTexts: ['Looking it up.', 'Book 14 days ahead.'], citations: [] }),
    event(20_200, 'run.completed', { outcome: 'answered' }),
  ];
}

describe('CTL-3b: the saved parts carry the plan and the timing a reload shows', () => {
  it('keeps the final plan as a plan part, first, with each step\'s last state', () => {
    const parts = buildParts(foldTimeline(journal(), RUN));
    expect(parts[0]).to.deep.equal({
      type: 'plan',
      runId: RUN,
      steps: [
        { taskId: 't1', title: 'Find the policy', status: 'completed', deps: [] },
        { taskId: 't2', title: 'Summarise it', status: 'completed', deps: ['t1'] },
      ],
    });
  });

  it('times each step from the commit before it, and the run from its admission to its end', () => {
    const parts = buildParts(foldTimeline(journal(), RUN));
    const [first, call, final] = parts.filter((p) => p['type'] !== 'plan');
    // The first model turn ran from the claim (0.5s) to its response (8s).
    expect(first).to.include({ type: 'text', durationMs: 7_500 });
    expect(call).to.include({ type: 'tool_call', toolCallId: 'c1', durationMs: 3_500 });
    expect(final).to.include({ type: 'text', isFinal: true, durationMs: 8_500, elapsedMs: 20_200 });
  });

  it('a run with no plan saves no plan part, and an unfinished run no total', () => {
    const events = journal().filter((e) => !e.type.startsWith('task.') && e.type !== 'run.completed' && e.type !== 'x.pipeshub.answer.finalized');
    const parts = buildParts(foldTimeline(events, RUN));
    expect(parts.some((p) => p['type'] === 'plan')).to.equal(false);
    expect(parts.some((p) => 'elapsedMs' in p)).to.equal(false);
  });
});
