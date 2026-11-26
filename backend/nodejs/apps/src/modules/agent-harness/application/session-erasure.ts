import {
  addCounts,
  ERASURE_ALERT_AFTER_FAILURES,
  ERASURE_JOB_LEASE_MS,
  ERASURE_JOBS_PER_TICK,
  ERASURE_LEASE_MS,
  ERASURE_LEASE_NAME,
  ERASURE_STEPS,
  ERASURE_TICK_MS,
  ErasureCounts,
  ErasureJob,
  ErasureStep,
  erasureBackoffMs,
  nextErasureStep,
} from '../domain/session-erasure';
import { LeaderLease } from '../ports/leader-lease';
import {
  ConversationStateLookup,
  ErasureJobStore,
  ErasurePass,
  SessionDataEraser,
} from '../ports/session-erasure';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';
import {
  detachedInterval,
  forEnumeratedOrg,
} from '../../../libs/context/orgContext';

/** A step found the job taken over by another holder while it ran. */
export class ErasureOwnershipLostError extends Error {
  constructor() {
    super('The erasure job is held by another owner');
    this.name = 'ErasureOwnershipLostError';
  }
}

export type ErasureRunResult =
  'completed' | 'waiting' | 'retry' | 'lost' | 'withdrawn';

const errorType = (error: unknown): string =>
  error instanceof Error ? error.name : typeof error;

export interface SessionErasureRunnerOptions<Tx> {
  jobs: ErasureJobStore<Tx>;
  erasers: readonly SessionDataEraser<Tx>[];
  conversations: ConversationStateLookup;
  logger?: HarnessLogger;
  leaseMs?: number;
}

/**
 * Runs one claimed job's steps in order until it completes, waits, fails or is
 * lost. Before every step it reads the conversation: a live one (restored, or
 * never deleted) withdraws the job, so nothing more of it is erased (E7B).
 */
export class SessionErasureRunner<Tx> {
  private readonly steps: ReadonlyMap<ErasureStep, SessionDataEraser<Tx>>;
  private readonly jobs: ErasureJobStore<Tx>;
  private readonly conversations: ConversationStateLookup;
  private readonly logger: HarnessLogger;
  private readonly leaseMs: number;

  constructor(options: SessionErasureRunnerOptions<Tx>) {
    this.jobs = options.jobs;
    this.conversations = options.conversations;
    this.logger = options.logger ?? silentHarnessLogger;
    this.leaseMs = options.leaseMs ?? ERASURE_JOB_LEASE_MS;
    const { erasers } = options;
    const steps = new Map(erasers.map((eraser) => [eraser.step, eraser]));
    const missing = ERASURE_STEPS.filter((step) => !steps.has(step));
    if (missing.length > 0 || steps.size !== erasers.length) {
      throw new Error(
        `Erasure needs exactly one eraser per step; missing: ${missing.length > 0 ? missing.join(', ') : 'none'}`,
      );
    }
    this.steps = steps;
  }

  async run(claimed: ErasureJob): Promise<ErasureRunResult> {
    let job = claimed;
    const pass: ErasurePass = {};
    while (job.step !== null) {
      const step = job.step;
      const next = nextErasureStep(step);
      const current = job;
      let outcome: Awaited<ReturnType<SessionDataEraser<Tx>['erase']>>;
      try {
        if (
          (await this.conversations.state(job.orgId, job.sessionId)) === 'live'
        ) {
          return await this.withdrawn(job);
        }
        outcome = await this.eraser(step).erase(current, {
          completeIn: (tx, counts) =>
            this.jobs.advance(current, counts, next, this.leaseMs, tx),
          pass,
        });
      } catch (error) {
        if (error instanceof ErasureOwnershipLostError) return this.lost(job);
        return this.failed(job, error);
      }
      let counts: ErasureCounts = {};
      if (outcome.kind === 'wait') {
        await this.jobs.reschedule(
          job,
          (await this.jobs.now()) + outcome.retryAfterMs,
          null,
        );
        this.logger.debug(
          'session_erasure.waiting',
          harnessLogMeta('session_erasure.waiting', {
            ...this.ids(job),
            step,
            reason: outcome.reason,
            retryAfterMs: outcome.retryAfterMs,
          }),
        );
        return 'waiting';
      }
      if (outcome.kind === 'done') {
        counts = outcome.counts ?? {};
        if (!(await this.jobs.advance(job, counts, next, this.leaseMs))) {
          return this.lost(job);
        }
      }
      this.logger.info(
        'session_erasure.step',
        harnessLogMeta('session_erasure.step', {
          ...this.ids(job),
          step,
          attempt: job.attempt,
          ...counts,
        }),
      );
      job = {
        ...job,
        step: next,
        counts: addCounts(job.counts, counts),
        failures: 0,
        lastErrorType: null,
      };
    }
    this.logger.info(
      'session_erasure.completed',
      harnessLogMeta('session_erasure.completed', {
        ...this.ids(job),
        reason: job.reason,
        ...job.counts,
      }),
    );
    return 'completed';
  }

