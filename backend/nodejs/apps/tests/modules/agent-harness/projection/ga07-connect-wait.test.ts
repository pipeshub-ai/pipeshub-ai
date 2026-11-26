/**
 * GA-07 (G-12, C20.13): a call that waits for its run's owner to connect an
 * item, as Node shows and decides it. The worker journals
 * `interaction.requested` kind `connect` (`ix.connect.*`) and suspends the
 * run as for an approval; the owner's chat gets a connect card (live and on
 * the run's timeline), the waiting count counts it, a shared viewer gets
 * none of it, and only the run's actor may say it is connected.
 */
import { expect } from 'chai';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import {
  connectRefusal,
  decisionRefusal,
  isConnectInteraction,
} from '../../../../src/modules/agent-harness/domain/interactions';
import {
  CONNECT_PAUSES_FEATURE,
  NODE_CAPABILITIES,
} from '../../../../src/modules/agent-harness/domain/node-capabilities';
import { AguiFrame, Audience } from '../../../../src/modules/agent-harness/projection/frames';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { buildParts } from '../../../../src/modules/agent-harness/projection/transcript';
import { interactionRequestedEventSchema } from '../../../../src/modules/agent-harness/contracts/generated/core/events/interaction-requested';
import { projectorContext, readJournal } from '../../../contracts/support/durable';

const base = readJournal('agent_quick_tool_call');
const asked = base.events.filter((e) => e.seq <= 12);
const last = asked[asked.length - 1] as JournalEvent;
const IX = 'ix.connect.t1.0';
const ENGINE_IX = 'ix.connect.k0123456789abcdef0123456789abcdef01234567';
const JIRA = { item: 'Jira', itemKind: 'toolset', instanceId: 'inst_jira_1' };

const event = (seq: number, type: string, writer: 'worker' | 'node', data: Record<string, unknown>): JournalEvent => ({
  ...last,
  seq,
  type,
  writer,
  critical: true,
  data,
});
const requested = (seq: number, interactionId = IX, connect: Record<string, unknown> = JIRA) =>
  event(seq, 'interaction.requested', 'worker', {
    interactionId,
    kind: 'connect',
    bindingDigest: `sha256:${'1'.repeat(64)}`,
    connect,
  });
const resolved = (seq: number, decision: string, interactionId = IX) =>
  event(seq, 'interaction.resolved', 'node', { interactionId, decision, by: 'p_user', channel: 'web' });

const project = (events: JournalEvent[], audience: Audience = 'owner'): AguiFrame[] => {
  const projector = new RunProjector(projectorContext(base, audience));
  projector.begin();
  return projector.project(events).map((f) => f.frame);
};
const values = (frames: AguiFrame[], name: string): Array<Record<string, unknown>> =>
  frames
    .filter((f) => f.type === 'CUSTOM' && f['name'] === name)
    .map((f) => f['value'] as Record<string, unknown>);

