/**
 * UX1 AR-8, TO-2, AV-1: the worker's reason for an approval reaches the card's
 * frame and the saved part (never the destination's grant key); a run whose
 * deadline passes while it waits for a person says so; the admin view lists
 * what was held and why.
 */
import { expect } from 'chai';
import { heldRequestsOf } from '../../../../src/modules/agent-harness/domain/effective-policy';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { AguiFrame } from '../../../../src/modules/agent-harness/projection/frames';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import {
  APPROVAL_EXPIRED,
  approvalPart,
  finalAnswer,
  runFailure,
} from '../../../../src/modules/agent-harness/projection/transcript';
import { projectorContext, readJournal } from '../../../contracts/support/durable';

const base = readJournal('agent_quick_tool_call');
const asked = base.events.filter((e) => e.seq <= 12);
const last = asked[asked.length - 1] as JournalEvent;
const IX = 'ix.approve.t1.0';
const KEY = `sha256:${'2'.repeat(64)}`;

const event = (
  seq: number,
  type: string,
  writer: 'worker' | 'node',
  data: Record<string, unknown>,
  critical = true,
): JournalEvent => ({ ...last, seq, type, writer, critical, data });

const reasonData = {
  interactionId: IX,
  rule: 'flow_external_write',
  toolId: 'slack@default/send_message',
  sensitivity: 'private',
  sources: [{ title: 'Q3 plan', recordId: 'rec-1' }, { url: 'https://example.com/live' }, { title: 'x', url: 'javascript:1' }],
  moreSources: 3,
  destination: { label: '#sales', key: KEY },
  conversationGrant: true,
};
const REASON = {
  rule: 'flow_external_write',
  sensitivity: 'private',
  sources: [{ title: 'Q3 plan', recordId: 'rec-1' }, { url: 'https://example.com/live' }, { title: 'x' }],
  moreSources: 3,
  destination: '#sales',
  conversationGrant: true,
};
const requested = event(14, 'interaction.requested', 'worker', {
  interactionId: IX,
  kind: 'approval',
  bindingDigest: `sha256:${'1'.repeat(64)}`,
});
const held: JournalEvent[] = [...asked, event(13, 'interaction.explained', 'worker', reasonData, false), requested];

const project = (events: JournalEvent[]): AguiFrame[] => {
  const projector = new RunProjector(projectorContext(base, 'owner'));
  projector.begin();
  return projector.project(events).map((f) => f.frame);
};
const card = (events: JournalEvent[]) =>
  project(events).find((f) => f.type === 'CUSTOM' && f['name'] === 'approval_requested')?.['value'] as
    | Record<string, unknown>
    | undefined;

describe('agent-harness projection: why an approval is asked (UX1)', () => {
  it('AR-8: the card frame carries the reason, with sources and destination label but never the grant key', () => {
    const value = card(held);
    expect(value?.['reason']).to.deep.equal(REASON);
    expect(JSON.stringify(value)).not.to.contain(KEY);
  });

  it("an edition rule's own field reaches the card frame and the saved part as journaled", () => {
    const editionPart = { level: 'l2', who: 'admins' };
    const data = { interactionId: IX, rule: 'edition_rule', layer: 'org', toolId: 'slack@default/send_message', conversationGrant: false, editionPart };
    const events = [...asked, event(13, 'interaction.explained', 'worker', data, false), requested];
    const expected = { rule: 'edition_rule', layer: 'org', sources: [], moreSources: 0, conversationGrant: false, editionPart };
    expect(card(events)?.['reason']).to.deep.equal(expected);
    const [view] = foldTimeline(events, base.runId).approvals;
    expect(approvalPart(view!, base.runId)['reason']).to.deep.equal(expected);
  });

  it('AR-8: a plain policy ask keeps today’s frame', () => {
    expect(card([...asked, requested])).not.to.have.property('reason');
  });

  it('AR-8: a reason written by Node is ignored', () => {
    const forged = [...asked, event(13, 'interaction.explained', 'node', reasonData, false), requested];
    expect(card(forged)).not.to.have.property('reason');
  });

  it('AR-8: the saved part keeps the reason and the conversation scope; replay is identical', () => {
    const decided = [
      ...held,
      event(15, 'interaction.resolved', 'node', { interactionId: IX, decision: 'approved', by: 'p_u', channel: 'web' }),
      event(16, 'interaction.granted', 'node', { interactionId: IX, scope: 'conversation' }, false),
    ];
    const timeline = foldTimeline(decided, base.runId);
    const [view] = timeline.approvals;
    expect(view?.scope).to.equal('conversation');
    const part = approvalPart(view!, base.runId);
    expect(part['reason']).to.deep.equal(REASON);
    expect(part['scope']).to.equal('conversation');
    expect(project(decided)).to.deep.equal(project(decided));
  });

  it('TO-2: the deadline passing while a person still had to decide is said plainly', () => {
    const expired = [...held, event(15, 'run.failed', 'node', { errorType: 'deadline_exceeded' })];
    const timeline = foldTimeline(expired, base.runId);
    expect(timeline.approvals[0]?.status).to.equal('expired');
    const answer = finalAnswer(timeline);
    expect(answer?.errorCode).to.equal(APPROVAL_EXPIRED);
    expect(answer?.answer).to.match(/approval that nobody gave in time/);
    expect(runFailure(timeline).code).to.equal(APPROVAL_EXPIRED);
  });

  it('TO-2: a deadline with no approval waiting keeps today’s message', () => {
    const timeline = foldTimeline([...asked, event(13, 'run.failed', 'node', { errorType: 'deadline_exceeded' })], base.runId);
    expect(finalAnswer(timeline)?.errorCode).to.equal('deadline_exceeded');
  });
});

describe('effectivePolicy.heldRequests (UX1 AV-1)', () => {
  it('lists each approval with its rule, sensitivity, decision and scope; never titles or arguments', () => {
    const other = 'ix.approve.t2.0';
    const events = [
      ...held,
      event(15, 'interaction.resolved', 'node', { interactionId: IX, decision: 'approved', by: 'p_u', channel: 'web' }),
      event(16, 'interaction.granted', 'node', { interactionId: IX, scope: 'conversation' }, false),
      event(17, 'interaction.requested', 'worker', { interactionId: other, kind: 'approval', bindingDigest: KEY }),
      event(18, 'interaction.requested', 'worker', { interactionId: 'ix.unknown.t3.0', kind: 'approval', bindingDigest: KEY }),
    ];
    const held_ = heldRequestsOf(events);
    expect(held_).to.deep.equal([
      {
        interactionId: IX,
        rule: 'flow_external_write',
        toolId: 'slack@default/send_message',
        sensitivity: 'private',
        status: 'approved',
        scope: 'conversation',
        ts: last.ts,
      },
      { interactionId: other, rule: 'approval_policy', status: 'pending', ts: last.ts },
    ]);
    expect(JSON.stringify(held_)).not.to.contain('Q3 plan');
  });

  it('is empty for a run that held nothing', () => {
    expect(heldRequestsOf(asked)).to.deep.equal([]);
  });
});
