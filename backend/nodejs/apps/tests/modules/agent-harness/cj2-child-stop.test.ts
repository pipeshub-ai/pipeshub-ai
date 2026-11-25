/**
 * CLASSIC-JOBS BG-2: the job card offers Stop on every durable child, a loop sub-agent's as well as a code_task
 * engine's, so `POST …/runs/:childRunId/cancel` must accept the child run id its `sub_agent` part carries. Every
 * child is admitted by `childRunDocument` in its parent's session as its parent's actor, which is exactly what the
 * session route's run lookup checks.
 */
import { expect } from 'chai';
import { SessionRunService } from '../../../src/modules/agent-harness/application/session-run-service';
import { childRunDocument } from '../../../src/modules/agent-harness/domain/child-runs';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';

const SCOPE = { orgId: 'org-1', actorId: 'user-1', sessionId: 's-1' };

const parent = {
  runId: 'run-root',
  orgId: 'org-1',
  sessionId: 's-1',
  actorId: 'user-1',
  status: 'running',
  revision: 3,
  absoluteDeadlineAt: 10_000,
} as unknown as HarnessRun;

const loopChild = childRunDocument(
  parent,
  { childRunId: 'run-loop-child', invocationId: 'inv-1', requestHash: 'sha256:x', input: { query: 'look it up' } } as never,
  1_000,
  { digest: (_org: string, key: string) => key },
);

const world = (runs: Record<string, HarnessRun>) => {
  const controlled: string[] = [];
  const service = new SessionRunService(
    {
      get: async (_org: string, runId: string) => runs[runId] ?? null,
      control: async (command: { runId: string }) => {
        controlled.push(command.runId);
        return { run: { status: 'cancelling' } };
      },
      controlReceipt: async () => null,
    } as never,
    { findStreamAccess: async () => ({ audience: 'owner' }) } as never,
  );
  return { service, controlled };
};

describe('BG-2: Stop on a loop sub-agent (CLASSIC-JOBS)', () => {
  it('a child is admitted in its parent’s session as its parent’s actor', () => {
    expect(loopChild).to.include({ runId: 'run-loop-child', sessionId: 's-1', actorId: 'user-1' });
    expect(loopChild.parent?.runId).to.equal('run-root');
  });

  it('the session cancel route takes the child’s run id and cancels that child', async () => {
    const w = world({ 'run-root': parent, 'run-loop-child': loopChild });
    await w.service.control({ ...SCOPE, runId: 'run-loop-child', idempotencyKey: 'stop-run-loop-child', action: 'cancel' });
    expect(w.controlled).to.deep.equal(['run-loop-child']);
  });

  it('another person’s child is not theirs to stop', async () => {
    const w = world({ 'run-loop-child': loopChild });
    const refused = await w.service
      .control({ ...SCOPE, actorId: 'user-2', runId: 'run-loop-child', idempotencyKey: 'k', action: 'cancel' })
      .catch((e: unknown) => e);
    expect(refused).to.be.instanceOf(HarnessNotFoundError);
    expect(w.controlled).to.deep.equal([]);
  });
});
