/**
 * What every `ErasureJobStore` must do (E7 ER-1, ER-15, ER-18, BF-18; QA-15's JS-1..JS-5 folded in): the
 * durable, leased and fenced session-erasure jobs. `InMemoryErasureJobStore`
 * (`erasure-job-store.test.ts`) and `MongoErasureJobStore`
 * (`erasure-job-store.mongo.itest.ts`). The suite owns the store's clock.
 */
import { expect } from 'chai';
import {
  ERASURE_STEPS,
  ErasureRequest,
} from '../../../../src/modules/agent-harness/domain/session-erasure';
import { StoreClock } from '../../../../src/modules/agent-harness/domain/execution';
import { ErasureJobStore } from '../../../../src/modules/agent-harness/ports/session-erasure';
import { HarnessConflictError } from '../../../../src/modules/agent-harness/domain/errors';

/** Org ids in the shape every backend takes (PostgreSQL's org tables check it). */
const ORG_1 = 'a1a1a1a1a1a1a1a1a1a1a1a1';
const ORG_2 = 'b2b2b2b2b2b2b2b2b2b2b2b2';
const T0 = 1_000_000;
const LEASE = 1_000;

const request = (overrides: Partial<ErasureRequest> = {}): ErasureRequest => ({
  orgId: ORG_1,
  sessionId: 'ses-1',
  reason: 'user_delete',
  by: 'p_owner',
  ...overrides,
});

