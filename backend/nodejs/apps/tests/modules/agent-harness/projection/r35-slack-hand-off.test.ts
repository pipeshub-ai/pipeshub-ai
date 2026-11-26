/**
 * R35-SLACK D-SLACK-ASK (R34FIX-FU-2): the Slack bot reads one stream per turn and shows no card. When the run asks a
 * person's approval, the `slack` audience gets D-MCP-ASK's hand-off: its stream ends at the open card, and the frame
 * names what the run waits on and where the conversation is in PipesHub, which the bot puts in the thread.
 */
import { expect } from 'chai';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { AUDIENCE_VIEWS } from '../../../../src/modules/agent-harness/projection/audiences';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';
import { projectorContext, readJournal } from '../../../contracts/support/durable';

const base = readJournal('agent_quick_tool_call');
const called = base.events.filter((e) => e.seq <= 14);
const last = called[called.length - 1] as JournalEvent;
const IX = 'ix.approve.t1.0';
const event = (seq: number, type: string, writer: 'worker' | 'node', data: Record<string, unknown>): JournalEvent => ({
  ...last,
  seq,
  type,
  writer,
  critical: true,
  data,
});
const asks = event(15, 'interaction.requested', 'worker', {
  interactionId: IX,
  kind: 'approval',
  bindingDigest: `sha256:${'1'.repeat(64)}`,
});

describe('R35-SLACK D-SLACK-ASK: a Slack turn that needs an approval hands off instead of holding the thread', () => {
  it('SLACKASK-1: every audience that cannot show a card hands off; a shared viewer reads on in the web app', () => {
    expect(AUDIENCE_VIEWS.slack.handsOff).to.equal(true);
    expect(AUDIENCE_VIEWS.external.handsOff).to.equal(true);
    expect(AUDIENCE_VIEWS.shared.handsOff).to.equal(false);
  });

  it('SLACKASK-2: the Slack hand-off names the waiting call and carries the link the stream gives it', () => {
    const p = new RunProjector(projectorContext(base, 'slack'));
    p.begin();
    p.project([...called, asks]);
    expect(p.awaitingApproval).to.equal(true);
    const [frame] = p.handOff({ _id: 'conv-1', messages: [] }, { requestId: 'r', link: '/chat/?conversationId=conv-1' });
    expect(frame!.frame.type).to.equal('RUN_FINISHED');
    const meta = (frame!.frame['result'] as { meta: Record<string, unknown> }).meta;
    expect(meta['awaiting']).to.equal('approval');
    expect(meta['link']).to.equal('/chat/?conversationId=conv-1');
    expect(meta['waitingOn']).to.deep.equal([{ toolName: 'goldendocs__search_docs' }]);
  });

  it('SLACKASK-3: a decided card is no longer waited on', () => {
    const p = new RunProjector(projectorContext(base, 'slack'));
    p.begin();
    p.project([...called, asks]);
    p.project([event(16, 'interaction.resolved', 'node', { interactionId: IX, decision: 'approved', by: 'p_user', channel: 'web' })]);
    expect(p.awaitingApproval).to.equal(false);
  });
});
