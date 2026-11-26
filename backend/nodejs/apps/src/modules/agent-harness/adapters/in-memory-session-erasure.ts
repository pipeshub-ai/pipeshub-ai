/* eslint-disable @typescript-eslint/require-await -- This deterministic adapter implements the asynchronous store port without I/O. */
import {
  addCounts,
  assertErasureAdvance,
  ERASURE_STEPS,
  ErasureCounts,
  ErasureJob,
  ErasureRequest,
  ErasureStep,
  erasureJobId,
  requestsOrg,
} from '../domain/session-erasure';
import { ErasureJobStore } from '../ports/session-erasure';

interface Held {
  job: ErasureJob;
  holder: string | null;
  leaseExpiresAt: number | null;
}

/** Erasure jobs in memory, on an injected clock: tests and single-process runs. */
export class InMemoryErasureJobStore implements ErasureJobStore<unknown> {
  private readonly jobs = new Map<string, Held>();

  constructor(private readonly clock: () => number = Date.now) {}

  async enqueue(request: ErasureRequest): Promise<boolean> {
    const id = erasureJobId(request.orgId, request.sessionId);
    if (this.jobs.has(id)) return false;
    const now = this.clock();
    this.jobs.set(id, {
      job: {
        ...request,
        status: 'pending',
        step: ERASURE_STEPS[0],
        counts: {},
        attempt: 0,
        failures: 0,
        requestedAt: now,
        nextAttemptAt: now,
        completedAt: null,
        lastErrorType: null,
      },
      holder: null,
      leaseExpiresAt: null,
    });
    return true;
  }

  async enqueueMany(requests: readonly ErasureRequest[]): Promise<number> {
    requestsOrg(requests);
    let created = 0;
    for (const request of requests) {
      if (await this.enqueue(request)) created += 1;
    }
    return created;
  }

  async find(orgId: string, sessionId: string): Promise<ErasureJob | null> {
    const held = this.jobs.get(erasureJobId(orgId, sessionId));
    return held === undefined ? null : structuredClone(held.job);
  }

  async claimDue(holder: string, leaseMs: number): Promise<ErasureJob | null> {
    const now = this.clock();
    const due = [...this.jobs.values()]
      .filter(
        (held) =>
          held.job.status === 'pending' &&
          held.job.nextAttemptAt <= now &&
          (held.leaseExpiresAt === null || held.leaseExpiresAt <= now),
      )
      .sort((a, b) => a.job.nextAttemptAt - b.job.nextAttemptAt)[0];
    if (due === undefined) return null;
    due.job = { ...due.job, attempt: due.job.attempt + 1 };
    due.holder = holder;
    due.leaseExpiresAt = now + leaseMs;
    return structuredClone(due.job);
  }

  async advance(
    job: ErasureJob,
    counts: ErasureCounts,
    next: ErasureStep | null,
    leaseMs: number,
  ): Promise<boolean> {
    assertErasureAdvance(job, next);
    const held = this.current(job);
    if (held === null) return false;
    const now = this.clock();
    held.job = {
      ...held.job,
      step: next,
      status: next === null ? 'completed' : 'pending',
      counts: addCounts(held.job.counts, counts),
      failures: 0,
      lastErrorType: null,
      completedAt: next === null ? now : null,
    };
    held.leaseExpiresAt = next === null ? null : now + leaseMs;
    return true;
  }

  async reschedule(
    job: ErasureJob,
    at: number,
    failure: { errorType: string } | null,
  ): Promise<boolean> {
    const held = this.current(job);
    if (held === null) return false;
    held.job = {
      ...held.job,
      nextAttemptAt: at,
      failures: failure === null ? held.job.failures : held.job.failures + 1,
      lastErrorType: failure?.errorType ?? held.job.lastErrorType,
    };
    held.holder = null;
    held.leaseExpiresAt = null;
    return true;
  }

  async withdraw(job: ErasureJob): Promise<boolean> {
    if (this.current(job) === null) return false;
    return this.jobs.delete(erasureJobId(job.orgId, job.sessionId));
  }

  async findMany(
    orgId: string,
    sessionIds: readonly string[],
  ): Promise<Map<string, ErasureJob>> {
    const found = new Map<string, ErasureJob>();
    for (const sessionId of sessionIds) {
      const held = this.jobs.get(erasureJobId(orgId, sessionId));
      if (held !== undefined) found.set(sessionId, structuredClone(held.job));
    }
    return found;
  }

  async now(): Promise<number> {
    return this.clock();
  }

  private current(job: ErasureJob): Held | null {
    const held = this.jobs.get(erasureJobId(job.orgId, job.sessionId));
    return held?.job.status === 'pending' &&
      held.job.attempt === job.attempt &&
      held.job.step === job.step
      ? held
      : null;
  }
}
