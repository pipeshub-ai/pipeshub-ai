/**
 * UX3 QD-1: a run no worker has claimed yet says it is waiting to start, from
 * the first frames of its stream; once claimed, "starting" follows as before.
 */
import { expect } from 'chai';
import { AguiFrame } from '../../../../src/modules/agent-harness/projection/frames';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';
import { projectorContext, readJournal } from '../../../contracts/support/durable';

const base = readJournal('agent_quick_tool_call');
const statuses = (frames: Array<{ frame: AguiFrame }>): unknown[] =>
  frames
    .map((f) => f.frame)
    .filter((f) => f.type === 'STATE_SNAPSHOT')
    .map((f) => (f['snapshot'] as Record<string, unknown>)['status']);

describe('agent-harness projection: a queued run (UX3)', () => {
  it('QD-1: opened while queued, the stream says so right after conversation_created', () => {
    const projector = new RunProjector({ ...projectorContext(base, 'owner'), queuedAt: '2026-10-09T12:00:00.000Z' });
    const begun = projector.begin();
    expect(begun.map((f) => f.frame.type)).to.deep.equal(['CUSTOM', 'STATE_SNAPSHOT']);
    expect(statuses(begun)).to.deep.equal(['queued']);
    expect(statuses(projector.project(base.events)).slice(0, 1)).to.deep.equal(['starting']);
  });

  it('QD-1: opened once claimed, nothing about the queue', () => {
    const projector = new RunProjector(projectorContext(base, 'owner'));
    expect(statuses(projector.begin())).to.deep.equal([]);
  });

  it('QD-1: the frames after the queue keep their ids', () => {
    const queued = new RunProjector({ ...projectorContext(base, 'owner'), queuedAt: '2026-10-09T12:00:00.000Z' });
    queued.begin();
    const plain = new RunProjector(projectorContext(base, 'owner'));
    plain.begin();
    expect(queued.project(base.events)).to.deep.equal(plain.project(base.events));
  });
});
