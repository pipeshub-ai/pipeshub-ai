/**
 * CLEANUP-OPIK: erasing a session deletes its traces in Opik through the query
 * service, page by page of run ids, before the journal tombstones the runs;
 * Opik unreachable is a failure the job retries, never a skipped step.
 */
import { expect } from 'chai';
import { createErasureSteps } from '../../../src/modules/agent-harness/application/session-erasure-steps';
import { SessionErasureRunner } from '../../../src/modules/agent-harness/application/session-erasure';
import { InMemoryErasureJobStore } from '../../../src/modules/agent-harness/adapters/in-memory-session-erasure';
import { ERASURE_STEPS, ErasureJob } from '../../../src/modules/agent-harness/domain/session-erasure';
import { TraceEraser } from '../../../src/modules/agent-harness/ports/session-erasure';

const job = (runs: number): { job: ErasureJob; runIds: string[] } => ({
  runIds: Array.from({ length: runs }, (_, i) => `run-${String(i).padStart(3, '0')}`),
  job: {
    orgId: 'org-1',
    sessionId: 'ses-1',
    reason: 'user_delete',
    by: 'p_u1',
    status: 'pending',
    step: 'traces',
    counts: {},
    attempt: 1,
    failures: 0,
    requestedAt: 0,
    nextAttemptAt: 0,
    completedAt: null,
    lastErrorType: null,
  },
});

const tracesStep = (runIds: string[], traces: TraceEraser | null) => {
  const stub = {} as never;
  const steps = createErasureSteps({
    runs: { runsOf: () => Promise.resolve(runIds.map((runId) => ({ runId, status: 'completed' }))) } as never,
    executions: stub,
    workspaceKeys: stub,
    liveBus: null,
    artifacts: stub,
    traces,
    stores: stub,
    conversations: stub,
    journal: stub,
  });
  return steps.find((step) => step.step === 'traces')!;
};

class RecordingTraces implements TraceEraser {
  readonly calls: Array<{ orgId: string; sessionId: string; runIds: readonly string[] }> = [];
  fail = false;
  async eraseTraces(orgId: string, sessionId: string, runIds: readonly string[]) {
    if (this.fail) throw new Error('opik unavailable');
    this.calls.push({ orgId, sessionId, runIds: [...runIds] });
    return { traces: runIds.length };
  }
}

describe('CLEANUP-OPIK: the traces erasure step', () => {
  it('runs after the artifacts and before the journal tombstones the runs', () => {
    const at = (name: string) => ERASURE_STEPS.indexOf(name as never);
    expect(at('traces')).to.equal(at('artifacts') + 1);
    expect(at('traces')).to.be.lessThan(at('journal'));
  });

  it("names the session's run ids in pages of fifty, and the session once even with no runs", async () => {
    const traces = new RecordingTraces();
    const { job: many, runIds } = job(120);
    expect(await tracesStep(runIds, traces).erase(many, {} as never)).to.deep.equal({ kind: 'done', counts: { traces: 120 } });
    expect(traces.calls.map((call) => call.runIds.length)).to.deep.equal([50, 50, 20]);
    expect(traces.calls.flatMap((call) => call.runIds)).to.deep.equal(runIds);
    expect(new Set(traces.calls.map((call) => `${call.orgId}/${call.sessionId}`))).to.deep.equal(new Set(['org-1/ses-1']));

    const none = new RecordingTraces();
    await tracesStep([], none).erase(job(0).job, {} as never);
    expect(none.calls).to.deep.equal([{ orgId: 'org-1', sessionId: 'ses-1', runIds: [] }]);
  });

  it('does nothing where no trace eraser is composed', async () => {
    expect(await tracesStep(['run-1'], null).erase(job(1).job, {} as never)).to.deep.equal({ kind: 'done', counts: {} });
  });

  it('Opik unavailable fails the step; the job retries it later and completes once Opik answers', async () => {
    const traces = new RecordingTraces();
    traces.fail = true;
    const now = { at: 1_000 };
    const jobs = new InMemoryErasureJobStore(() => now.at);
    const erasers = ERASURE_STEPS.map((name) =>
      name === 'traces'
        ? tracesStep(['run-1'], traces)
        : { step: name, erase: () => Promise.resolve({ kind: 'done' as const, counts: {} }) },
    );
    const runner = new SessionErasureRunner({ jobs, erasers, conversations: { state: () => Promise.resolve('absent' as const) } });
    await jobs.enqueue({ orgId: 'org-1', sessionId: 'ses-1', reason: 'user_delete', by: null });
    const outcomes: string[] = [];
    for (let round = 0; round < 40; round += 1) {
      const claimed = await jobs.claimDue('w', 60_000);
      if (claimed === null) {
        if ((await jobs.find('org-1', 'ses-1'))?.status === 'completed') break;
        now.at += 600_000;
        traces.fail = false;
        continue;
      }
      outcomes.push(await runner.run(claimed));
    }
    expect(outcomes).to.include('retry');
    expect((await jobs.find('org-1', 'ses-1'))?.status).to.equal('completed');
    expect(traces.calls).to.deep.equal([{ orgId: 'org-1', sessionId: 'ses-1', runIds: ['run-1'] }]);
  });
});