export function describeErasureJobStoreContract(
  name: string,
  create: (clock: StoreClock) => Promise<ErasureJobStore<unknown>>,
): void {
  describe(`ErasureJobStore contract: ${name}`, () => {
    let now = T0;
    let jobs: ErasureJobStore<unknown>;

    beforeEach(async () => {
      now = T0;
      jobs = await create({ now: () => now });
    });

    it('EJ-1: a delete enqueues one pending job at the first step, due at once; asking again keeps it', async () => {
      expect(await jobs.enqueue(request())).to.equal(true);
      now += 50;
      expect(await jobs.enqueue(request({ reason: 'retention', by: null }))).to.equal(false);
      expect(await jobs.find(ORG_1, 'ses-1')).to.deep.equal({
        ...request(),
        status: 'pending',
        step: ERASURE_STEPS[0],
        counts: {},
        attempt: 0,
        failures: 0,
        requestedAt: T0,
        nextAttemptAt: T0,
        completedAt: null,
        lastErrorType: null,
      });
    });

    it('EJ-16 (QAFINAL-FU-VOL): enqueueMany is enqueue of each, in one write: an existing job is kept, a mixed-org batch refused', async () => {
      await jobs.enqueue(request({ sessionId: 'ses-1', reason: 'retention', by: null }));
      now += 20;
      expect(await jobs.enqueueMany([])).to.equal(0);
      expect(
        await jobs.enqueueMany(['ses-1', 'ses-2', 'ses-3', 'ses-2'].map((sessionId) => request({ sessionId }))),
      ).to.equal(2);
      expect(await jobs.find(ORG_1, 'ses-1')).to.include({ reason: 'retention', by: null, requestedAt: T0 });
      for (const sessionId of ['ses-2', 'ses-3']) {
        expect(await jobs.find(ORG_1, sessionId)).to.include({
          reason: 'user_delete',
          by: 'p_owner',
          status: 'pending',
          step: ERASURE_STEPS[0],
          attempt: 0,
          requestedAt: T0 + 20,
          nextAttemptAt: T0 + 20,
        });
      }
      let refused: unknown = null;
      try {
        await jobs.enqueueMany([request({ sessionId: 'ses-4' }), request({ orgId: ORG_2, sessionId: 'ses-5' })]);
      } catch (error) {
        refused = error;
      }
      expect(refused).to.be.instanceOf(Error);
      expect(await jobs.find(ORG_1, 'ses-4')).to.equal(null);
    });

    it('EJ-2: a session never erased has no job; the same session id in another org is its own job', async () => {
      expect(await jobs.find(ORG_1, 'ses-1')).to.equal(null);
      await jobs.enqueue(request());
      expect(await jobs.find(ORG_2, 'ses-1')).to.equal(null);
      expect(await jobs.enqueue(request({ orgId: ORG_2, by: null, reason: 'retention' }))).to.equal(true);
      expect((await jobs.find(ORG_2, 'ses-1'))?.reason).to.equal('retention');
      expect((await jobs.find(ORG_1, 'ses-1'))?.reason).to.equal('user_delete');
    });

    it('EJ-3: claimDue takes the oldest due job, bumps its attempt and holds it under the lease', async () => {
      await jobs.enqueue(request({ sessionId: 'ses-old' }));
      now += 10;
      await jobs.enqueue(request({ sessionId: 'ses-new' }));
      const first = await jobs.claimDue('a', LEASE);
      expect(first).to.include({ sessionId: 'ses-old', attempt: 1, status: 'pending' });
      const second = await jobs.claimDue('b', LEASE);
      expect(second).to.include({ sessionId: 'ses-new', attempt: 1 });
      expect(await jobs.claimDue('c', LEASE)).to.equal(null);
    });

    it('EJ-4: nothing is due before its time; the job is claimable from that time on', async () => {
      expect(await jobs.claimDue('a', LEASE)).to.equal(null);
      await jobs.enqueue(request());
      const held = (await jobs.claimDue('a', LEASE))!;
      expect(await jobs.reschedule(held, T0 + 5_000, null)).to.equal(true);
      now = T0 + 4_999;
      expect(await jobs.claimDue('a', LEASE)).to.equal(null);
      now = T0 + 5_000;
      expect((await jobs.claimDue('a', LEASE))?.attempt).to.equal(2);
    });

    it('EJ-5: a holder whose lease lapsed can no longer advance, reschedule or withdraw; the next holder can', async () => {
      await jobs.enqueue(request());
      const stale = (await jobs.claimDue('a', LEASE))!;
      now += LEASE;
      const current = (await jobs.claimDue('b', LEASE))!;
      expect(current.attempt).to.equal(stale.attempt + 1);
      expect(await jobs.advance(stale, { x: 1 }, 'shred_workspace_key', LEASE)).to.equal(false);
      expect(await jobs.reschedule(stale, now, { errorType: 'Error' })).to.equal(false);
      expect(await jobs.withdraw(stale)).to.equal(false);
      expect(await jobs.find(ORG_1, 'ses-1')).to.deep.include({ step: ERASURE_STEPS[0], failures: 0, counts: {} });
      expect(await jobs.advance(current, { x: 1 }, 'shred_workspace_key', LEASE)).to.equal(true);
    });

    it('EJ-6: advance adds the counts, moves to the next step, clears the failure and keeps the job held', async () => {
      await jobs.enqueue(request());
      const held = (await jobs.claimDue('a', LEASE))!;
      await jobs.reschedule(held, now, { errorType: 'MongoNetworkError' });
      const retried = (await jobs.claimDue('a', LEASE))!;
      expect(await jobs.advance(retried, { runs: 2 }, 'shred_workspace_key', LEASE)).to.equal(true);
      expect(await jobs.find(ORG_1, 'ses-1')).to.deep.include({
        status: 'pending',
        step: 'shred_workspace_key',
        counts: { runs: 2 },
        attempt: 2,
        failures: 0,
        lastErrorType: null,
        completedAt: null,
      });
      expect(await jobs.claimDue('b', LEASE)).to.equal(null);
      const next = { ...retried, step: 'shred_workspace_key' as const, counts: { runs: 2 } };
      expect(await jobs.advance(next, { runs: 1, keys: 1 }, 'await_runs_terminal', LEASE)).to.equal(true);
      expect((await jobs.find(ORG_1, 'ses-1'))?.counts).to.deep.equal({ runs: 3, keys: 1 });
    });

    it('EJ-7: advancing past the last step completes the job, which stays found and is never claimed again', async () => {
      await jobs.enqueue(request());
      let held = (await jobs.claimDue('a', LEASE))!;
      // A job moves only one step at a time (QA13-F3): walk to the last step, then complete it.
      for (const step of ERASURE_STEPS.slice(1)) {
        expect(await jobs.advance(held, {}, step, LEASE)).to.equal(true);
        held = { ...held, step };
      }
      now += 7;
      expect(await jobs.advance(held, { events: 4 }, null, LEASE)).to.equal(true);
      expect(await jobs.find(ORG_1, 'ses-1')).to.deep.include({
        status: 'completed',
        step: null,
        completedAt: T0 + 7,
        counts: { events: 4 },
      });
      now += LEASE * 10;
      expect(await jobs.claimDue('a', LEASE)).to.equal(null);
      expect(await jobs.enqueue(request())).to.equal(false);
      expect(await jobs.withdraw({ ...held, step: null, status: 'completed' })).to.equal(false);
    });

    it('EJ-8: an advance or reschedule naming another step or attempt is refused and changes nothing', async () => {
      await jobs.enqueue(request());
      const held = (await jobs.claimDue('a', LEASE))!;
      const before = await jobs.find(ORG_1, 'ses-1');
      expect(await jobs.advance({ ...held, step: 'journal' }, { x: 1 }, 'conversation', LEASE)).to.equal(false);
      expect(await jobs.advance({ ...held, attempt: held.attempt + 1 }, { x: 1 }, ERASURE_STEPS[1], LEASE)).to.equal(false);
      expect(await jobs.reschedule({ ...held, step: 'journal' }, now, { errorType: 'E' })).to.equal(false);
      expect(await jobs.find(ORG_1, 'ses-1')).to.deep.equal(before);
    });

    it('EJ-15 (QA13-F3): an advance to any step but the next (a skip, a repeat, an early end) is refused and changes nothing', async () => {
      await jobs.enqueue(request());
      const held = (await jobs.claimDue('a', LEASE))!;
      const before = await jobs.find(ORG_1, 'ses-1');
      for (const next of [ERASURE_STEPS[2], ERASURE_STEPS[0], null]) {
        const refusal = await jobs.advance(held, { x: 1 }, next, LEASE).catch((error: unknown) => error);
        expect(refusal, String(next)).to.be.instanceOf(HarnessConflictError);
        expect((refusal as HarnessConflictError).code).to.equal('INVALID_TRANSITION');
      }
      expect(await jobs.find(ORG_1, 'ses-1')).to.deep.equal(before);
    });

    it('EJ-9: reschedule releases the lease; a failure counts and is named, a wait keeps both', async () => {
      await jobs.enqueue(request());
      let held = (await jobs.claimDue('a', LEASE))!;
      expect(await jobs.reschedule(held, now, { errorType: 'E1' })).to.equal(true);
      held = (await jobs.claimDue('b', LEASE))!;
      expect(held).to.include({ attempt: 2, failures: 1, lastErrorType: 'E1' });
      expect(await jobs.reschedule(held, now, { errorType: 'E2' })).to.equal(true);
      held = (await jobs.claimDue('c', LEASE))!;
      expect(held).to.include({ failures: 2, lastErrorType: 'E2' });
      expect(await jobs.reschedule(held, now + 100, null)).to.equal(true);
      expect(await jobs.find(ORG_1, 'ses-1')).to.deep.include({
        failures: 2,
        lastErrorType: 'E2',
        nextAttemptAt: now + 100,
      });
    });

    it('EJ-10: withdraw is fenced on attempt and step and removes only that job', async () => {
      await jobs.enqueue(request());
      await jobs.enqueue(request({ sessionId: 'ses-2' }));
      const held = (await jobs.claimDue('a', LEASE))!;
      expect(await jobs.withdraw({ ...held, attempt: held.attempt + 1 })).to.equal(false);
      expect(await jobs.withdraw({ ...held, step: 'journal' })).to.equal(false);
      expect(await jobs.withdraw(held)).to.equal(true);
      expect(await jobs.find(ORG_1, held.sessionId)).to.equal(null);
      expect(await jobs.withdraw(held)).to.equal(false);
      expect([...(await jobs.findMany(ORG_1, ['ses-1', 'ses-2'])).keys()]).to.deep.equal([
        held.sessionId === 'ses-1' ? 'ses-2' : 'ses-1',
      ]);
    });

    it("EJ-11: findMany answers the existing jobs of one org by session, never another org's", async () => {
      await jobs.enqueue(request());
      await jobs.enqueue(request({ sessionId: 'ses-2' }));
      await jobs.enqueue(request({ orgId: ORG_2, sessionId: 'ses-3' }));
      const found = await jobs.findMany(ORG_1, ['ses-1', 'ses-2', 'ses-3']);
      expect([...found.keys()].sort()).to.deep.equal(['ses-1', 'ses-2']);
      expect(found.get('ses-2')).to.deep.equal(await jobs.find(ORG_1, 'ses-2'));
      expect((await jobs.findMany(ORG_2, ['ses-1', 'ses-2'])).size).to.equal(0);
      expect((await jobs.findMany(ORG_1, [])).size).to.equal(0);
    });

    it('EJ-12: concurrent claimers never hold the same job', async () => {
      for (let index = 0; index < 4; index += 1) {
        await jobs.enqueue(request({ sessionId: `ses-${String(index)}` }));
      }
      const claimed = await Promise.all(
        Array.from({ length: 8 }, (_, index) => jobs.claimDue(`holder-${String(index)}`, LEASE)),
      );
      const sessions = claimed.filter((job) => job !== null).map((job) => job.sessionId);
      expect(sessions.sort()).to.deep.equal(['ses-0', 'ses-1', 'ses-2', 'ses-3']);
      expect(claimed.filter((job) => job === null)).to.have.length(4);
    });

    it('EJ-14: once its job is withdrawn, the session can be enqueued again', async () => {
      await jobs.enqueue(request());
      const held = (await jobs.claimDue('a', LEASE))!;
      expect(await jobs.withdraw(held)).to.equal(true);
      expect(await jobs.enqueue(request())).to.equal(true);
      expect(await jobs.find(ORG_1, 'ses-1')).to.include({ status: 'pending', attempt: 0 });
    });

    it("EJ-13: now() reads the store's clock", async () => {
      expect(await jobs.now()).to.equal(T0);
      now += 123;
      expect(await jobs.now()).to.equal(T0 + 123);
    });
  });
}
