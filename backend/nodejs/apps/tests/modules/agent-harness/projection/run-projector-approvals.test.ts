/**
 * PJ-1..PJ-4 (P2.6c): the AG-UI frames of a call held for approval. The card
 * is built from the journaled call (name and arguments), only the owner's
 * stream carries it, and a resumed stream replays it identically.
 */
import { expect } from 'chai';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { AguiFrame, Audience } from '../../../../src/modules/agent-harness/projection/frames';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';
import { projectorContext, readJournal } from '../../../contracts/support/durable';

const base = readJournal('agent_quick_tool_call');
/** Up to the call the model asked for (its presentation), before it ran. */
const PRESENTED = (base.events.find((e) => e.type === 'x.pipeshub.tool.call_presented') as JournalEvent).seq;
const asked = base.events.filter((e) => e.seq <= PRESENTED);
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
const held: JournalEvent[] = [
  ...asked,
  event(PRESENTED + 1, 'interaction.requested', 'worker', {
    interactionId: IX,
    kind: 'approval',
    bindingDigest: `sha256:${'1'.repeat(64)}`,
  }),
  event(PRESENTED + 2, 'interaction.resolved', 'node', { interactionId: IX, decision: 'approved', by: 'p_user', channel: 'web' }),
];

const project = (events: JournalEvent[], audience: Audience = 'owner'): AguiFrame[] => {
  const projector = new RunProjector(projectorContext(base, audience));
  projector.begin();
  return projector.project(events).map((f) => f.frame);
};
const named = (frames: AguiFrame[], name: string): AguiFrame[] =>
  frames.filter((f) => f.type === 'CUSTOM' && f['name'] === name);

describe('agent-harness projection: approvals (P2.6c)', () => {
  it('PJ-1: a held call becomes an approval card naming the tool and its arguments', () => {
    const [card] = named(project(held), 'approval_requested');
    expect(card?.['value']).to.deep.equal({
      interactionId: IX,
      toolCallId: 'call-search',
      toolName: 'goldendocs__search_docs',
      args: '{"query": "paid leave"}',
      argsSummary: (card?.['value'] as Record<string, unknown>)['argsSummary'],
      // UXINT-1: the chat orders and groups cards by these.
      seq: PRESENTED + 1,
      afterTurns: 1,
      // WP-113: asks open together share it.
      decidedBefore: 0,
      // WP-97: since when it waits, the request's journal time.
      requestedAt: last.ts,
    });
    expect(card?.['runId']).to.equal(base.runId);
  });

  it('PJ-2: the decision follows the card', () => {
    const frames = project(held);
    const card = frames.findIndex((f) => f['name'] === 'approval_requested');
    const decided = frames.findIndex((f) => f['name'] === 'approval_resolved');
    expect(decided).to.be.greaterThan(card);
    expect(frames[decided]?.['value']).to.deep.equal({ interactionId: IX, decision: 'approved' });
  });

  it('PJ-3: shared viewers, MCP and Slack never see the card or its arguments', () => {
    for (const audience of ['shared', 'external', 'slack'] as const) {
      const frames = project(held, audience);
      expect(named(frames, 'approval_requested'), audience).to.deep.equal([]);
      expect(named(frames, 'approval_resolved'), audience).to.deep.equal([]);
    }
  });

  it('PJ-4: the frames are deterministic, so a resumed stream replays them identically', () => {
    const first = new RunProjector(projectorContext(base));
    first.begin();
    const second = new RunProjector(projectorContext(base));
    second.begin();
    expect(second.project(held)).to.deep.equal(first.project(held));
  });

  it('an unknown-effect interaction and an engine call keyed by effect produce no card', () => {
    const other: JournalEvent[] = [
      ...asked,
      event(PRESENTED + 1, 'interaction.requested', 'worker', {
        interactionId: 'ix.unknown.t1.0',
        kind: 'approval',
        bindingDigest: `sha256:${'1'.repeat(64)}`,
      }),
      event(PRESENTED + 2, 'interaction.requested', 'worker', {
        interactionId: `ix.approve.k${'a'.repeat(40)}`,
        kind: 'approval',
        bindingDigest: `sha256:${'1'.repeat(64)}`,
      }),
    ];
    expect(named(project(other), 'approval_requested')).to.deep.equal([]);
  });
});
