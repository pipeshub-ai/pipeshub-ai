/**
 * E7 session erasure, in memory: the job runner (ER-14, ER-15, ER-19) and
 * each step's own rules (ER-2..ER-6, ER-11). The Mongo stores are covered on
 * a replica set in session-erasure.mongo.itest.ts.
 * The job store itself (ER-18, BF-18) is support/erasure-job-store-contract.ts.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { SealedJournalStore } from '../../../src/modules/agent-harness/application/sealed-journal-store';
import { SessionRunService } from '../../../src/modules/agent-harness/application/session-run-service';
import { SessionErasedError } from '../../../src/modules/agent-harness/domain/errors';
import { transportError } from '../../../src/modules/agent-harness/transport/errors';
import { testCipher } from './support/data-protection';
import { InMemoryErasureJobStore } from '../../../src/modules/agent-harness/adapters/in-memory-session-erasure';
import { InMemoryJournalStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { InMemoryLiveBus } from '../../../src/modules/agent-harness/adapters/in-memory-live-bus';
import {
  ErasureOwnershipLostError,
  SessionErasureRunner,
  SessionErasureWorker,
} from '../../../src/modules/agent-harness/application/session-erasure';
import {
  createErasureSteps,
  ERASURE_RETENTION_PRINCIPAL,
  ErasureStepDependencies,
} from '../../../src/modules/agent-harness/application/session-erasure-steps';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { RunStatus } from '../../../src/modules/agent-harness/domain/execution';
import {
  ERASURE_JOB_LEASE_MS,
  ERASURE_STEPS,
  ErasureJob,
  ErasureStep,
  erasureBackoffMs,
} from '../../../src/modules/agent-harness/domain/session-erasure';
import { validatingPreparer } from '../../../src/modules/agent-harness/ports/journal-store';
import { SessionDataEraser } from '../../../src/modules/agent-harness/ports/session-erasure';
import { ControlRun } from '../../../src/modules/agent-harness/domain/execution';
import { sessionAppend } from '../../../src/modules/agent-harness/domain/session-participants';
import { LeaderLease } from '../../../src/modules/agent-harness/ports/leader-lease';
import { without } from './support/without';

/** Erasure in these tests runs over no conversation document (E7B's guard reads 'absent'). */
const NO_CONVERSATION = { state: () => Promise.resolve('absent' as const) };

const SENTINEL = 'SENTINEL-conversation-text';

class Clock {
  constructor(public value = 1_000_000) {}
  readonly now = (): number => this.value;
}

class Logs {
  readonly lines: Array<{ level: string; message: string; meta: Record<string, unknown> }> = [];
  private add = (level: string) => (message: string, meta: Record<string, unknown> = {}) => {
    this.lines.push({ level, message, meta });
  };
  readonly debug = this.add('debug');
  readonly info = this.add('info');
  readonly warn = this.add('warn');
  readonly error = this.add('error');
  named(message: string) {
    return this.lines.filter((line) => line.message === message);
  }
}

const alwaysLeader: LeaderLease = {
  tryAcquire: () => Promise.resolve(true),
  release: () => Promise.resolve(),
};

/** Records which steps ran; any step can be told to throw, wait or record counts. */
function recordingSteps(behaviour: Partial<Record<ErasureStep, () => Promise<unknown>>> = {}) {
  const ran: ErasureStep[] = [];
  const erasers: SessionDataEraser<unknown>[] = ERASURE_STEPS.map((step) => ({
    step,
    erase: async () => {
      ran.push(step);
      const special = behaviour[step];
      if (special !== undefined) return (await special()) as never;
      return { kind: 'done', counts: { [step.replace(/_(\w)/g, (_m, c: string) => c.toUpperCase())]: 1 } };
    },
  }));
  return { ran, erasers };
}

const request = {
  orgId: 'org-1',
  sessionId: 'ses-1',
  reason: 'user_delete' as const,
  by: 'p_owner',
};

