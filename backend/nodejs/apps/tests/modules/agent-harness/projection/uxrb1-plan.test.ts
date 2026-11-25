import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { foldActivity } from '../../../../src/modules/agent-harness/projection/activity';
import { frameFor } from '../../../../src/modules/agent-harness/projection/audiences';
import {
  PLAN_FRAME,
  RunProjector,
} from '../../../../src/modules/agent-harness/projection/run-projector';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { withSteps } from '../../../../src/modules/agent-harness/application/run-timeline-service';
import { buildParts } from '../../../../src/modules/agent-harness/projection/transcript';
import { CONTRACTS_DIR } from '../../../support/harness/contracts';

/** UXR-B1 B1-14..B1-17: one fold of `task.*` for the stream, the timeline and the activity panel. */

interface Revision {
  events: Array<{ type: string; data: Record<string, unknown> }>;
  frame: { steps: unknown[]; currentTaskId: string | null };
}
const VECTOR = JSON.parse(
  readFileSync(join(CONTRACTS_DIR, 'vectors', 'plan-steps.json'), 'utf8'),
) as { revisions: Revision[] };

const RUN = 'run-b1';
let seq = 0;
const event = (
  type: string,
  data: Record<string, unknown>,
  runId = RUN,
): JournalEvent =>
  ({
    orgId: 'org-1', sessionId: 'ses-1', streamId: 'ses-1', seq: (seq += 1), runId, attemptId: `${RUN}.a1`,
    fence: 1, type, schemaVersion: 1, critical: false, ts: '2026-10-13T12:00:00.000Z', writer: 'worker',
    visibility: 'owner', data,
  }) as JournalEvent;

const inline = (value: unknown): { ref: string } => ({
  ref: `inline:${Buffer.from(JSON.stringify(value)).toString('base64url')}`,
});

/** A model turn that calls `name` once. */
const toolTurn = (turn: number, name: string): JournalEvent[] => [
  event('turn.started', { turn }),
  event('model.response', {
    messageRef: inline({ text: '', toolCalls: [{ id: `c${turn}`, name, args: {} }] }),
    toolCalls: [{ callId: `c${turn}`, modelName: name }],
  }),
];

const revisionEvents = (n: number): JournalEvent[] =>
  (VECTOR.revisions[n]?.events ?? []).map((e) => event(e.type, e.data));

const context = (audience: 'owner' | 'shared' = 'owner') => ({
  runId: RUN, sessionId: 'ses-1', conversationId: 'conv-1', goal: 'q', audience,
});

const plans = (frames: ReturnType<RunProjector['project']>) =>
  frames
    .map((f) => f.frame as Record<string, unknown>)
    .filter((f) => f['type'] === 'CUSTOM' && f['name'] === PLAN_FRAME)
    .map((f) => f['value'] as Record<string, unknown>);

