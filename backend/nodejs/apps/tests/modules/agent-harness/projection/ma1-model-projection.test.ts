/**
 * MA1 (QA37-X3, Node half; QA37-X4's source): the model a run and each of its
 * sub-agents shows. A run's label is the route of its latest `model.request`,
 * and until it has called, the first route of its pinned spec; a child's is
 * its role's model from `child.spawned`, else its parent's (inherited). The
 * timeline view, the `sub_agent` transcript part, the live `RUN_STARTED`
 * frame and the Activity view all read the one fold.
 */
import { expect } from 'chai';
import { RunTimelineService } from '../../../../src/modules/agent-harness/application/run-timeline-service';
import type { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';
import type { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { foldActivity } from '../../../../src/modules/agent-harness/projection/activity';
import { AGUI } from '../../../../src/modules/agent-harness/projection/frames';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';
import { childModelLabel, foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { buildParts, failureParts } from '../../../../src/modules/agent-harness/projection/transcript';

const RUN = 'run-ma1';
const A = { provider: 'openai', model: 'gpt-5.4' };
const B = { provider: 'anthropic', model: 'claude-haiku-4-5' };
const DIGEST = `sha256:${'a'.repeat(64)}`;

const journal = (rows: Array<[string, Record<string, unknown>]>, runId = RUN): JournalEvent[] =>
  rows.map(
    ([type, data], index) =>
      ({
        orgId: 'org-1',
        sessionId: 'ses-1',
        streamId: 'ses-1',
        seq: index + 1,
        runId,
        type,
        schemaVersion: 1,
        critical: true,
        visibility: 'owner',
        ts: `2026-10-12T12:00:${String(index + 1).padStart(2, '0')}.000Z`,
        writer: 'node',
        data,
      }) as JournalEvent,
  );

const pinned = (...policy: Array<Record<string, unknown>>): [string, Record<string, unknown>] => [
  'run.spec_pinned',
  { specDigest: DIGEST, spec: { model: { policy } } },
];
const called = (route: Record<string, unknown>): [string, Record<string, unknown>] => [
  'model.request',
  { contextDigest: DIGEST, route, modelAttempt: 1 },
];
const spawned = (childRunId: string, model?: Record<string, unknown>): [string, Record<string, unknown>] => [
  'child.spawned',
  { childRunId, invocationId: `t1.${childRunId.slice(-1)}`, grantDigest: DIGEST, ...(model !== undefined ? { model } : {}) },
];

const subAgents = (parts: Array<Record<string, unknown>>) =>
  parts.filter((part) => part['type'] === 'sub_agent').map((part) => [part['runId'], part['model'] ?? null]);

describe('MA1 the model a run shows (QA37-X3)', () => {
  it('QA37-X3: a run that has not called yet shows the first route of its pinned spec, without its access', () => {
    const timeline = foldTimeline(journal([pinned({ ...A, access: { route: 'gateway', accountKind: 'org_api_key' } }, { ...B, on: ['429_exhausted'] })]), RUN);
    expect(timeline.model).to.deep.equal(A);
  });

  it('QA37-X3: once it has called, the label is what its journal says it called, the latest call', () => {
    const timeline = foldTimeline(journal([pinned(A), called(A), called(B), pinned(A)]), RUN);
    expect(timeline.model).to.deep.equal(B);
  });

  it('QA37-X3: a run pinned to the org default names no model until it calls one', () => {
    const waiting = journal([pinned({ provider: 'pipeshub', model: 'org-default' })]);
    expect(foldTimeline(waiting, RUN).model).to.equal(undefined);
    expect(foldTimeline([...waiting, ...journal([called(A)]).map((event) => ({ ...event, seq: 2 }))], RUN).model).to.deep.equal(A);
  });

  it('QA37-X3: a journal that names no model (an older run, an unreadable spec) shows none', () => {
    for (const rows of [[], [['run.spec_pinned', { specDigest: DIGEST }]], [pinned()], [pinned({ provider: 'openai' })], [called({ model: '' })]] as Array<Array<[string, Record<string, unknown>]>>) {
      expect(foldTimeline(journal(rows), RUN).model).to.equal(undefined);
    }
  });

  it('QA37-X3: another run’s events in the stream never label this one', () => {
    expect(foldTimeline(journal([pinned(B), called(B)], 'run-other'), RUN).model).to.equal(undefined);
  });
});

describe('MA1 the model each sub-agent shows (QA37-X3)', () => {
  const EVENTS = journal([
    pinned(A),
    spawned('run-c1', { ...B, role: 'researcher' }),
    spawned('run-c2'),
    called(A),
  ]);

  it('QA37-X3: a role-assigned child shows its role and model; one that inherited shows its parent’s, with no role', () => {
    const timeline = foldTimeline(EVENTS, RUN);
    expect(timeline.children.map((child) => [child.runId, childModelLabel(timeline, child)])).to.deep.equal([
      ['run-c1', { ...B, role: 'researcher' }],
      ['run-c2', A],
    ]);
  });

  it('QA37-X3: the `sub_agent` transcript part carries the model for both, never the role', () => {
    const timeline = foldTimeline(EVENTS, RUN);
    expect(subAgents(buildParts(timeline))).to.deep.equal([
      ['run-c1', B],
      ['run-c2', A],
    ]);
    expect(subAgents(failureParts(timeline))).to.deep.equal([
      ['run-c1', B],
      ['run-c2', A],
    ]);
  });

  it('QA37-X3: a parent that names no model leaves an inheriting child unlabelled; a role-assigned one keeps its own', () => {
    const timeline = foldTimeline(journal([spawned('run-c1', { ...B, role: 'default' }), spawned('run-c2')]), RUN);
    expect(subAgents(buildParts(timeline))).to.deep.equal([
      ['run-c1', B],
      ['run-c2', null],
    ]);
  });

  it('QA37-X3: a `child.spawned` model without its role (not the contract’s shape) is not shown as a role’s', () => {
    const timeline = foldTimeline(journal([pinned(A), spawned('run-c1', B)]), RUN);
    expect(childModelLabel(timeline, timeline.children[0]!)).to.deep.equal(A);
  });

  it('QA37-X3: the live `RUN_STARTED` frame of a child carries the model known when it is spawned', () => {
    const projector = new RunProjector({ runId: RUN, sessionId: 'ses-1', conversationId: 'ses-1', goal: 'g', audience: 'owner' });
    const frames = projector
      .project(EVENTS)
      .map((projected) => projected.frame)
      .filter((frame) => frame.type === AGUI.RUN_STARTED && frame['parentRunId'] === RUN);
    expect(frames.map((frame) => [frame['runId'], frame['model'] ?? null])).to.deep.equal([
      ['run-c1', B],
      ['run-c2', A],
    ]);
    const unlabelled = new RunProjector({ runId: RUN, sessionId: 'ses-1', conversationId: 'ses-1', goal: 'g', audience: 'owner' })
      .project(journal([spawned('run-c2')]))
      .map((projected) => projected.frame)
      .filter((frame) => frame.type === AGUI.RUN_STARTED && frame['parentRunId'] === RUN);
    expect(unlabelled).to.have.length(1);
    expect('model' in unlabelled[0]!).to.equal(false);
  });

  it('QA37-X3: the run timeline view shows the run’s model and each child’s, with the role only where one set it', async () => {
    const run = { orgId: 'org-1', runId: RUN, sessionId: 'ses-1', status: 'running' } as HarnessRun;
    const view = await new RunTimelineService({
      runs: { streamAccess: async () => ({ run, access: { audience: 'owner' } }) } as never,
      journal: { readRun: async (_org, runId, after) => (after === 0 ? EVENTS.filter((event) => event.runId === runId) : []) },
      journals: { resolve: async (events: JournalEvent[]) => [...events] } as never,
      workspaces: null,
      ledger: null,
    }).timeline({} as never, RUN);
    expect(view.model).to.deep.equal(A);
    expect(view.children.map((child) => [child.runId, child.model])).to.deep.equal([
      ['run-c1', { ...B, role: 'researcher' }],
      ['run-c2', A],
    ]);
    const bare = await new RunTimelineService({
      runs: { streamAccess: async () => ({ run, access: { audience: 'owner' } }) } as never,
      journal: { readRun: async () => journal([spawned('run-c2')]).slice(0, 1) },
      journals: { resolve: async (events: JournalEvent[]) => [...events] } as never,
      workspaces: null,
      ledger: null,
    }).timeline({} as never, RUN);
    expect('model' in bare).to.equal(false);
    expect('model' in bare.children[0]!).to.equal(false);
  });
});

describe('MA1 the Activity panel’s model (QA37-X4, Node source)', () => {
  it('QA37-X4: the activity view names the run’s model, and omits it while the journal names none', () => {
    expect(foldActivity(journal([pinned(A), called(B)]), { runId: RUN }).model).to.deep.equal(B);
    expect('model' in foldActivity(journal([]), { runId: RUN })).to.equal(false);
  });
});