  private eraser(step: ErasureStep): SessionDataEraser<Tx> {
    const eraser = this.steps.get(step);
    if (eraser === undefined) throw new Error(`No eraser for ${step}`);
    return eraser;
  }

  private async failed(job: ErasureJob, error: unknown): Promise<'retry'> {
    const failures = job.failures + 1;
    const fields = harnessLogMeta('session_erasure.retry', {
      ...this.ids(job),
      step: job.step,
      failures,
      errorType: errorType(error),
      retryInMs: erasureBackoffMs(failures),
    });
    if (failures >= ERASURE_ALERT_AFTER_FAILURES) {
      this.logger.error('session_erasure.retry', fields);
    } else {
      this.logger.warn('session_erasure.retry', fields);
    }
    await this.jobs.reschedule(
      job,
      (await this.jobs.now()) + erasureBackoffMs(failures),
      { errorType: errorType(error) },
    );
    return 'retry';
  }

  private async withdrawn(job: ErasureJob): Promise<'withdrawn' | 'lost'> {
    if (!(await this.jobs.withdraw(job))) return this.lost(job);
    this.logger.warn(
      'session_erasure.withdrawn',
      harnessLogMeta('session_erasure.withdrawn', {
        ...this.ids(job),
        step: job.step,
        reason: 'conversation_live',
      }),
    );
    return 'withdrawn';
  }

  private lost(job: ErasureJob): 'lost' {
    this.logger.warn(
      'session_erasure.lost',
      harnessLogMeta('session_erasure.lost', {
        ...this.ids(job),
        step: job.step,
        attempt: job.attempt,
      }),
    );
    return 'lost';
  }

  private ids(job: ErasureJob): { orgId: string; sessionId: string } {
    return { orgId: job.orgId, sessionId: job.sessionId };
  }
}

export interface SessionErasureWorkerOptions<Tx> {
  jobs: ErasureJobStore<Tx>;
  runner: SessionErasureRunner<Tx>;
  lease: LeaderLease;
  holderId: string;
  logger?: HarnessLogger;
  jobLeaseMs?: number;
}

/**
 * Takes due erasure jobs one at a time on the replica that holds the
 * erasure lease (the sweeper's election). The per-job lease and fence make a
 * replica that lost the election mid-job harmless.
 */
export class SessionErasureWorker<Tx> {
  private timer: NodeJS.Timeout | null = null;
  private running = false;

  private readonly jobs: ErasureJobStore<Tx>;
  private readonly runner: SessionErasureRunner<Tx>;
  private readonly lease: LeaderLease;
  private readonly holderId: string;
  private readonly logger: HarnessLogger;
  private readonly jobLeaseMs: number;

  constructor(options: SessionErasureWorkerOptions<Tx>) {
    this.jobs = options.jobs;
    this.runner = options.runner;
    this.lease = options.lease;
    this.holderId = options.holderId;
    this.logger = options.logger ?? silentHarnessLogger;
    this.jobLeaseMs = options.jobLeaseMs ?? ERASURE_JOB_LEASE_MS;
  }

  start(intervalMs = ERASURE_TICK_MS): void {
    if (this.timer) return;
    this.timer = detachedInterval(() => {
      void this.tick();
    }, intervalMs);
  }

  async stop(): Promise<void> {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    while (this.running) {
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    try {
      await this.lease.release(ERASURE_LEASE_NAME, this.holderId);
    } catch (error) {
      this.logger.warn(
        'session_erasure.lease_release_failed',
        harnessLogMeta('session_erasure.lease_release_failed', {
          errorType: errorType(error),
        }),
      );
    }
  }

  /** Runs up to a batch of due jobs; returns how many it took. */
  async tick(limit = ERASURE_JOBS_PER_TICK): Promise<number> {
    if (this.running) return 0;
    this.running = true;
    let taken = 0;
    try {
      if (
        !(await this.lease.tryAcquire(
          ERASURE_LEASE_NAME,
          this.holderId,
          ERASURE_LEASE_MS,
        ))
      ) {
        return 0;
      }
      while (taken < limit) {
        const job = await this.jobs.claimDue(this.holderId, this.jobLeaseMs);
        if (job === null) break;
        taken += 1;
        // The claimed job row names the org it erases from; the job runs as that org.
        await forEnumeratedOrg(job.orgId, 'agent-harness.session-erasure', () =>
          this.runner.run(job),
        );
      }
      return taken;
    } catch (error) {
      this.logger.error(
        'session_erasure.tick_failed',
        harnessLogMeta('session_erasure.tick_failed', {
          errorType: errorType(error),
        }),
      );
      return taken;
    } finally {
      this.running = false;
    }
  }
}