describe('E7 session erasure: the job', () => {
  it('runs every step once, in order, and completes with the counts', async () => {
    const jobs = new InMemoryErasureJobStore(new Clock().now);
    const { ran, erasers } = recordingSteps();
    await jobs.enqueue(request);
    const runner = new SessionErasureRunner({ jobs, erasers, conversations: NO_CONVERSATION });
    expect(await runner.run((await jobs.claimDue('a', 60_000))!)).to.equal('completed');
    expect(ran).to.deep.equal([...ERASURE_STEPS]);
    const job = (await jobs.find('org-1', 'ses-1'))!;
    expect(job.status).to.equal('completed');
    expect(job.step).to.equal(null);
    expect(job.counts).to.include({ cancelRuns: 1, journal: 1, audit: 1 });
  });

  it('refuses a runner that lacks a step, so no store can be skipped silently', () => {
    const { erasers } = recordingSteps();
    expect(
      () => new SessionErasureRunner({
        jobs: new InMemoryErasureJobStore(),
        erasers: erasers.slice(1),
        conversations: NO_CONVERSATION,
      }),
    ).to.throw(/missing: cancel_runs/);
  });

  it('ER-4: a waiting step reschedules the job without a failure and runs nothing after it', async () => {
    const clock = new Clock();
    const jobs = new InMemoryErasureJobStore(clock.now);
    let stopping = true;
    const { ran, erasers } = recordingSteps({
      await_runs_terminal: () =>
        Promise.resolve(
          stopping
            ? { kind: 'wait', retryAfterMs: 5_000, reason: 'runs_stopping' }
            : { kind: 'done' },
        ),
    });
    await jobs.enqueue(request);
    const runner = new SessionErasureRunner({ jobs, erasers, conversations: NO_CONVERSATION });
    expect(await runner.run((await jobs.claimDue('a', 60_000))!)).to.equal('waiting');
    expect(ran).to.deep.equal(['cancel_runs', 'shred_workspace_key', 'await_runs_terminal']);
    let job = (await jobs.find('org-1', 'ses-1'))!;
    expect(job.failures).to.equal(0);
    expect(job.nextAttemptAt).to.equal(clock.value + 5_000);
    expect(await jobs.claimDue('a', 60_000)).to.equal(null);
    clock.value += 5_000;
    stopping = false;
    expect(await runner.run((await jobs.claimDue('a', 60_000))!)).to.equal('completed');
    job = (await jobs.find('org-1', 'ses-1'))!;
    expect(job.status).to.equal('completed');
  });

  for (const failing of ERASURE_STEPS) {
    it(`ER-14: a crash in ${failing} resumes at ${failing} and finishes; earlier steps do not run again`, async () => {
      const clock = new Clock();
      const jobs = new InMemoryErasureJobStore(clock.now);
      let crash = true;
      const { ran, erasers } = recordingSteps({
        [failing]: () => {
          if (crash) {
            crash = false;
            return Promise.reject(new Error('crash after side effects'));
          }
          return Promise.resolve({ kind: 'done' });
        },
      });
      await jobs.enqueue(request);
      const runner = new SessionErasureRunner({ jobs, erasers, conversations: NO_CONVERSATION });
      expect(await runner.run((await jobs.claimDue('a', 60_000))!)).to.equal('retry');
      let job = (await jobs.find('org-1', 'ses-1'))!;
      expect(job.step).to.equal(failing);
      expect(job.failures).to.equal(1);
      expect(job.lastErrorType).to.equal('Error');
      expect(job.nextAttemptAt).to.equal(clock.value + erasureBackoffMs(1));
      clock.value += erasureBackoffMs(1);
      expect(await runner.run((await jobs.claimDue('b', 60_000))!)).to.equal('completed');
      const index = ERASURE_STEPS.indexOf(failing);
      expect(ran).to.deep.equal([
        ...ERASURE_STEPS.slice(0, index + 1),
        ...ERASURE_STEPS.slice(index),
      ]);
      job = (await jobs.find('org-1', 'ses-1'))!;
      expect(job.status).to.equal('completed');
      expect(job.failures).to.equal(0);
    });
  }

  it('backs off from 5 s, doubling to 5 min, and never gives up', () => {
    expect([1, 2, 3, 7, 50].map(erasureBackoffMs)).to.deep.equal([
      5_000, 10_000, 20_000, 300_000, 300_000,
    ]);
  });

  it('ER-15: a holder whose job lease lapsed cannot move the job; the new holder finishes it', async () => {
    const clock = new Clock();
    const jobs = new InMemoryErasureJobStore(clock.now);
    let release: () => void = () => undefined;
    const slow = new Promise<void>((resolve) => {
      release = resolve;
    });
    const first = recordingSteps({
      artifacts: async () => {
        await slow;
        return { kind: 'done' };
      },
    });
    await jobs.enqueue(request);
    const a = new SessionErasureRunner({ jobs, erasers: first.erasers, conversations: NO_CONVERSATION });
    const running = a.run((await jobs.claimDue('a', 60_000))!);
    await new Promise((resolve) => setImmediate(resolve));
    clock.value += ERASURE_JOB_LEASE_MS + 1;
    const second = recordingSteps();
    const taken = (await jobs.claimDue('b', 60_000))!;
    expect(taken.attempt).to.equal(2);
    expect(taken.step).to.equal('artifacts');
    expect(await new SessionErasureRunner({ jobs, erasers: second.erasers, conversations: NO_CONVERSATION }).run(taken)).to.equal('completed');
    release();
    expect(await running).to.equal('lost');
    expect((await jobs.find('org-1', 'ses-1'))!.status).to.equal('completed');
  });

  it('a step that finds the job taken over inside its transaction reports lost, not a failure', async () => {
    const jobs = new InMemoryErasureJobStore(new Clock().now);
    const { erasers } = recordingSteps({
      cancel_runs: () => Promise.reject(new ErasureOwnershipLostError()),
    });
    await jobs.enqueue(request);
    expect(
      await new SessionErasureRunner({ jobs, erasers, conversations: NO_CONVERSATION }).run((await jobs.claimDue('a', 60_000))!),
    ).to.equal('lost');
    expect((await jobs.find('org-1', 'ses-1'))!.failures).to.equal(0);
  });

  it('the worker takes due jobs only while it holds the erasure lease', async () => {
    const jobs = new InMemoryErasureJobStore(new Clock().now);
    await jobs.enqueue(request);
    await jobs.enqueue({ ...request, sessionId: 'ses-2' });
    const { erasers } = recordingSteps();
    const runner = new SessionErasureRunner({ jobs, erasers, conversations: NO_CONVERSATION });
    const follower = new SessionErasureWorker({
      jobs,
      runner,
      lease: { tryAcquire: () => Promise.resolve(false), release: () => Promise.resolve() },
      holderId: 'b',
    });
    expect(await follower.tick()).to.equal(0);
    const leader = new SessionErasureWorker({ jobs, runner, lease: alwaysLeader, holderId: 'a' });
    expect(await leader.tick()).to.equal(2);
    expect((await jobs.find('org-1', 'ses-2'))!.status).to.equal('completed');
  });

  it('ER-19: log lines carry ids, steps and counts, never content', async () => {
    const jobs = new InMemoryErasureJobStore(new Clock().now);
    const logs = new Logs();
    let failed = false;
    const { erasers } = recordingSteps({
      conversation: () => {
        if (!failed) {
          failed = true;
          return Promise.reject(new Error(SENTINEL));
        }
        return Promise.resolve({ kind: 'done', counts: { messages: 2 } });
      },
    });
    await jobs.enqueue(request);
    const runner = new SessionErasureRunner({ jobs, erasers, conversations: NO_CONVERSATION, logger: logs });
    await runner.run((await jobs.claimDue('a', 60_000))!);
    await jobs.reschedule((await jobs.find('org-1', 'ses-1'))!, 0, null);
    await runner.run((await jobs.claimDue('a', 60_000))!);
    expect(logs.named('session_erasure.step')).to.have.length(ERASURE_STEPS.length);
    expect(logs.named('session_erasure.retry')[0]!.meta).to.include({
      orgId: 'org-1',
      sessionId: 'ses-1',
      step: 'conversation',
      errorType: 'Error',
    });
    expect(logs.named('session_erasure.completed')[0]!.meta).to.include({
      orgId: 'org-1',
      sessionId: 'ses-1',
      messages: 2,
    });
    expect(JSON.stringify(logs.lines)).to.not.include(SENTINEL);
  });
});

