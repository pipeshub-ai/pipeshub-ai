import { expect } from 'chai';
import { canonicalJsonBytes } from '../../../../src/modules/agent-harness/contracts/canonical-json';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { inlineContentRef } from '../../../../src/modules/agent-harness/domain/run-content';
import { AGUI } from '../../../../src/modules/agent-harness/projection/frames';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';
import {
  CHILD_DEFAULT_FAILURE,
  childFailureMessage,
} from '../../../../src/modules/agent-harness/projection/transcript';

const PARENT = 'run-parent';
const CHILD = 'run-child';

const event = (
  seq: number,
  type: string,
  data: Record<string, unknown>,
  runId = PARENT,
  streamId = 'session-1',
): JournalEvent => ({
  orgId: 'org-1',
  sessionId: 'session-1',
  streamId,
  seq,
  runId,
  type,
  schemaVersion: 1,
  critical: true,
  visibility: 'owner',
  ts: `2026-10-03T12:00:0${String(seq)}.000Z`,
  writer: 'node',
  data,
});

const snapshots = (frames: ReturnType<RunProjector['project']>) =>
  frames
    .map((f) => f.frame)
    .filter((frame) => frame.type === AGUI.STATE_SNAPSHOT)
    .map((frame) => frame['snapshot'] as Record<string, unknown>);

const context = (runId: string, parentRunId?: string) => ({
  runId,
  sessionId: 'session-1',
  conversationId: 'session-1',
  goal: 'goal',
  audience: 'owner' as const,
  ...(parentRunId !== undefined ? { parentRunId } : {}),
});

