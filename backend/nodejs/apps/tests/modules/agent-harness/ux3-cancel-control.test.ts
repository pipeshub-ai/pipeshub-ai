/**
 * UX3 ST-2: Stop is one request. Without a revision the cancel acts on the
 * run as it is, re-reading it if a worker commit moved it meanwhile; a stale
 * revision the caller did send is still refused.
 */
import { expect } from 'chai';
import { SessionRunService } from '../../../src/modules/agent-harness/application/session-run-service';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { controlSessionRunSchema } from '../../../src/modules/agent-harness/transport/schemas';

const SCOPE = { orgId: 'org-1', actorId: 'user-1', sessionId: 's-1' };

const world = (conflicts: number) => {
  let revision = 4;
  const sent: number[] = [];
  const receipts = new Map<string, unknown>();
  const service = new SessionRunService(
    {
      get: async () => ({ runId: 'r-1', sessionId: 's-1', actorId: 'user-1', revision }),
      control: async (command: { expectedRevision: number; commandId: string }) => {
        sent.push(command.expectedRevision);
        if (sent.length <= conflicts) {
          revision += 1;
          throw new HarnessConflictError('REVISION_CONFLICT', 'Run revision changed');
        }
        revision += 1;
        const receipt = { run: { status: 'cancelling' } };
        receipts.set(command.commandId, receipt);
        return receipt;
      },
      controlReceipt: async (_org: string, _run: string, commandId: string) => receipts.get(commandId) ?? null,
    } as never,
    { findStreamAccess: async () => ({ audience: 'owner' }) } as never,
  );
  return { service, sent };
};

describe('Stop without a revision (UX3 ST-2)', () => {
  it('uses the run’s current revision', async () => {
    const w = world(0);
    await w.service.control({ ...SCOPE, runId: 'r-1', idempotencyKey: 'k', action: 'cancel' });
    expect(w.sent).to.deep.equal([4]);
  });

  it('re-reads a run a worker commit moved meanwhile', async () => {
    const w = world(1);
    await w.service.control({ ...SCOPE, runId: 'r-1', idempotencyKey: 'k', action: 'cancel' });
    expect(w.sent).to.deep.equal([4, 5]);
  });

  it('a revision the caller sent is still a precondition', async () => {
    const w = world(1);
    const refused = await w.service
      .control({ ...SCOPE, runId: 'r-1', expectedRevision: 4, idempotencyKey: 'k', action: 'cancel' })
      .catch((e: unknown) => e);
    expect((refused as HarnessConflictError).code).to.equal('REVISION_CONFLICT');
    expect(w.sent).to.deep.equal([4]);
  });

  it('LAT2: the same Stop sent again answers its first receipt, though that Stop moved the run', async () => {
    const w = world(0);
    await w.service.control({ ...SCOPE, runId: 'r-1', idempotencyKey: 'k', action: 'cancel' });
    const again = await w.service.control({ ...SCOPE, runId: 'r-1', idempotencyKey: 'k', action: 'cancel' });
    expect(again).to.deep.include({ replayed: true });
    expect(w.sent, 'one command').to.deep.equal([4]);
  });

  it('the route takes a cancel with only an idempotency key', () => {
    const params = { agentKey: 'a', sessionId: 's', runId: 'r' };
    expect(controlSessionRunSchema.safeParse({ params, body: { idempotencyKey: 'k' } }).success).to.equal(true);
  });
});
