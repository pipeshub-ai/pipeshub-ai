/**
 * UX3 UE-1..UE-3: a run waiting on an effect whose outcome nobody knows says so
 * in its owner's chat, and only there; replay gives the same frames.
 */
import { expect } from 'chai';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { AguiFrame } from '../../../../src/modules/agent-harness/projection/frames';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';
import { projectorContext, readJournal } from '../../../contracts/support/durable';

const base = readJournal('agent_quick_tool_call');
const intended = base.events.filter((e) => e.seq <= 13);
const last = intended[intended.length - 1] as JournalEvent;
const IX = 'ix.unknown.t1.0';

const event = (seq: number, type: string, writer: 'worker' | 'node', data: Record<string, unknown>): JournalEvent => ({
  ...last,
  seq,
  type,
  writer,
  critical: true,
  data,
});

const parked: JournalEvent[] = [
  ...intended,
  event(14, 'tool.reconciled', 'worker', { invocationId: 't1.0', verdict: 'still_unknown', reason: 'lookup_failed', lookups: 2 }),
  event(15, 'tool.unknown', 'worker', { invocationId: 't1.0' }),
  event(16, 'interaction.requested', 'worker', { interactionId: IX, kind: 'approval' }),
];
const resolved: JournalEvent[] = [
  ...parked,
  event(17, 'interaction.resolved', 'node', { interactionId: IX, decision: 'answered', by: 'p_u', channel: 'web' }),
];

const project = (events: JournalEvent[], audience: 'owner' | 'shared' = 'owner'): Array<{ id: string; frame: AguiFrame }> => {
  const projector = new RunProjector(projectorContext(base, audience));
  projector.begin();
  return projector.project(events);
};
const named = (events: JournalEvent[], name: string, audience: 'owner' | 'shared' = 'owner') =>
  project(events, audience)
    .map((f) => f.frame)
    .filter((f) => f.type === 'CUSTOM' && f['name'] === name)
    .map((f) => f['value'] as Record<string, unknown>);

describe('agent-harness projection: unknown effects in the chat (UX3)', () => {
  it('UE-1: the parked effect reaches the owner as unknown_effect with the call it was', () => {
    const [value] = named(parked, 'unknown_effect');
    expect(value).to.include({ interactionId: IX });
    expect(value?.['toolName']).to.be.a('string').and.not.equal('');
    expect(value?.['toolCallId']).to.be.a('string');
    expect(value?.['reconcile']).to.deep.equal({ verdict: 'still_unknown', reason: 'lookup_failed', lookups: 2 });
    expect(named(parked, 'approval_requested')).to.have.length(0);
  });

  it('UE-1: its resolution is unknown_effect_resolved with the decision', () => {
    expect(named(resolved, 'unknown_effect_resolved')).to.deep.equal([{ interactionId: IX, decision: 'answered' }]);
  });

  it('UE-2: a shared viewer sees neither', () => {
    expect(named(resolved, 'unknown_effect', 'shared')).to.have.length(0);
    expect(named(resolved, 'unknown_effect_resolved', 'shared')).to.have.length(0);
  });

  it('UE-3: replay gives the same frames and ids', () => {
    expect(project(resolved)).to.deep.equal(project(resolved));
    const live = new RunProjector(projectorContext(base, 'owner'));
    live.begin();
    const frames = [...live.project(parked), ...live.project(resolved)];
    expect(frames).to.deep.equal(project(resolved));
  });
});
