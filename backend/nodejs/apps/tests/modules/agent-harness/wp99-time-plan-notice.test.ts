/**
 * WP99-UI (ADR-RUN-01 D4): an engine turn journals its time plan (`x.pipeshub.run.notice` `time_plan`, with
 * `writeBy` and `finishBy`) as it starts; the answer shows the latest plan, a later turn's replacing the one before.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { JournalEvent, validateJournalAppend } from '../../../src/modules/agent-harness/domain/journal';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { buildParts } from '../../../src/modules/agent-harness/projection/transcript';
import { journalOnlySteps, project } from '../../contracts/support/durable';

const RUN = 'run-1';
const FIRST = { code: 'time_plan', writeBy: '2026-11-06T17:20:00.000Z', finishBy: '2026-11-06T17:45:00.000Z' };
const LATER = { code: 'time_plan', writeBy: '2026-11-06T17:30:00.000Z', finishBy: '2026-11-06T17:50:00.000Z' };

function journal(...notices: Array<Record<string, unknown>>): JournalEvent[] {
  return journalOf(false, ...notices);
}

/** `started`: a turn began before the notices, so the run is live when each plan is journaled. */
function journalOf(started: boolean, ...notices: Array<Record<string, unknown>>): JournalEvent[] {
  const specs: Array<[string, Record<string, unknown>, 'node' | 'worker']> = [
    ['run.admitted', { runKind: 'interactive', principalRef: 'p_user' }, 'node'],
    ['run.claimed', { attempt: 1, fence: 1, leaseMs: 60000 }, 'node'],
    ...(started ? [['turn.started', { turn: 1 }, 'worker'] as [string, Record<string, unknown>, 'worker']] : []),
    ...notices.map((data): [string, Record<string, unknown>, 'worker'] => ['x.pipeshub.run.notice', data, 'worker']),
    ['x.pipeshub.answer.finalized', { outcome: 'answered', answer: 'Done.', partTexts: ['Done.'], citations: [] }, 'worker'],
    ['run.completed', { outcome: 'answered' }, 'node'],
  ];
  return specs.map(
    ([type, data, writer], index) =>
      ({
        orgId: 'org-1',
        sessionId: 'ses-1',
        streamId: 'ses-1',
        seq: index + 1,
        runId: RUN,
        attemptId: writer === 'worker' ? `${RUN}.a1` : undefined,
        fence: writer === 'worker' ? 1 : undefined,
        type,
        schemaVersion: 1,
        critical: !type.startsWith('x.'),
        ts: '2026-10-16T12:00:00.000Z',
        writer,
        visibility: 'owner',
        data,
      }) as JournalEvent,
  );
}

const append = (data: Record<string, unknown>) => () =>
  validateJournalAppend({
    orgId: 'org-1',
    sessionId: 'ses-1',
    streamId: 'ses-1',
    runId: RUN,
    writer: 'worker',
    events: [{ type: 'x.pipeshub.run.notice', schemaVersion: 1, critical: false, visibility: 'owner', data }],
  } as never);

describe('WP99-UI (ADR-RUN-01 D4): the run\'s time plan notice', () => {
  it('the worker may journal it with both times; a time that is not a timestamp is refused', () => {
    expect(append(FIRST)).to.not.throw();
    expect(append({ ...FIRST, writeBy: '17:20' })).to.throw();
  });

  it('the answer carries the latest plan only, saved and live', () => {
    const events = journal(FIRST, LATER);
    const parts = buildParts(foldTimeline(events, RUN)).filter((part) => part['type'] === 'notice');
    expect(parts).to.deep.equal([{ type: 'notice', ...LATER, runId: RUN }]);
    const journalled = { scenario: 'wp99', query: 'q', runId: RUN, sessionId: 'ses-1', events, live: [] };
    const frames = project(journalled, journalOnlySteps(journalled)).map((f) => f.frame);
    // Both are journaled before the run's first step, so the live answer starts with the plan that stands.
    expect(frames.filter((f) => f['name'] === 'notice').map((f) => f['value'])).to.deep.equal([LATER]);
  });

  it('a later turn\'s plan journaled while the run is live is sent as it comes, replacing the first', () => {
    const events = journalOf(true, FIRST, LATER);
    const parts = buildParts(foldTimeline(events, RUN)).filter((part) => part['type'] === 'notice');
    expect(parts).to.deep.equal([{ type: 'notice', ...LATER, runId: RUN }]);
    const journalled = { scenario: 'wp99-live', query: 'q', runId: RUN, sessionId: 'ses-1', events, live: [] };
    const frames = project(journalled, journalOnlySteps(journalled)).map((f) => f.frame);
    expect(frames.filter((f) => f['name'] === 'notice').map((f) => f['value'])).to.deep.equal([FIRST, LATER]);
  });
});
