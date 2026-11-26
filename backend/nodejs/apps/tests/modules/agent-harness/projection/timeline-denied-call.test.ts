import { expect } from 'chai';
import { canonicalJsonBytes } from '../../../../src/modules/agent-harness/contracts/canonical-json';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { inlineContentRef } from '../../../../src/modules/agent-harness/domain/run-content';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';

const RUN = 'run-denied';

let seq = 0;
const event = (type: string, data: Record<string, unknown>): JournalEvent =>
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
    ts: `2026-10-04T12:00:${String(seq).padStart(2, '0')}.000Z`,
    writer: 'worker',
    visibility: 'owner',
    data,
  }) as JournalEvent;

const turnWithCalls = (...ids: string[]): JournalEvent[] => [
  event('turn.started', { turn: 1 }),
  event('model.response', {
    messageRef: inlineContentRef(
      canonicalJsonBytes({ text: '', toolCalls: ids.map((id) => ({ id, name: 'jira_create', args: {} })) }),
    ),
    toolCalls: ids.map((id) => ({ callId: id, modelName: 'jira_create', argsDigest: `sha256:${'c'.repeat(64)}` })),
    usage: { inputTokens: 0, outputTokens: 0 },
    provider: 'p',
    transport: 't',
  }),
];

describe('agent-harness timeline: a denied tool call (P2.6c rule `deny`)', () => {
  it('stays blocked whatever outcome or presented result follows; only its text is kept; no outcome counted', () => {
    seq = 0;
    const events = [
      ...turnWithCalls('c1', 'c2'),
      event('tool.denied', { invocationId: 't1.0', rule: 'org.writes' }),
      event('tool.outcome', { invocationId: 't1.0', status: 'error' }),
      event('x.pipeshub.tool.result_presented', { callId: 'c1', status: 'completed', content: 'ignored' }),
      event('tool.outcome', { invocationId: 't1.1', status: 'ok' }),
    ];
    const timeline = foldTimeline(events, RUN);
    const [denied, allowed] = timeline.turns[0]!.calls;
    expect(denied).to.include({ callId: 'c1', status: 'blocked', outcome: 'error' });
    expect(denied!.content).to.not.equal('ignored');
    expect(allowed).to.include({ callId: 'c2', status: 'completed', outcome: 'ok' });
    expect(timeline.outcomes).to.deep.equal([{ name: 'jira_create', ok: true }]);
  });

  it('a denial naming no call of the run changes nothing', () => {
    seq = 0;
    const events = [
      ...turnWithCalls('c1'),
      event('tool.denied', { invocationId: 't9.0', rule: 'org.writes' }),
      event('tool.denied', { invocationId: 'not-an-invocation', rule: 'org.writes' }),
    ];
    expect(foldTimeline(events, RUN).turns[0]!.calls[0]!.status).to.equal(undefined);
  });
});