interface FakeRun {
  runId: string;
  status: RunStatus;
  revision: number;
  harnessMajor: 1 | 2;
}

function stepDeps(runs: FakeRun[]) {
  const controls: ControlRun[] = [];
  const shredded: string[] = [];
  const artifactPages: number[] = [];
  const liveBus = new InMemoryLiveBus();
  const journal = new InMemoryJournalStore();
  let remainingPages = 0;
  let controlError: Error | null = null;
  const deps: ErasureStepDependencies<unknown> = {
    runs: { runsOf: () => Promise.resolve(runs.map((run) => ({ ...run }))) },
    executions: {
      control: (command) => {
        if (controlError !== null) return Promise.reject(controlError);
        controls.push(command);
        return Promise.resolve({} as never);
      },
    },
    workspaceKeys: {
      shred: (orgId, sessionId) => {
        shredded.push(`${orgId}:${sessionId}`);
        return Promise.resolve(true);
      },
    },
    liveBus,
    artifacts: {
      eraseConversation: () => {
        artifactPages.push(remainingPages);
        remainingPages = Math.max(0, remainingPages - 1);
        return Promise.resolve({ artifacts: 2, blobs: 2, remaining: remainingPages > 0 });
      },
      collectDiscarded: () => Promise.resolve({ artifacts: 0, blobs: 0, remaining: false }),
    },
    stores: {
      eraseWorkspaces: () => Promise.resolve({ manifests: 3, heads: 2 }),
      erasePayloads: () => Promise.resolve({ payloads: 4 }),
      tombstoneJournal: () =>
        Promise.resolve({ events: 5, v1Events: 0, receipts: 1, admissionReceipts: 1, runInputs: 1 }),
    },
    conversations: { erase: () => Promise.resolve({ citations: 1, messages: 2, sessions: 1, attachments: 0, attachmentBlobs: 0 }) },
    journal: {
      store: journal,
      preparer: validatingPreparer,
      transaction: (work) => work(undefined),
    },
  };
  return {
    deps,
    controls,
    shredded,
    artifactPages,
    liveBus,
    journal,
    setRemaining: (pages: number) => {
      remainingPages = pages;
    },
    failControl: (error: Error | null) => {
      controlError = error;
    },
  };
}

