import { expect } from 'chai';
import sinon from 'sinon';
import { InMemoryErasureJobStore } from '../../../src/modules/agent-harness/adapters/in-memory-session-erasure';
import { SessionErasureRunner } from '../../../src/modules/agent-harness/application/session-erasure';
import { ERASURE_STEPS, nextErasureStep } from '../../../src/modules/agent-harness/domain/session-erasure';
import { SessionDataEraser } from '../../../src/modules/agent-harness/ports/session-erasure';

/** Erasure in these tests runs over no conversation document (E7B's guard reads 'absent'). */
const NO_CONVERSATION = { state: () => Promise.resolve('absent' as const) };

/**
 * E7: a step whose store writes and the job's advance must commit together
 * (`completeIn`) advances the job inside its own transaction; the runner
 * then moves on without advancing it a second time.
 */
const request = { orgId: 'org-1', sessionId: 'ses-1', reason: 'user_delete' as const, by: 'p_owner' };

describe('E7 session erasure: a step completed in its own transaction', () => {
  it('advances once, in the step\'s transaction, with the step\'s counts', async () => {
    const jobs = new InMemoryErasureJobStore(() => 1_000);
    const advance = sinon.spy(jobs, 'advance');
    const erasers: SessionDataEraser<string>[] = ERASURE_STEPS.map((step) => ({
      step,
      erase: async (_job, context) => {
        if (step !== 'journal') return { kind: 'done' };
        expect(await context.completeIn('tx-journal', { journal: 3 })).to.equal(true);
        return { kind: 'completed_in_transaction' };
      },
    }));
    await jobs.enqueue(request);
    const runner = new SessionErasureRunner<string>({ jobs: jobs as never, erasers, conversations: NO_CONVERSATION });
    expect(await runner.run((await jobs.claimDue('a', 60_000))!)).to.equal('completed');
    const inTransaction = advance.getCalls().filter((call) => (call.args as unknown[])[4] === 'tx-journal');
    expect(inTransaction).to.have.length(1);
    expect(inTransaction[0]!.args[2]).to.equal(nextErasureStep('journal'));
    expect(advance.callCount).to.equal(ERASURE_STEPS.length);
    expect((await jobs.find('org-1', 'ses-1'))!.counts).to.include({ journal: 3 });
  });
});
