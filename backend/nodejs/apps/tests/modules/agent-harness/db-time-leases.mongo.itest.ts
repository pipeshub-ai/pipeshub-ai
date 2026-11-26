/**
 * J-5: leases, fencing and expiry run on DB time. The services' clocks are
 * skewed by an hour either way (as on a replica with a broken NTP, or a laptop
 * after sleep); the production stores read `$$NOW`, so nothing changes.
 * The skew goes through the services' clock seams, not a faked global `Date`:
 * Mongoose stamps heartbeats with the global `Date` and judges them with the
 * native one, so a `Date` faked an hour behind across one heartbeat marked the
 * live connection disconnected and buffered the next operation (FLK9).
 * Real replica set required (see execution-store.mongo.itest.ts).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Types } from 'mongoose';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { MongoLeaderLease } from '../../../src/libs/repositories/agent-harness/leader-lease/mongo';
import { mongoServerTime } from '../../../src/libs/repositories/agent-harness/time/mongo';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  mongoBackend,
  resetHarnessMongo,
} from './support/harness-mongo';
import { fixedRollout } from './support/rollout';
import { testCipher } from './support/data-protection';

const HOUR = 60 * 60 * 1000;

const conflictCode = async (work: Promise<unknown>): Promise<string> => {
  try {
    await work;
  } catch (error) {
    expect(error).to.be.instanceOf(HarnessConflictError);
    return (error as HarnessConflictError).code;
  }
  return expect.fail('expected a HarnessConflictError');
};

const sleep = (ms: number) =>
  new Promise((resolve) => {
    setTimeout(resolve, ms);
  });

// Long enough that a stalled host cannot lapse it between two assertions.
const LIVE_LEASE_MS = 30_000;

describe('J-5 leases on DB time with a clock-skewed Node process', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  for (const skewMs of [HOUR, -HOUR]) {
    describe(`Node clock ${skewMs > 0 ? 'ahead' : 'behind'} by an hour`, () => {
      let executions: ExecutionService;
      let admissions: RunAdmissionService;

      beforeEach(() => {
        const clock = { now: () => Date.now() + skewMs };
        const backend = mongoBackend();
        executions = new ExecutionService({
          store: backend.executionStore,
          inputs: backend.inputs ?? null,
          journal: backend.journal,
          journals: new JournalService(backend.payloads, testCipher()),
          clock,
        });
        admissions = new RunAdmissionService({
          store: backend.admissionStore,
          rollout: fixedRollout(),
          clock,
        });
      });

      const admit = async (): Promise<HarnessRun> => {
        const outcome = await admissions.submit({
          orgId: new Types.ObjectId().toString(),
          actorId: new Types.ObjectId().toString(),
          idempotencyKey: `skew-${String(Math.random()).slice(2)}`,
          deadlineMs: 10 * 60_000,
          input: {
            query: 'q',
            recordIds: [],
            filters: {},
            attachments: [],
            model: { chatMode: 'auto' },
          },
        });
        if (outcome.route !== 'durable') expect.fail('expected durable');
        return outcome.admission.run;
      };

      const claim = async (
        run: HarnessRun,
        workerId: string,
        leaseMs: number,
      ): Promise<HarnessRun> =>
        (
          await executions.claim({
            orgId: run.orgId,
            runId: run.runId,
            workerId,
            attemptId: `attempt-${workerId}`,
            leaseDurationMs: leaseMs,
          })
        ).run;

      it('anchors the admission deadline and the lease on DB time', async () => {
        const run = await admit();
        const dbNow = await mongoServerTime.read();
        expect(
          Math.abs(run.absoluteDeadlineAt - (dbNow + 10 * 60_000)),
        ).to.be.below(5_000);
        const claimed = await claim(run, 'a', 30_000);
        expect(
          Math.abs((claimed.leaseExpiresAt as number) - (dbNow + 30_000)),
        ).to.be.below(5_000);
      });

      it('a live lease cannot be stolen or swept, and its owner keeps committing', async () => {
        const run = await admit();
        const owned = await claim(run, 'a', 30_000);
        expect(await conflictCode(claim(run, 'b', 30_000))).to.equal(
          'RUN_NOT_CLAIMABLE',
        );
        expect(await executions.sweep(100)).to.equal(0);
        const renewed = await executions.renew({
          orgId: run.orgId,
          runId: run.runId,
          workerId: 'a',
          attemptId: owned.attemptId as string,
          fence: owned.fence,
          leaseDurationMs: 30_000,
        });
        const receipt = await executions.commit({
          commandId: 'progress-1',
          orgId: run.orgId,
          runId: run.runId,
          workerId: 'a',
          attemptId: owned.attemptId as string,
          fence: owned.fence,
          expectedRevision: renewed.revision,
          status: 'running',
          events: [{ type: 'TEXT_MESSAGE_CONTENT', data: { delta: 'x' } }],
          outbox: [],
        });
        expect(receipt.run.fence).to.equal(owned.fence);
      });

      it('a lapsed lease is re-claimable and fences the old owner out', async () => {
        const run = await admit();
        const first = await claim(run, 'a', 300);
        await sleep(450);
        const second = await claim(run, 'b', 30_000);
        expect(second.fence).to.equal(first.fence + 1);
        expect(
          await conflictCode(
            executions.commit({
              commandId: 'stale',
              orgId: run.orgId,
              runId: run.runId,
              workerId: 'a',
              attemptId: first.attemptId as string,
              fence: first.fence,
              expectedRevision: first.revision,
              status: 'completed',
              outcome: 'answer',
              events: [{ type: 'run_completed', data: { answer: 'late' } }],
              outbox: [],
            }),
          ),
        ).to.equal('STALE_ATTEMPT');
      });

      it('the sweeper re-queues only a lease that lapsed on DB time', async () => {
        const live = await admit();
        await claim(live, 'a', LIVE_LEASE_MS);
        const run = await admit();
        await claim(run, 'a', 300);
        await sleep(450);
        expect(await executions.sweep(100)).to.equal(1);
        expect((await executions.get(run.orgId, run.runId))?.status).to.equal(
          'queued',
        );
        expect(
          (await executions.get(live.orgId, live.runId))?.status,
        ).to.not.equal('queued');
      });

      it('elects the sweeper leader on DB time', async () => {
        const lease = new MongoLeaderLease();
        expect(await lease.tryAcquire('held', 'a', LIVE_LEASE_MS)).to.equal(
          true,
        );
        expect(await lease.tryAcquire('held', 'b', 300)).to.equal(false);
        expect(await lease.tryAcquire('skew', 'a', 300)).to.equal(true);
        await sleep(450);
        expect(await lease.tryAcquire('skew', 'b', 300)).to.equal(true);
      });
    });
  }
});