const jobAt = (step: ErasureStep, overrides: Partial<ErasureJob> = {}): ErasureJob => ({
  ...request,
  status: 'pending',
  step,
  counts: {},
  attempt: 1,
  failures: 0,
  requestedAt: 0,
  nextAttemptAt: 0,
  completedAt: null,
  lastErrorType: null,
  ...overrides,
});

const eraserFor = (deps: ErasureStepDependencies<unknown>, step: ErasureStep) =>
  createErasureSteps(deps).find((eraser) => eraser.step === step)!;

const noContext = { completeIn: () => Promise.resolve(true) };

describe('E7B: the runner re-verifies the conversation before every step (BF-12)', () => {
  const lookup = (states: Array<'deleted' | 'absent' | 'live'>) => {
    const seen: string[] = [];
    return {
      seen,
      state: () => {
        const state = states.length > 1 ? states.shift()! : states[0]!;
        seen.push(state);
        return Promise.resolve(state);
      },
    };
  };

  it('reads the conversation before each step; deleted and absent run every step', async () => {
    const jobs = new InMemoryErasureJobStore(new Clock().now);
    const { ran, erasers } = recordingSteps();
    await jobs.enqueue(request);
    const guard = lookup(['deleted', 'deleted', 'absent']);
    expect(await new SessionErasureRunner({ jobs, erasers, conversations: guard }).run((await jobs.claimDue('a', 60_000))!)).to.equal('completed');
    expect(ran).to.deep.equal([...ERASURE_STEPS]);
    expect(guard.seen).to.have.length(ERASURE_STEPS.length);
  });

  it('a live conversation before the first step: the job is withdrawn and nothing runs', async () => {
    const jobs = new InMemoryErasureJobStore(new Clock().now);
    const { ran, erasers } = recordingSteps();
    await jobs.enqueue(request);
    const logs = new Logs();
    const runner = new SessionErasureRunner({ jobs, erasers, conversations: lookup(['live']), logger: logs });
    expect(await runner.run((await jobs.claimDue('a', 60_000))!)).to.equal('withdrawn');
    expect(ran).to.deep.equal([]);
    expect(await jobs.find('org-1', 'ses-1')).to.equal(null);
    expect(logs.lines.find((l) => l.message === 'session_erasure.withdrawn')).to.deep.include({ level: 'warn' });
    // A later delete enqueues afresh.
    expect(await jobs.enqueue(request)).to.equal(true);
  });

  it('live between steps (restored mid-erasure): the next step does not run', async () => {
    const jobs = new InMemoryErasureJobStore(new Clock().now);
    const { ran, erasers } = recordingSteps();
    await jobs.enqueue(request);
    const runner = new SessionErasureRunner({ jobs, erasers, conversations: lookup(['deleted', 'deleted', 'live']) });
    expect(await runner.run((await jobs.claimDue('a', 60_000))!)).to.equal('withdrawn');
    expect(ran).to.deep.equal(['cancel_runs', 'shred_workspace_key']);
  });

  it('a failing lookup is a retry, never an erasure', async () => {
    const jobs = new InMemoryErasureJobStore(new Clock().now);
    const { ran, erasers } = recordingSteps();
    await jobs.enqueue(request);
    const runner = new SessionErasureRunner({
      jobs,
      erasers,
      conversations: { state: () => Promise.reject(new Error('down')) },
    });
    expect(await runner.run((await jobs.claimDue('a', 60_000))!)).to.equal('retry');
    expect(ran).to.deep.equal([]);
  });
});