describe('agent-harness projection: child runs (P6.1, minimal)', () => {
  it("PJ-1: the parent's stream shows each child nested under it, then its ending", () => {
    const projector = new RunProjector(context(PARENT));
    const frames = projector.project([
      event(1, 'run.claimed', { attempt: 1, fence: 1, leaseMs: 1000 }),
      event(2, 'turn.started', { turn: 1 }),
      event(3, 'child.spawned', {
        childRunId: CHILD,
        invocationId: 't1.0',
        taskId: 'tickets',
        grantDigest: `sha256:${'a'.repeat(64)}`,
      }),
      event(4, 'child.completed', {
        childRunId: CHILD,
        resultRef: { ref: 'inline:e30=', hash: `sha256:${'b'.repeat(64)}`, size: 2 },
        labels: { integrity: 'internal' },
        outcome: 'failed',
        errorType: 'model_error',
      }),
    ]);
    const children = snapshots(frames).filter((s) => s['run_id'] === CHILD);
    expect(children.map((s) => s['status'])).to.deep.equal(['running', 'failed']);
    for (const snapshot of children) {
      expect(snapshot).to.include({
        parent_run_id: PARENT,
        role_name: 'pipeshub-subagent',
        goal_description: 'tickets',
        started_at: '2026-10-03T12:00:03.000Z',
      });
    }
    const ids = frames.map((f) => f.id);
    expect(new Set(ids).size).to.equal(ids.length);
  });

  it("PJ-1: the parent's own snapshot names the child it waits on", () => {
    const projector = new RunProjector(context(PARENT));
    projector.project([
      event(1, 'run.claimed', { attempt: 1, fence: 1, leaseMs: 1000 }),
      event(2, 'child.spawned', {
        childRunId: CHILD,
        invocationId: 't1.0',
        grantDigest: `sha256:${'a'.repeat(64)}`,
      }),
    ]);
    const frames = projector.project([event(3, 'turn.started', { turn: 1 })]);
    const own = snapshots(frames).filter((s) => s['run_id'] === PARENT);
    expect(own.at(-1)?.['current_child_run_id']).to.equal(CHILD);
  });

  const spawnTurn = (seq: number) => [
    event(seq, 'turn.started', { turn: 1 }),
    event(seq + 1, 'model.response', {
      messageRef: inlineContentRef(
        canonicalJsonBytes({
          text: '',
          toolCalls: [
            { id: 'c1', name: 'spawn_agent', args: { role: 'jira', goal: 'g' } },
          ],
        }),
      ),
      toolCalls: [
        { callId: 'c1', modelName: 'spawn_agent', argsDigest: `sha256:${'c'.repeat(64)}` },
      ],
      usage: { inputTokens: 0, outputTokens: 0 },
      provider: 'director',
      transport: 'synthetic',
    }),
  ];

  const completed = (seq: number, outcome: string, errorType?: string) =>
    event(seq, 'child.completed', {
      childRunId: CHILD,
      resultRef: { ref: 'inline:e30=', hash: `sha256:${'b'.repeat(64)}`, size: 2 },
      labels: { integrity: 'internal' },
      outcome,
      ...(errorType !== undefined ? { errorType } : {}),
    });

  it("NK-6 (K-L): legacy's sub-agent frames nest the child under the parent in the web chat", () => {
    const projector = new RunProjector(context(PARENT));
    const frames = projector
      .project([
        event(1, 'run.claimed', { attempt: 1, fence: 1, leaseMs: 1000 }),
        ...spawnTurn(2),
        event(4, 'child.spawned', {
          childRunId: CHILD,
          invocationId: 't1.0',
          taskId: 'tickets',
          grantDigest: `sha256:${'a'.repeat(64)}`,
        }),
        completed(5, 'answered'),
      ])
      .map((f) => f.frame)
      .filter((f) => f['runId'] === CHILD || (typeof f['stepName'] === 'string' && f['stepName'].startsWith('sub_agent:')));
    expect(frames.map((f) => f.type)).to.deep.equal([
      AGUI.STEP_STARTED,
      AGUI.RUN_STARTED,
      AGUI.STATE_SNAPSHOT,
      AGUI.STATE_SNAPSHOT,
      AGUI.RUN_FINISHED,
      AGUI.STEP_FINISHED,
    ]);
    expect(frames[0]).to.include({ stepName: 'sub_agent:pipeshub-subagent-jira', runId: PARENT });
    expect(frames[1]).to.include({ runId: CHILD, parentRunId: PARENT, threadId: 'session-1' });
    expect(frames[4]).to.include({ runId: CHILD, parentRunId: PARENT });
  });

  it('NK-6: a failed or cancelled child ends with a nested RUN_ERROR; a shared viewer gets its lifecycle only (CJFIX-B), other audiences none', () => {
    for (const [outcome, errorType, message] of [
      ['failed', 'auth_error', CHILD_DEFAULT_FAILURE],
      ['cancelled', 'cancelled', 'This sub-task was stopped before it finished.'],
    ] as const) {
      const frames = new RunProjector(context(PARENT))
        .project([
          event(1, 'run.claimed', { attempt: 1, fence: 1, leaseMs: 1000 }),
          event(2, 'child.spawned', {
            childRunId: CHILD,
            invocationId: 't1.0',
            grantDigest: `sha256:${'a'.repeat(64)}`,
          }),
          completed(3, outcome, errorType),
        ])
        .map((f) => f.frame);
      const error = frames.find((f) => f.type === AGUI.RUN_ERROR);
      expect(error).to.include({ runId: CHILD, parentRunId: PARENT, message, code: 'agent_error', errorType });
      expect(frames.find((f) => f.type === AGUI.STEP_STARTED)).to.include({
        stepName: 'sub_agent:pipeshub-subagent',
      });
    }
    const as = (audience: 'shared' | 'slack' | 'external') =>
      new RunProjector({ ...context(PARENT), audience })
        .project([
          event(1, 'run.claimed', { attempt: 1, fence: 1, leaseMs: 1000 }),
          event(2, 'child.spawned', { childRunId: CHILD, invocationId: 't1.0', grantDigest: `sha256:${'a'.repeat(64)}` }),
          completed(3, 'failed', 'auth_error'),
        ])
        .map((f) => f.frame)
        .filter((f) => f['parentRunId'] !== undefined || f.type === AGUI.STEP_STARTED || f.type === AGUI.STEP_FINISHED);
    // D14: the viewer's saved answer holds the child's card, so its live stream says the child started and ended.
    expect(as('shared').map((f) => f.type)).to.deep.equal([AGUI.RUN_STARTED, AGUI.RUN_ERROR]);
    expect(as('shared')[0]).to.include({ runId: CHILD, parentRunId: PARENT });
    expect(as('shared')[1]).to.include({ runId: CHILD, parentRunId: PARENT, errorType: 'auth_error' });
    expect(as('slack')).to.deep.equal([]);
    expect(as('external')).to.deep.equal([]);
  });

  const childError = (outcome: string, errorType?: string) =>
    new RunProjector(context(PARENT))
      .project([
        event(1, 'run.claimed', { attempt: 1, fence: 1, leaseMs: 1000 }),
        event(2, 'child.spawned', { childRunId: CHILD, invocationId: 't1.0', grantDigest: `sha256:${'a'.repeat(64)}` }),
        completed(3, outcome, errorType),
      ])
      .map((f) => f.frame)
      .find((f) => f.type === AGUI.RUN_ERROR);

  it('WP62-ERR-1: a child that ended commit_rejected shows a plain message; the code stays a machine field', () => {
    const error = childError('failed', 'commit_rejected');
    expect(error).to.include({
      runId: CHILD,
      parentRunId: PARENT,
      code: 'agent_error',
      errorType: 'commit_rejected',
      message: 'This sub-task stopped because its work could not be saved. Work saved before that is kept.',
    });
  });

  it('WP62-ERR-2: no machine code reaches the message, whatever the child ended with', () => {
    for (const errorType of ['commit_rejected', 'auth_error', 'limit_stall', 'x.unknown-code', 'budget_exhausted', 'deadline_exceeded']) {
      const message = childError('failed', errorType)?.['message'];
      expect(message, errorType).to.be.a('string');
      expect(message as string, errorType).not.to.include(errorType);
      expect(message as string, errorType).not.to.match(/^[a-z0-9_.-]+$/);
    }
    // A child failure the journal gave no reason for still reads as words, and carries no errorType.
    const bare = childError('failed');
    expect(bare).to.include({ message: CHILD_DEFAULT_FAILURE });
    expect(bare).not.to.have.property('errorType');
  });

  it('WP62-ERR-3: codes the root run words reuse its words; unknown codes get the generic line', () => {
    expect(childFailureMessage('failed', 'budget_exhausted')).to.equal(
      'This task reached its spending limit and was stopped.',
    );
    expect(childFailureMessage('failed', 'something_new')).to.equal(CHILD_DEFAULT_FAILURE);
    expect(childFailureMessage('cancelled', undefined)).to.equal('This sub-task was stopped before it finished.');
  });

  it('PJ-2: a child run projected alone names its parent', () => {
    const projector = new RunProjector(context(CHILD, PARENT));
    const frames = projector.project([
      event(1, 'run.claimed', { attempt: 1, fence: 1, leaseMs: 1000 }, CHILD, CHILD),
      event(2, 'turn.started', { turn: 1 }, CHILD, CHILD),
    ]);
    const own = snapshots(frames);
    expect(own.length).to.be.greaterThan(0);
    for (const snapshot of own) {
      expect(snapshot).to.include({
        parent_run_id: PARENT,
        role_name: 'pipeshub-subagent',
      });
    }
  });
});
