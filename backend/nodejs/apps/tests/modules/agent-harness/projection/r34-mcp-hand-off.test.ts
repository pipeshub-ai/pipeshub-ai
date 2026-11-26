/**
 * R34-FIX D-MCP-ASK: an MCP client's `pipeshub_chat` reads one stream to its end and cannot show a card. When the
 * run asks a person's approval, the external audience is handed the conversation as it stands with the words a
 * person reads, in the `RUN_FINISHED` its reader ends on; the run stays parked for the decision in PipesHub.
 */
import { expect } from 'chai';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import {
  AWAITING_APPROVAL_STATUS,
  AWAITING_APPROVAL_TEXT,
  RunProjector,
} from '../../../../src/modules/agent-harness/projection/run-projector';
import { projectorContext, readJournal } from '../../../contracts/support/durable';

const base = readJournal('agent_quick_tool_call');
const asked = base.events.filter((e) => e.seq <= 12);
const last = asked[asked.length - 1] as JournalEvent;
const IX = 'ix.approve.t1.0';
const event = (seq: number, type: string, writer: 'worker' | 'node', data: Record<string, unknown>): JournalEvent => ({
  ...last,
  seq,
  type,
  writer,
  critical: true,
  data,
});

const projector = (): RunProjector => {
  const p = new RunProjector(projectorContext(base, 'external'));
  p.begin();
  return p;
};

describe('R34-FIX D-MCP-ASK: an MCP chat that needs an approval ends its stream with words, not a hang', () => {
  it('MCPASK-1: an open card is awaiting; its decision and the run\'s end are not', () => {
    const p = projector();
    p.project(asked);
    expect(p.awaitingApproval).to.equal(false);
    p.project([event(13, 'interaction.requested', 'worker', { interactionId: IX, kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` })]);
    expect(p.awaitingApproval).to.equal(true);
    p.project([event(14, 'interaction.resolved', 'node', { interactionId: IX, decision: 'approved', by: 'p_user', channel: 'web' })]);
    expect(p.awaitingApproval).to.equal(false);
  });

  it('MCPASK-2: the hand-off is the RUN_FINISHED an MCP reader ends on, its answer the waiting words', () => {
    const p = projector();
    p.project(asked);
    const [frame] = p.handOff({ _id: 'conv-1', messages: [{ messageType: 'user_query', content: 'q' }] }, { requestId: 'r' });
    expect(frame!.frame.type).to.equal('RUN_FINISHED');
    const result = frame!.frame['result'] as { conversation: Record<string, unknown>; meta: Record<string, unknown> };
    expect(result.conversation['status']).to.equal(AWAITING_APPROVAL_STATUS);
    const messages = result.conversation['messages'] as Array<Record<string, unknown>>;
    expect(messages.at(-1)).to.deep.equal({ messageType: 'bot_response', content: AWAITING_APPROVAL_TEXT });
    expect(result.meta['awaiting']).to.equal('approval');
  });
});