describe('E7 session erasure: the steps', () => {
  const runs = (): FakeRun[] => [
    { runId: 'r-done', status: 'completed', revision: 4, harnessMajor: 2 },
    { runId: 'r-queued', status: 'queued', revision: 1, harnessMajor: 2 },
    { runId: 'r-running', status: 'running', revision: 3, harnessMajor: 2 },
    { runId: 'r-stopping', status: 'cancelling', revision: 5, harnessMajor: 2 },
  ];

  it('ER-2: cancel_runs cancels each live run once, by the requester, with a replayable command id', async () => {
    const fake = stepDeps(runs());
    const outcome = await eraserFor(fake.deps, 'cancel_runs').erase(jobAt('cancel_runs'), noContext);
    expect(outcome).to.deep.equal({ kind: 'done', counts: { runs: 4, cancelled: 2 } });
    expect(fake.controls.map((c) => [c.runId, c.expectedRevision, c.by, c.commandId])).to.deep.equal([
      ['r-queued', 1, 'p_owner', 'erase:ses-1:r-queued'],
      ['r-running', 3, 'p_owner', 'erase:ses-1:r-running'],
    ]);
  });

  it('SS2 NR-9: cancel_runs drops the waiting next runs before it cancels anything', async () => {
    const fake = stepDeps(runs());
    const order: string[] = [];
    const control = fake.deps.executions.control;
    fake.deps.executions = {
      control: (command) => {
        order.push(`cancel:${command.runId}`);
        return control(command);
      },
    };
    fake.deps.nextRuns = {
      dropNextRuns: (orgId, sessionId) => {
        order.push(`drop:${orgId}:${sessionId}`);
        return Promise.resolve(2);
      },
    };
    const outcome = await eraserFor(fake.deps, 'cancel_runs').erase(jobAt('cancel_runs'), noContext);
    expect(outcome).to.deep.equal({ kind: 'done', counts: { runs: 4, cancelled: 2, nextRuns: 2 } });
    expect(order).to.deep.equal(['drop:org-1:ses-1', 'cancel:r-queued', 'cancel:r-running']);
  });

  it('ER-2: a retention erasure cancels as the retention principal, never as the run actor', async () => {
    const fake = stepDeps(runs());
    await eraserFor(fake.deps, 'cancel_runs').erase(
      jobAt('cancel_runs', { reason: 'retention', by: null }),
      noContext,
    );
    expect(fake.controls.every((c) => c.by === ERASURE_RETENTION_PRINCIPAL)).to.equal(true);
  });

  it('ER-2: a run that moved between read and cancel is read again shortly, not failed', async () => {
    const fake = stepDeps(runs());
    fake.failControl(new HarnessConflictError('REVISION_CONFLICT', 'moved'));
    expect(
      await eraserFor(fake.deps, 'cancel_runs').erase(jobAt('cancel_runs'), noContext),
    ).to.deep.include({ kind: 'wait', reason: 'run_moved' });
  });

  it('ER-3: the workspace key is shredded when the session ran, and left alone when it never did', async () => {
    const ran = stepDeps(runs());
    expect(
      await eraserFor(ran.deps, 'shred_workspace_key').erase(jobAt('shred_workspace_key'), noContext),
    ).to.deep.equal({ kind: 'done', counts: { workspaceKeys: 1 } });
    expect(ran.shredded).to.deep.equal(['org-1:ses-1']);
    const never = stepDeps([]);
    await eraserFor(never.deps, 'shred_workspace_key').erase(jobAt('shred_workspace_key'), noContext);
    expect(never.shredded).to.deep.equal([]);
  });

  it('ER-4: await_runs_terminal waits while any run is still stopping', async () => {
    const stopping = stepDeps(runs());
    expect(
      await eraserFor(stopping.deps, 'await_runs_terminal').erase(jobAt('await_runs_terminal'), noContext),
    ).to.deep.include({ kind: 'wait', reason: 'runs_stopping' });
    const ended = stepDeps(runs().map((run) => ({ ...run, status: 'cancelled' as RunStatus })));
    expect(
      await eraserFor(ended.deps, 'await_runs_terminal').erase(jobAt('await_runs_terminal'), noContext),
    ).to.deep.equal({ kind: 'done', counts: {} });
  });

  it("ER-5: the LiveBus streams of the session's runs go; another run's stays", async () => {
    const fake = stepDeps(runs());
    for (const runId of ['r-done', 'r-running', 'other-run']) {
      fake.liveBus.publish({ runId, fence: 1, modelAttempt: 0, n: 0, kind: 'text_delta', data: { text: SENTINEL } } as never);
    }
    expect(
      await eraserFor(fake.deps, 'live_bus').erase(jobAt('live_bus'), noContext),
    ).to.deep.equal({ kind: 'done', counts: { liveStreams: 2 } });
    expect((await fake.liveBus.read('r-running')).deltas).to.have.length(0);
    expect((await fake.liveBus.read('other-run')).deltas).to.have.length(1);
  });

  it('ER-6: artifacts are asked for page after page while the registry says more remain', async () => {
    const fake = stepDeps(runs());
    fake.setRemaining(3);
    expect(
      await eraserFor(fake.deps, 'artifacts').erase(jobAt('artifacts'), noContext),
    ).to.deep.equal({ kind: 'done', counts: { artifacts: 6, blobs: 6 } });
    expect(fake.artifactPages).to.deep.equal([3, 2, 1]);
  });

  it('ER-11: audit appends one session.erased with the counts, completing the job in its transaction', async () => {
    const fake = stepDeps(runs());
    await fake.journal.append(
      await validatingPreparer.prepare(
        sessionAppend('org-1', 'ses-1', [
          { type: 'session.participant_changed', schemaVersion: 1, critical: false, visibility: 'shared', data: { by: 'p_owner', subject: 'p_ann', role: 'viewer', prevRole: 'none' } },
        ]),
      ),
      undefined,
    );
    let completed = 0;
    const outcome = await eraserFor(fake.deps, 'audit').erase(
      jobAt('audit', { counts: { events: 1, messages: 2 } }),
      { completeIn: () => Promise.resolve(++completed === 1) },
    );
    expect(outcome).to.deep.equal({ kind: 'completed_in_transaction' });
    const stream = await fake.journal.readStream('org-1', 'ses-1', 0, 10);
    expect(stream.map((event) => event.type)).to.deep.equal([
      'session.participant_changed',
      'session.erased',
    ]);
    expect(stream[1]!.data).to.deep.equal({
      by: 'p_owner',
      reason: 'user_delete',
      counts: { events: 1, messages: 2 },
    });
    expect(stream[1]!.visibility).to.equal('audit');
    expect(stream[1]!.critical).to.equal(false);
  });

  it('ER-11: a session that never ran durably has no journal to extend', async () => {
    const fake = stepDeps([]);
    await eraserFor(fake.deps, 'audit').erase(jobAt('audit'), noContext);
    expect(await fake.journal.lastSeq('org-1', ['ses-1'])).to.deep.equal({ 'ses-1': 0 });
  });

  it('ER-15: audit refuses to complete a job another holder took over', async () => {
    const fake = stepDeps([]);
    let error: unknown;
    try {
      await eraserFor(fake.deps, 'audit').erase(jobAt('audit'), {
        completeIn: () => Promise.resolve(false),
      });
    } catch (caught) {
      error = caught;
    }
    expect(error).to.be.instanceOf(ErasureOwnershipLostError);
  });
});