describe('UXR-B1: plan steps on the stream and in the timeline', () => {
  beforeEach(() => {
    seq = 0;
  });

  it('B1-14 the timeline folds the plan, the step in progress and tags what happens under it', () => {
    const events = [
      event('run.claimed', { fence: 1 }),
      ...revisionEvents(0),
      ...toolTurn(1, 'search'),
      ...revisionEvents(1),
      ...toolTurn(2, 'read'),
      ...revisionEvents(2),
      ...revisionEvents(3),
      ...toolTurn(3, 'answer'),
    ];
    const timeline = foldTimeline(events, RUN);
    expect(timeline.tasks.map((t) => [t.taskId, t.status])).to.deep.equal([
      ['task-1', 'completed'],
      ['task-2', 'completed'],
      ['task-3', 'completed'],
      ['task-4', 'completed'],
      ['task-5', 'cancelled'],
    ]);
    expect(timeline.currentTaskId).to.equal(null);
    expect(timeline.turns.map((t) => t.stepId)).to.deep.equal(['task-1', 'task-2', undefined]);
    expect(timeline.turns.flatMap((t) => t.calls.map((c) => c.stepId))).to.deep.equal([
      'task-1',
      'task-2',
      undefined,
    ]);
    const parts = withSteps(buildParts(timeline), timeline).filter((p) => p['type'] === 'tool_call');
    expect(parts.map((p) => p['stepId'])).to.deep.equal(['task-1', 'task-2', undefined]);
  });

  it('B1-14 an unknown status or another run never enters the plan', () => {
    const timeline = foldTimeline(
      [
        event('task.created', { taskId: 't1', title: 'x', status: 'exploded', deps: [] }),
        event('task.created', { taskId: 't2', title: 'y', status: 'pending', deps: [] }, 'run-other'),
        event('task.created', { taskId: 't3', title: 'z', status: 'pending', deps: [], asksFirst: true }),
      ],
      RUN,
    );
    expect(timeline.tasks).to.deep.equal([
      { taskId: 't3', title: 'z', status: 'pending', deps: [], asksFirst: true },
    ]);
  });

  it('B1-15 one plan frame per commit, carrying the whole plan', () => {
    const projector = new RunProjector(context());
    const frames = projector.project([event('run.claimed', { fence: 1 }), ...revisionEvents(0)]);
    const first = plans(frames);
    expect(first).to.have.length(1);
    expect(first[0]).to.deep.equal({ runId: RUN, ...VECTOR.revisions[0]?.frame });
  });

  it('B1-17 folding the vector revision by revision gives its frames', () => {
    const projector = new RunProjector(context());
    projector.project([event('run.claimed', { fence: 1 })]);
    VECTOR.revisions.forEach((revision, n) => {
      expect(plans(projector.project(revisionEvents(n)))).to.deep.equal([
        { runId: RUN, ...revision.frame },
      ]);
    });
  });

  it('B1-15 a viewer of a shared conversation never receives the plan', () => {
    const frames = new RunProjector(context('shared')).project([
      event('run.claimed', { fence: 1 }),
      ...revisionEvents(0),
    ]);
    const shared = frames
      .map((f) => frameFor('shared', f.frame, { checkedCitations: () => ({ citations: (c) => c, messages: (m) => m }), checkedArtifacts: () => ({ card: (c) => c, text: (t) => t, messages: (m) => m, completion: (c) => c }), asksUser: () => false }))
      .filter((f) => f !== null);
    expect(shared.filter((f) => (f as Record<string, unknown>)['name'] === PLAN_FRAME)).to.deep.equal([]);
  });

  it('B1-16 however the journal is paged, a resume after any id sent receives the final plan', () => {
    const all = [
      event('run.claimed', { fence: 1 }),
      ...VECTOR.revisions.flatMap((_r, n) => revisionEvents(n)),
    ];
    const planSeqs = (frames: ReturnType<RunProjector['project']>) =>
      frames
        .filter((f) => (f.frame as Record<string, unknown>)['name'] === PLAN_FRAME)
        .map((f) => Number(/^j:(\d+)\./.exec(f.id)?.[1]));
    const live = new RunProjector(context());
    const liveFrames = [
      ...live.project(all.slice(0, 2)),
      ...live.project(all.slice(2, 5)),
      ...live.project(all.slice(5)),
    ];
    const replayed = new RunProjector(context()).project(all);
    // Positions order by journal seq first: the replay's one frame is at the live stream's last.
    expect(planSeqs(replayed)).to.have.length(1);
    expect(planSeqs(liveFrames).slice(-1)).to.deep.equal(planSeqs(replayed));
    expect(Math.max(...planSeqs(liveFrames))).to.equal(planSeqs(replayed)[0]);
    expect(plans(replayed)).to.deep.equal([
      { runId: RUN, ...VECTOR.revisions[VECTOR.revisions.length - 1]?.frame },
    ]);
  });

  it('B1-18 the activity panel reads the same plan', () => {
    const activity = foldActivity(
      VECTOR.revisions.flatMap((_r, n) => revisionEvents(n)),
      { runId: RUN },
    );
    expect(activity.tasks.map((t) => [t.taskId, t.status])).to.deep.equal([
      ['task-1', 'completed'],
      ['task-2', 'completed'],
      ['task-3', 'completed'],
      ['task-4', 'completed'],
      ['task-5', 'cancelled'],
    ]);
    expect(Object.keys(activity.tasks[0] ?? {})).to.deep.equal(['taskId', 'title', 'status']);
  });
});
