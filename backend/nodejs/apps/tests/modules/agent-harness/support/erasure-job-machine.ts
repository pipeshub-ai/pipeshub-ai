/**
 * QA13-11: a session's erasure job as a state machine. Two holders claim due
 * jobs, advance them a step (or try to skip or repeat one), reschedule them
 * after a wait or a failure, and withdraw them, with holders crashing (their
 * lease lapsing) at any point. After every operation the store must hold
 * what the pure model says: one job per session, steps only in
 * `ERASURE_STEPS` order, a completed job never moves, a holder whose attempt
 * was superseded moves nothing, and a lapsed lease lets the other holder in.
 */
import { expect } from 'chai';
import fc from 'fast-check';
import { HarnessConflictError } from '../../../../src/modules/agent-harness/domain/errors';
import {
  ERASURE_STEPS,
  type ErasureJob,
  type ErasureStep,
  nextErasureStep,
} from '../../../../src/modules/agent-harness/domain/session-erasure';
import type { ErasureJobStore } from '../../../../src/modules/agent-harness/ports/session-erasure';
import { propertyRuns } from '../../../support/harness/property-profile';

const ORG = 'org-qa13';
const LEASE_MS = 120_000;
const SESSIONS = ['ses-a', 'ses-b', 'ses-c'];
const HOLDERS = ['holder-1', 'holder-2'] as const;

type Holder = (typeof HOLDERS)[number];
type Advance = 'next' | 'skip' | 'repeat' | 'finish';

type Op =
  | { kind: 'enqueue'; session: number }
  | { kind: 'claim'; holder: Holder }
  | { kind: 'advance'; holder: Holder; to: Advance }
  | { kind: 'reschedule'; holder: Holder; failed: boolean; afterMs: number }
  | { kind: 'withdraw'; holder: Holder }
  | { kind: 'advance_clock'; ms: number };

const holder = fc.constantFrom(...HOLDERS);
const opArbitrary: fc.Arbitrary<Op> = fc.oneof(
  { weight: 1, arbitrary: fc.record({ kind: fc.constant('enqueue' as const), session: fc.nat(2) }) },
  { weight: 2, arbitrary: fc.record({ kind: fc.constant('claim' as const), holder }) },
  {
    weight: 4,
    arbitrary: fc.record({
      kind: fc.constant('advance' as const),
      holder,
      to: fc.oneof(
        { weight: 6, arbitrary: fc.constant<Advance>('next') },
        { weight: 1, arbitrary: fc.constantFrom<Advance>('skip', 'repeat', 'finish') },
      ),
    }),
  },
  {
    weight: 1,
    arbitrary: fc.record({
      kind: fc.constant('reschedule' as const),
      holder,
      failed: fc.boolean(),
      afterMs: fc.constantFrom(0, 5_000, 60_000),
    }),
  },
  { weight: 1, arbitrary: fc.record({ kind: fc.constant('withdraw' as const), holder }) },
  { weight: 1, arbitrary: fc.record({ kind: fc.constant('advance_clock' as const), ms: fc.constantFrom(1_000, 60_000, LEASE_MS) }) },
);

interface ModelJob {
  status: 'pending' | 'completed';
  step: ErasureStep | null;
  attempt: number;
  failures: number;
  nextAttemptAt: number;
  lease: number | null;
}

const target = (step: ErasureStep, to: Advance): ErasureStep | null => {
  const index = ERASURE_STEPS.indexOf(step);
  switch (to) {
    case 'next':
      return nextErasureStep(step);
    case 'skip':
      return ERASURE_STEPS[index + 2] ?? null;
    case 'repeat':
      return step;
    case 'finish':
      return null;
  }
};

class ErasureMachine {
  readonly jobs = new Map<string, ModelJob>();
  readonly held = new Map<Holder, ErasureJob>();

  constructor(
    readonly store: ErasureJobStore<unknown>,
    readonly clock: { now: number },
  ) {}

  /** The holder's copy is the store's job at its claim, as the runner keeps it. */
  private current(copy: ErasureJob): ModelJob | null {
    const job = this.jobs.get(copy.sessionId);
    return job !== undefined && job.status === 'pending' && job.attempt === copy.attempt && job.step === copy.step
      ? job
      : null;
  }

