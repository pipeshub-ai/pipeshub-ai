/**
 * WP-97: an approval card is never missing and always says since when it waits.
 *
 * - WP97-N1: run 10's command (the engine's approval line, then the request, no `interaction.explained`) is a plain,
 *   decidable card named from the line: no reason line, the call's arguments, and when it was asked.
 * - WP97-N2: a request whose call line never reached the journal (the engine between turns) is still a card,
 *   named from the tool its reason names; before, it was dropped and the run waited with nothing on screen.
 * - WP97-N3: the saved part keeps `requestedAt`, so a reload says the same.
 */
import { expect } from 'chai';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { AguiFrame } from '../../../../src/modules/agent-harness/projection/frames';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { approvalPart } from '../../../../src/modules/agent-harness/projection/transcript';
import { projectorContext, readJournal } from '../../../contracts/support/durable';

const base = readJournal('agent_quick_tool_call');
const start = base.events.filter((e) => e.seq <= 6);
const last = start[start.length - 1] as JournalEvent;
const IX = 'ix.approve.k76b4f6ade2c36a82401200512c288de4fc860367';
const ASKED_AT = '2026-11-05T22:15:33.435Z';
const SAVE = 'pipeshub@artifacts/save_artifact';

const event = (
  seq: number,
  type: string,
  data: Record<string, unknown>,
  critical = true,
): JournalEvent => ({ ...last, seq, type, writer: 'worker', critical, data, ts: ASKED_AT });

const approvalLine = event(
  7,
  'x.pipeshub.engine.progress',
  {
    tool: 'code_task',
    taskId: base.runId,
    kind: 'approval',
    text: 'artifacts__save_artifact',
    data: { interactionId: IX, toolName: 'artifacts__save_artifact', argsJson: '{"name":"report.md"}' },
    turnId: 'trn_1',
    seq: 7,
  },
  false,
);
const explained = event(
  8,
  'interaction.explained',
  { interactionId: IX, rule: 'writes', layer: 'org', toolId: SAVE, conversationGrant: false },
  false,
);
const requested = event(9, 'interaction.requested', {
  interactionId: IX,
  kind: 'approval',
  bindingDigest: `sha256:${'f'.repeat(64)}`,
});

const cards = (events: JournalEvent[]): Record<string, unknown>[] => {
  const projector = new RunProjector(projectorContext(base, 'owner'));
  projector.begin();
  return projector
    .project(events)
    .map((f) => f.frame)
    .filter((f: AguiFrame) => f.type === 'CUSTOM' && f['name'] === 'approval_requested')
    .map((f) => f['value'] as Record<string, unknown>);
};

describe('agent-harness projection: WP-97 approval cards are never missing', () => {
  it('WP97-N1: run 10\'s request without a reason is a plain card from the call line, with when it was asked', () => {
    const [card] = cards([...start, approvalLine, requested]);
    expect(card).to.include({ interactionId: IX, toolName: 'artifacts__save_artifact', args: '{"name":"report.md"}' });
    expect(card).to.not.have.property('reason');
    expect(card?.['requestedAt']).to.equal(ASKED_AT);
  });

  it('WP97-N2: a request whose call line is missing is a card named from its reason', () => {
    const [card] = cards([...start, explained, requested]);
    expect(card).to.include({ interactionId: IX, toolName: SAVE, args: '{}', requestedAt: ASKED_AT });
    expect(card?.['reason']).to.include({ rule: 'writes', layer: 'org', conversationGrant: false });
  });

  it('WP97-N2: a request with neither its line nor a reason still makes no card, as before', () => {
    expect(cards([...start, requested])).to.deep.equal([]);
  });

  it('WP97-N3: the saved part keeps when it was asked', () => {
    const timeline = foldTimeline([...start, approvalLine, explained, requested], base.runId);
    const [view] = timeline.approvals;
    expect(view?.requestedAt).to.equal(ASKED_AT);
    expect(approvalPart(view!, base.runId)['requestedAt']).to.equal(ASKED_AT);
  });
});