describe('GA-07 connect wait (G-12): Node', () => {
  it('GA-07: the contract accepts the event; Node offers connect waits now that the chat can answer them', () => {
    // WP-51: the chat's connect card answers the wait, so a worker may pause on it instead of "connect it, then ask again".
    expect(NODE_CAPABILITIES.features).to.include(CONNECT_PAUSES_FEATURE);
    const parsed = interactionRequestedEventSchema.safeParse(requested(13));
    expect(parsed.success, JSON.stringify(parsed.error?.issues)).to.equal(true);
    const bad = interactionRequestedEventSchema.safeParse(requested(13, IX, { ...JIRA, itemKind: 'app' }));
    expect(bad.success).to.equal(false);
  });

  it("GA-07: the run's timeline names the call that waits, the item in words and where to connect it", () => {
    const timeline = foldTimeline([...asked, requested(13)], base.runId);
    expect(timeline.connects).to.have.length(1);
    expect(timeline.connects[0]).to.deep.include({
      interactionId: IX,
      toolCallId: 'call-search',
      item: 'Jira',
      itemKind: 'toolset',
      connectPath: '/workspace/actions',
      status: 'pending',
      seq: 13,
    });
    expect(timeline.approvals, 'a connect wait is not an approval').to.deep.equal([]);
  });

  it('GA-07: connected, declined, expired, or closed when the run ended first', () => {
    const status = (...tail: JournalEvent[]) =>
      foldTimeline([...asked, requested(13), ...tail], base.runId).connects[0]?.status;
    expect(status(resolved(14, 'approved'))).to.equal('connected');
    expect(status(resolved(14, 'denied'))).to.equal('declined');
    expect(status(resolved(14, 'expired'))).to.equal('expired');
    expect(status(event(14, 'run.cancelled', 'node', {}))).to.equal('closed');
  });

  it("GA-07: an engine's call waits under its key id; the card names the item, an MCP server's link its own", () => {
    const timeline = foldTimeline(
      [...asked, requested(13, ENGINE_IX, { item: 'GitHub', itemKind: 'mcp', instanceId: 'mcp-9' })],
      base.runId,
    );
    expect(timeline.connects[0]).to.deep.include({
      interactionId: ENGINE_IX,
      toolCallId: ENGINE_IX,
      item: 'GitHub',
      connectPath: '/workspace/mcp-servers/personal',
    });
  });

  it("GA-07: the owner's chat gets the card and the waiting count; the decision closes both", () => {
    const frames = project([...asked, requested(13), resolved(14, 'approved')]);
    const [card] = values(frames, 'connect_requested');
    // The card opens the owner's own sign-in for that instance (WP-51): it names it, never shows it.
    expect(card).to.deep.include({
      interactionId: IX,
      item: 'Jira',
      itemKind: 'toolset',
      instanceId: 'inst_jira_1',
      connectPath: '/workspace/actions',
      seq: 13,
      afterTurns: 1,
    });
    expect(values(frames, 'connect_resolved')).to.deep.equal([{ interactionId: IX, status: 'connected' }]);
    expect(values(frames, 'interaction_waiting').map((v) => v['count'])).to.deep.equal([1, 0]);
  });

  it('GA-07: the saved reply keeps the wait where it was asked and what became of it, without the instance', () => {
    const at = (...tail: JournalEvent[]) =>
      buildParts(foldTimeline([...asked, requested(13), ...tail], base.runId)).filter(
        (part) => part['type'] === 'approval',
      );
    const [waiting] = at();
    expect(waiting).to.deep.include({
      type: 'approval',
      kind: 'connect',
      interactionId: IX,
      toolCallId: 'call-search',
      status: 'pending',
      seq: 13,
      afterTurns: 1,
      runId: base.runId,
    });
    expect(waiting?.['connect']).to.deep.equal({ item: 'Jira', itemKind: 'toolset', connectPath: '/workspace/actions' });
    const statuses = [
      at(resolved(14, 'approved')),
      at(resolved(14, 'denied')),
      at(resolved(14, 'expired')),
      at(event(14, 'run.cancelled', 'node', {})),
    ].map(([part]) => part?.['status']);
    expect(statuses).to.deep.equal(['approved', 'denied', 'expired', 'closed']);
  });

  it('GA-07: a shared viewer gets no connect card', () => {
    const frames = project([...asked, requested(13)], 'shared');
    expect(values(frames, 'connect_requested')).to.deep.equal([]);
  });

  it("GA-07: only the run's actor may say it is connected; anyone who may deny may decline", () => {
    const actor = { isRunActor: true, isApprover: false, role: 'owner' as const, isOrgAdmin: false };
    const approver = { isRunActor: false, isApprover: true, role: null, isOrgAdmin: false };
    const admin = { isRunActor: false, isApprover: false, role: null, isOrgAdmin: true };
    expect(isConnectInteraction(IX) && isConnectInteraction(ENGINE_IX)).to.equal(true);
    expect(connectRefusal(actor, 'approved', IX)).to.equal(null);
    expect(connectRefusal(approver, 'approved', IX)).to.equal('CONNECT_OWNER_REQUIRED');
    expect(connectRefusal(admin, 'approved', ENGINE_IX)).to.equal('CONNECT_OWNER_REQUIRED');
    expect(connectRefusal(admin, 'denied', IX) ?? decisionRefusal(admin, 'denied')).to.equal(null);
    expect(connectRefusal(actor, 'answered', IX)).to.equal('DECISION_NOT_APPLICABLE');
    expect(connectRefusal(approver, 'approved', 'ix.approve.t1.0'), 'an approval is unchanged').to.equal(null);
  });
});
