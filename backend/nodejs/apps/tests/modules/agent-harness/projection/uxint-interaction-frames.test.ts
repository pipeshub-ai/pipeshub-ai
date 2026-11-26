/**
 * UXINT-1/3/5: what the chat needs to put a card where the run asked it.
 * - `approval_requested` and the saved approval part carry the journal `seq`, the batch (`afterTurns`) and the
 *   plan step, so the live and the saved card anchor and order the same way.
 * - `interaction_waiting` counts the open approvals and says when the run is paused on them (its hold ran out);
 *   it is the only thing a shared viewer gets of them.
 * - A child's card names its coding engine (`childEngine`) from the child's pinned spec.
 */
import { expect } from 'chai';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { AguiFrame, Audience } from '../../../../src/modules/agent-harness/projection/frames';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { buildParts } from '../../../../src/modules/agent-harness/projection/transcript';
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
const requested = (seq: number, interactionId = IX) =>
  event(seq, 'interaction.requested', 'worker', {
    interactionId,
    kind: 'approval',
    bindingDigest: `sha256:${'1'.repeat(64)}`,
  });
const resolved = (seq: number, decision = 'approved') =>
  event(seq, 'interaction.resolved', 'node', { interactionId: IX, decision, by: 'p_user', channel: 'web' });

const project = (events: JournalEvent[], audience: Audience = 'owner'): AguiFrame[] => {
  const projector = new RunProjector(projectorContext(base, audience));
  projector.begin();
  return projector.project(events).map((f) => f.frame);
};
const values = (frames: AguiFrame[], name: string): Array<Record<string, unknown>> =>
  frames
    .filter((f) => f.type === 'CUSTOM' && f['name'] === name)
    .map((f) => f['value'] as Record<string, unknown>);

describe('agent-harness projection: interaction anchors and waiting (UXINT)', () => {
  it('UXI-1: the live card carries its journal seq and batch; the saved part carries the same', () => {
    const events = [...asked, requested(13), resolved(14)];
    const [card] = values(project(events), 'approval_requested');
    expect(card).to.include({ interactionId: IX, toolCallId: 'call-search', seq: 13, afterTurns: 1 });
    const part = buildParts(foldTimeline(events, base.runId)).find((p) => p['type'] === 'approval');
    expect(part).to.include({ interactionId: IX, toolCallId: 'call-search', seq: 13, afterTurns: 1, status: 'approved' });
  });

  it('UXI-2: the open count follows each request and decision, in journal order after the card', () => {
    const frames = project([...asked, requested(13), resolved(14)]);
    const names = frames
      .filter((f) => f.type === 'CUSTOM' && /approval_|interaction_waiting/.test(String(f['name'])))
      .map((f) => String(f['name']));
    expect(names).to.deep.equal(['approval_requested', 'interaction_waiting', 'approval_resolved', 'interaction_waiting']);
    expect(values(frames, 'interaction_waiting')).to.deep.equal([
      { count: 1, paused: false },
      { count: 0, paused: false },
    ]);
  });

  it('UXI-3: the run suspended on the card is paused; claimed again it is not (D5)', () => {
    const frames = project([
      ...asked,
      requested(13),
      event(14, 'run.suspended', 'node', { reason: 'approval', interactionId: IX }),
      resolved(15),
      event(16, 'run.claimed', 'node', { attempt: 2, fence: 2, leaseMs: 1000 }),
    ]);
    expect(values(frames, 'interaction_waiting')).to.deep.equal([
      { count: 1, paused: false },
      { count: 1, paused: true },
      { count: 0, paused: true },
      { count: 0, paused: false },
    ]);
  });

  it('UXI-4: a suspension that waits on no approval says nothing', () => {
    const frames = project([...asked, event(13, 'run.suspended', 'node', { reason: 'timer' })]);
    expect(values(frames, 'interaction_waiting')).to.deep.equal([]);
  });

  it('UXI-5: a shared viewer gets the count only, never the card', () => {
    const frames = project([...asked, requested(13)], 'shared');
    expect(values(frames, 'approval_requested')).to.deep.equal([]);
    expect(values(frames, 'interaction_waiting')).to.deep.equal([{ count: 1, paused: false }]);
    for (const audience of ['external', 'slack'] as const) {
      expect(values(project([...asked, requested(13)], audience), 'interaction_waiting'), audience).to.deep.equal([]);
    }
  });

  it('UXI-6: an engine run names its engine from the pinned spec; a loop run names none', () => {
    const pinned = base.events.find((e) => e.type === 'run.spec_pinned') as JournalEvent;
    const spec = pinned.data?.['spec'] as Record<string, unknown>;
    const engine = { ...pinned, data: { ...pinned.data, spec: { ...spec, executor: { kind: 'engine', engine: { kind: 'codex' } } } } };
    expect(foldTimeline([engine], base.runId).engineKind).to.equal('codex');
    expect(foldTimeline(base.events, base.runId).engineKind).to.equal(undefined);
  });
});
