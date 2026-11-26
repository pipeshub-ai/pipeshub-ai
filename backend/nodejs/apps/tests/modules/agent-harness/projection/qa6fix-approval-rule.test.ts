/**
 * QA6-F6: a card raised by a layer's own approval rule (an org's `writes:
 * ask`) says which rule and whose, on the card's frame, the saved part, a
 * sub-agent's card and the admin's view; a reason older than layers, or one
 * naming a layer this build does not know, is read without it.
 */
import { expect } from 'chai';
import { heldRequestsOf } from '../../../../src/modules/agent-harness/domain/effective-policy';
import { RULE_LAYERS } from '../../../../src/modules/agent-harness/contracts/generated/core/enums';
import { ruleLayer } from '../../../../src/modules/agent-harness/domain/interactions';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import {
  APPROVAL_RULE_LAYERS_FEATURE,
  NODE_CAPABILITIES,
} from '../../../../src/modules/agent-harness/domain/node-capabilities';
import { interactionExplainedEventSchema } from '../../../../src/modules/agent-harness/contracts/generated/core/events/interaction-explained';
import { nodeCapabilitiesSchema } from '../../../../src/modules/agent-harness/contracts/generated/core/node-capabilities';
import { ChildInteractions } from '../../../../src/modules/agent-harness/projection/child-interactions';
import { AguiFrame } from '../../../../src/modules/agent-harness/projection/frames';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';
import { approvalReason, foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { approvalPart } from '../../../../src/modules/agent-harness/projection/transcript';
import { projectorContext, readJournal } from '../../../contracts/support/durable';

const base = readJournal('agent_quick_tool_call');
const asked = base.events.filter((e) => e.seq <= 12);
const last = asked[asked.length - 1] as JournalEvent;
const IX = 'ix.approve.t1.0';

const event = (
  seq: number,
  type: string,
  writer: 'worker' | 'node',
  data: Record<string, unknown>,
  critical = true,
): JournalEvent => ({ ...last, seq, type, writer, critical, data });

const orgRule = { interactionId: IX, rule: 'writes', layer: 'org', toolId: 'jira@default/create_issue', conversationGrant: false };
const ORG_REASON = { rule: 'writes', layer: 'org', sources: [], moreSources: 0, conversationGrant: false };
const requested = event(14, 'interaction.requested', 'worker', {
  interactionId: IX,
  kind: 'approval',
  bindingDigest: `sha256:${'1'.repeat(64)}`,
});
const held = (reason: Record<string, unknown>): JournalEvent[] => [
  ...asked,
  event(13, 'interaction.explained', 'worker', reason, false),
  requested,
];

const card = (events: JournalEvent[]): Record<string, unknown> | undefined => {
  const projector = new RunProjector(projectorContext(base, 'owner'));
  projector.begin();
  return projector
    .project(events)
    .map((f) => f.frame as AguiFrame)
    .find((f) => f.type === 'CUSTOM' && f['name'] === 'approval_requested')?.['value'] as Record<string, unknown> | undefined;
};

describe('agent-harness: an approval names its rule and the layer that set it (QA6-F6)', () => {
  it('F6-8: the card frame of an org rule carries the rule and the layer, and claims nothing it does not know', () => {
    expect(card(held(orgRule))?.['reason']).to.deep.equal(ORG_REASON);
  });

  for (const [rule, layer] of [
    ['writes', 'deployment'],
    ['tool', 'agent'],
    ['effect_class', 'engine'],
    ['flow_external_write', 'flow'],
    ['engine_writes', 'engine'],
  ] as const) {
    it(`F6-8: ${rule} of the ${layer} layer reaches the frame`, () => {
      const reason = card(held({ ...orgRule, rule, layer }))?.['reason'] as Record<string, unknown>;
      expect(reason).to.include({ rule, layer });
    });
  }

  it('F6-8: the saved part keeps them', () => {
    const [view] = foldTimeline(held(orgRule), base.runId).approvals;
    expect(approvalPart(view!, base.runId)['reason']).to.deep.equal(ORG_REASON);
  });

  it('F6-8: the admin\'s view names the rule and the layer, and no sensitivity nobody measured', () => {
    expect(heldRequestsOf(held(orgRule))).to.deep.equal([
      { interactionId: IX, rule: 'writes', layer: 'org', toolId: 'jira@default/create_issue', status: 'pending', ts: last.ts },
    ]);
    const [unexplained] = heldRequestsOf([...asked, requested]);
    expect(unexplained).to.include({ rule: 'approval_policy' }).and.to.not.have.property('layer');
  });

  it('F6-8: a sub-agent\'s card on its parent\'s stream keeps the rule and the layer', async () => {
    const child = held(orgRule).map((e) => ({ ...e, runId: 'run-child', streamId: 'run-child' }));
    const children = new ChildInteractions(() => Promise.resolve(child), base.runId);
    children.track([{ childRunId: 'run-child', role: 'research', spawnSeq: 3, live: true }]);
    const [stated] = await children.pull('run-child');
    const value = stated?.frame['value'] as Record<string, unknown>;
    expect(value['reason']).to.deep.equal(ORG_REASON);
    expect(value).to.include({ childRunId: 'run-child', childRole: 'research' });
  });

  it('F6-7: a reason journaled before layers were named reads as it did', () => {
    const older = { interactionId: IX, rule: 'flow_external_write', toolId: 't', sensitivity: 'private', sources: [], moreSources: 0, conversationGrant: true };
    expect(card(held(older))?.['reason']).to.deep.equal({
      rule: 'flow_external_write',
      sensitivity: 'private',
      sources: [],
      moreSources: 0,
      conversationGrant: true,
    });
  });

  it('F6-7: a layer or a sensitivity this build does not know is not passed on', () => {
    expect(approvalReason({ ...orgRule, layer: 'galaxy' })).to.deep.equal({ rule: 'writes', sources: [], moreSources: 0, conversationGrant: false });
    expect(approvalReason({ ...orgRule, sensitivity: 'secret' })).to.equal(null);
    expect(approvalReason({ layer: 'org' })).to.equal(null);
    expect([...RULE_LAYERS]).to.deep.equal(['deployment', 'org', 'agent', 'engine', 'flow']);
    for (const layer of RULE_LAYERS) expect(ruleLayer(layer)).to.equal(layer);
    for (const not of ['', 'team', 'toString', 'constructor', 3, null, undefined]) expect(ruleLayer(not)).to.equal(undefined);
  });

  it('F6-11: the journal\'s schema takes the new reason, the old one, and refuses an unknown layer', () => {
    const wire = (data: Record<string, unknown>) =>
      interactionExplainedEventSchema.safeParse({
        orgId: 'org_1',
        sessionId: 'ses_1',
        streamId: 'ses_1',
        seq: 8,
        runId: 'run_1',
        type: 'interaction.explained',
        schemaVersion: 1,
        critical: false,
        ts: '2026-10-09T12:00:00.123Z',
        writer: 'worker',
        visibility: 'owner',
        attemptId: 'att_1',
        fence: 3,
        data,
      }).success;
    expect(wire(orgRule)).to.equal(true);
    expect(wire({ interactionId: IX, rule: 'engine_writes', toolId: 't', sensitivity: 'public', sources: [], moreSources: 0, conversationGrant: true })).to.equal(true);
    expect(wire({ ...orgRule, layer: 'team' })).to.equal(false);
    expect(wire({ ...orgRule, rule: 'because' })).to.equal(false);
  });

  it('F6-6: this Node tells a worker it accepts rule layers', () => {
    expect(NODE_CAPABILITIES.features).to.include(APPROVAL_RULE_LAYERS_FEATURE);
    expect(nodeCapabilitiesSchema.safeParse(NODE_CAPABILITIES).success).to.equal(true);
  });
});