describe('E7 typed answers after erasure (ER-13)', () => {
  afterEach(() => sinon.restore());

  it('a sealed-store read of a tombstoned event answers SessionErasedError, and logs nothing unreadable', async () => {
    const inner = new InMemoryJournalStore();
    const logs = new Logs();
    const store = new SealedJournalStore(inner, testCipher(), logs);
    await store.append(
      await validatingPreparer.prepare(
        sessionAppend('org-1', 'ses-1', [
          {
            type: 'session.participant_changed',
            schemaVersion: 1,
            critical: false,
            visibility: 'shared',
            data: { by: 'p_owner', subject: 'p_ann', role: 'viewer', prevRole: 'none' },
          },
        ]),
      ),
      undefined,
    );
    const [stored] = await inner.readStream('org-1', 'ses-1', 0, 1);
    const tombstone = without({ ...stored!, erased: true, contentBytes: 10 }, 'sealed');
    sinon.stub(inner, 'readStream').resolves([tombstone]);
    let error: unknown;
    try {
      await store.readStream('org-1', 'ses-1', 0, 10);
    } catch (caught) {
      error = caught;
    }
    expect(error).to.be.instanceOf(SessionErasedError);
    expect(logs.named('journal.content_unreadable')).to.deep.equal([]);
  });

  it('a run GET of an erased session answers 410 SESSION_ERASED; of an unknown one, 404', async () => {
    const jobs = new InMemoryErasureJobStore();
    await jobs.enqueue(request);
    const service = new SessionRunService(
      {} as never,
      {
        findExecutionBinding: () => Promise.resolve(null),
        findStreamAccess: () => Promise.resolve(null),
      } as never,
      jobs,
    );
    const answer = async (sessionId: string) => {
      try {
        await service.get({ orgId: 'org-1', actorId: 'u', sessionId }, 'run-1');
      } catch (error) {
        return transportError(error) as { statusCode?: number; code?: string };
      }
      return null;
    };
    expect(await answer('ses-1')).to.include({
      statusCode: 410,
      code: 'HTTP_AGENT_HARNESS_SESSION_ERASED',
    });
    expect((await answer('ses-unknown'))?.statusCode).to.equal(404);
  });
});

