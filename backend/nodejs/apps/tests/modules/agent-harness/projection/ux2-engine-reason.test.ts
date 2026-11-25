/**
 * UX2 EA-9, EA-8: an engine run's approval explained by its worker (`engine_writes`)
 * reaches the card's frame and the admin view as the loop's does; the event's
 * schema accepts the rule and refuses any other.
 */
import { expect } from 'chai';
import { heldRequestsOf } from '../../../../src/modules/agent-harness/domain/effective-policy';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { interactionExplainedEventSchema } from '../../../../src/modules/agent-harness/contracts/generated/core';
import { readJournal } from '../../../contracts/support/durable';

const base = readJournal('agent_quick_tool_call');
const first = base.events[0] as JournalEvent;
const IX = 'ix.approve.k0123456789abcdef';
const KEY = `sha256:${'3'.repeat(64)}`;

const event = (
  seq: number,
  type: string,
  writer: 'worker' | 'node',
  data: Record<string, unknown>,
  critical = false,
): JournalEvent => ({ ...first, seq, type, writer, critical, data });

const reasonData = {
  interactionId: IX,
  rule: 'engine_writes',
  toolId: 'github@default/open_pull_request',
  sensitivity: 'public',
  sources: [],
  moreSources: 0,
  destination: { label: 'acme/web', key: KEY },
  conversationGrant: true,
};

const engineAsk: JournalEvent[] = [
  event(2, 'x.pipeshub.engine.progress', 'worker', {
    tool: 'code_task',
    taskId: 'run-1',
    kind: 'approval',
    text: 'open_pull_request',
    data: { interactionId: IX, toolName: 'github__open_pull_request', argsJson: '{"repo":"acme/web","title":"Fix"}' },
    turnId: 'trn_1',
    seq: 1,
  }),
  event(3, 'interaction.explained', 'worker', reasonData),
  event(4, 'interaction.requested', 'worker', { interactionId: IX, kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` }, true),
];

describe('An engine run’s approval, explained (UX2)', () => {
  it('EA-9: the card gets the engine rule, the destination and the offer; never the grant key', () => {
    const timeline = foldTimeline(engineAsk, first.runId as string);
    const [approval] = timeline.approvals;
    expect(approval?.toolName).to.equal('github__open_pull_request');
    expect(approval?.reason).to.deep.equal({
      rule: 'engine_writes',
      sensitivity: 'public',
      sources: [],
      moreSources: 0,
      destination: 'acme/web',
      conversationGrant: true,
    });
    expect(JSON.stringify(timeline)).not.to.contain(KEY);
  });

  it('EA-9: allowed for the conversation, the card and the admin view say so', () => {
    const decided = [
      ...engineAsk,
      event(5, 'interaction.resolved', 'node', { interactionId: IX, decision: 'approved', by: 'p_u', channel: 'web' }, true),
      event(6, 'interaction.granted', 'node', { interactionId: IX, scope: 'conversation' }),
    ];
    const [approval] = foldTimeline(decided, first.runId as string).approvals;
    expect(approval?.status).to.equal('approved');
    expect(approval?.scope).to.equal('conversation');
    const [held] = heldRequestsOf(decided);
    expect(held).to.include({ rule: 'engine_writes', toolId: 'github@default/open_pull_request', sensitivity: 'public', status: 'approved', scope: 'conversation' });
  });

  it('EA-8: the schema takes engine_writes and refuses a rule it does not list', () => {
    const envelope = { ...engineAsk[1], data: reasonData } as Record<string, unknown>;
    expect(interactionExplainedEventSchema.safeParse(envelope).success).to.equal(true);
    const other = { ...envelope, data: { ...reasonData, rule: 'because' } };
    expect(interactionExplainedEventSchema.safeParse(other).success).to.equal(false);
  });
});
