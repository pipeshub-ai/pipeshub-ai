/**
 * R30-LAT (ADR-RUN-05): deleting a chat stops its run when the delete commits. `SessionRunStopper` cancels the
 * session's active run with the erasure step's own command id, skips a run that has ended or is ending, and reads the
 * run again when it moved between the read and the cancel.
 */
import { expect } from 'chai';
import { SessionRunStopper } from '../../../src/modules/agent-harness/application/session-run-stopper';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { erasureCancelCommandId } from '../../../src/modules/agent-harness/domain/session-erasure';

const ORG = 'org-1';
const SESSION = 'sess-1';
const aRun = (over: Partial<HarnessRun> = {}): HarnessRun =>
  ({ orgId: ORG, sessionId: SESSION, runId: 'run-1', status: 'waiting_approval', revision: 3, parent: null, ...over }) as HarnessRun;

function world(active: Array<HarnessRun | null>, fail: Error[] = []) {
  const controls: Array<Record<string, unknown>> = [];
  const reads: string[] = [];
  const stopper = new SessionRunStopper({
    runs: {
      getActive: async (orgId: string, sessionId: string) => {
        reads.push(`${orgId}/${sessionId}`);
        return active.length > 1 ? (active.shift() ?? null) : (active[0] ?? null);
      },
    },
    executions: {
      control: async (command: Record<string, unknown>) => {
        controls.push(command);
        const error = fail.shift();
        if (error !== undefined) throw error;
        return {} as never;
      },
    } as never,
  });
  return { stopper, controls, reads };
}

describe('R30-LAT ADR-RUN-05: deleting a chat stops its run', () => {
  it('STOP-1: the active run is cancelled with the erasure step command id, by the person who deleted', async () => {
    const w = world([aRun()]);
    expect(await w.stopper.stop(ORG, SESSION, 'user-1')).to.equal(1);
    expect(w.reads).to.deep.equal([`${ORG}/${SESSION}`]);
    expect(w.controls).to.have.length(1);
    expect(w.controls[0]).to.include({
      orgId: ORG,
      runId: 'run-1',
      action: 'cancel',
      expectedRevision: 3,
      commandId: erasureCancelCommandId(SESSION, 'run-1'),
      by: 'p_user-1',
    });
  });

  it('STOP-2: no active run, or one that ended or is ending, controls nothing', async () => {
    for (const run of [null, aRun({ status: 'completed' }), aRun({ status: 'cancelling' })]) {
      const w = world([run]);
      expect(await w.stopper.stop(ORG, SESSION, 'user-1')).to.equal(0);
      expect(w.controls).to.deep.equal([]);
    }
  });

  it('STOP-3: a run that moved between the read and the cancel is read again and cancelled at its new revision', async () => {
    const w = world([aRun(), aRun({ status: 'running', revision: 4 })], [new HarnessConflictError('REVISION_CONFLICT', 'moved')]);
    expect(await w.stopper.stop(ORG, SESSION, 'user-1')).to.equal(1);
    expect(w.controls.map((c) => c.expectedRevision)).to.deep.equal([3, 4]);
  });
});