describe("WP114-FU-ERASE: the conversation step erases the chat's coding-engine choices", () => {
  const conversationStep = (deps: Record<string, unknown>) =>
    createErasureSteps(deps as never).find((eraser) => eraser.step === 'conversation')!;
  const erased = {
    citations: 0,
    messages: 1,
    sessions: 1,
    attachments: 0,
    attachmentBlobs: 0,
  };
  const job = { ...request, status: 'pending', step: 'conversation' } as unknown as ErasureJob;

  it('erases the choices before the conversation and counts them', async () => {
    const order: string[] = [];
    const step = conversationStep({
      conversations: {
        erase: (orgId: string, sessionId: string) => {
          order.push(`conversation:${orgId}/${sessionId}`);
          return Promise.resolve(erased);
        },
      },
      engineChoices: {
        eraseSession: (orgId: string, sessionId: string) => {
          order.push(`choices:${orgId}/${sessionId}`);
          return Promise.resolve(1);
        },
      },
    });
    expect(await step.erase(job, {} as never)).to.deep.equal({
      kind: 'done',
      counts: { ...erased, engineChoices: 1 },
    });
    expect(order).to.deep.equal(['choices:org-1/ses-1', 'conversation:org-1/ses-1']);
  });

  it('without coding engines the step counts the conversation only', async () => {
    const step = conversationStep({
      conversations: { erase: () => Promise.resolve(erased) },
      engineChoices: null,
    });
    expect(await step.erase(job, {} as never)).to.deep.equal({ kind: 'done', counts: erased });
  });
});