  async apply(op: Op): Promise<void> {
    const now = this.clock.now;
    switch (op.kind) {
      case 'enqueue': {
        const sessionId = SESSIONS[op.session]!;
        const created = await this.store.enqueue({ orgId: ORG, sessionId, reason: 'user_delete', by: 'p_user' });
        expect(created, `one job for ${sessionId}`).to.equal(!this.jobs.has(sessionId));
        if (created) {
          this.jobs.set(sessionId, {
            status: 'pending',
            step: ERASURE_STEPS[0],
            attempt: 0,
            failures: 0,
            nextAttemptAt: now,
            lease: null,
          });
        }
        return;
      }
      case 'claim': {
        const due = [...this.jobs.entries()].filter(
          ([, job]) => job.status === 'pending' && job.nextAttemptAt <= now && (job.lease === null || job.lease <= now),
        );
        const claimed = await this.store.claimDue(op.holder, LEASE_MS);
        if (due.length === 0) {
          expect(claimed, 'nothing is due').to.equal(null);
          return;
        }
        expect(claimed, 'a due job is claimed').not.to.equal(null);
        const earliest = Math.min(...due.map(([, job]) => job.nextAttemptAt));
        const job = this.jobs.get(claimed!.sessionId)!;
        expect(due.map(([sessionId]) => sessionId), 'the claimed job was due').to.include(claimed!.sessionId);
        expect(job.nextAttemptAt, 'oldest first').to.equal(earliest);
        job.attempt += 1;
        job.lease = now + LEASE_MS;
        this.held.set(op.holder, claimed!);
        return;
      }
      case 'advance': {
        const copy = this.held.get(op.holder);
        if (copy === undefined || copy.step === null) return;
        const next = target(copy.step, op.to);
        let moved: boolean | 'refused';
        try {
          moved = await this.store.advance(copy, { [copy.step]: 1 }, next, LEASE_MS);
        } catch (error) {
          expect(error, 'only a refusal').to.be.instanceOf(HarnessConflictError);
          expect((error as HarnessConflictError).code).to.equal('INVALID_TRANSITION');
          moved = 'refused';
        }
        const job = this.current(copy);
        if (op.to !== 'next') {
          expect(moved, `${copy.step} -> ${String(next)} is not the next step`).to.equal('refused');
          return;
        }
        expect(moved, `advance ${copy.step} at attempt ${String(copy.attempt)}`).to.equal(job !== null);
        if (job === null) return;
        job.step = next;
        job.status = next === null ? 'completed' : 'pending';
        job.failures = 0;
        job.lease = next === null ? null : now + LEASE_MS;
        this.held.set(op.holder, { ...copy, step: next, status: job.status });
        return;
      }
      case 'reschedule': {
        const copy = this.held.get(op.holder);
        if (copy === undefined) return;
        const moved = await this.store.reschedule(copy, now + op.afterMs, op.failed ? { errorType: 'Error' } : null);
        const job = this.current(copy);
        expect(moved).to.equal(job !== null);
        if (job === null) return;
        job.nextAttemptAt = now + op.afterMs;
        job.lease = null;
        if (op.failed) job.failures += 1;
        return;
      }
      case 'withdraw': {
        const copy = this.held.get(op.holder);
        if (copy === undefined) return;
        const withdrawn = await this.store.withdraw(copy);
        expect(withdrawn).to.equal(this.current(copy) !== null);
        if (withdrawn) this.jobs.delete(copy.sessionId);
        return;
      }
      case 'advance_clock':
        this.clock.now += op.ms;
        return;
      default:
        return;
    }
  }

  async agree(): Promise<void> {
    for (const sessionId of SESSIONS) {
      const stored = await this.store.find(ORG, sessionId);
      const job = this.jobs.get(sessionId);
      expect(
        stored === null
          ? null
          : [stored.status, stored.step, stored.attempt, stored.failures, stored.nextAttemptAt, stored.completedAt !== null],
        sessionId,
      ).to.deep.equal(
        job === undefined
          ? null
          : [job.status, job.step, job.attempt, job.failures, job.nextAttemptAt, job.status === 'completed'],
      );
    }
  }
}

/** The machine against `make()`'s job store, on the clock it is given. */
export function describeErasureJobMachine(
  name: string,
  make: (clock: { now: number }) => Promise<ErasureJobStore<unknown>>,
  runs: { ci: number; nightly: number } = { ci: 150, nightly: 5_000 },
): void {
  describe(`QA13-11 erasure jobs as a state machine: ${name}`, function () {
    this.timeout(1_800_000);

    it('every operation leaves the store as the model says', async () => {
      await fc.assert(
        fc.asyncProperty(fc.array(opArbitrary, { maxLength: 60, size: 'large' }), async (ops) => {
          const clock = { now: 1_000_000 };
          const machine = new ErasureMachine(await make(clock), clock);
          for (const op of ops) {
            await machine.apply(op);
            await machine.agree();
          }
        }),
        propertyRuns(runs.ci, runs.nightly),
      );
    });
  });
}
