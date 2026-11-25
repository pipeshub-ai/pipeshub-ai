/**
 * ExecutionStore contract, part of `describeExecutionStoreContract`. Claims and leases: fencing, renewal, release, the sweeper, worker transitions, worker compatibility, schema gates.
 */
import { expect } from 'chai';
import { randomUUID } from 'crypto';
import { HarnessConflictError } from '../../../../../src/modules/agent-harness/domain/errors';
import { CommitBatch, INTERACTIVE_CLAIM_DEADLINE_MS, NOT_STARTED, HarnessRun, RunStatus, ClaimedRun, RunTransition, T_CLAIM_MS, waitForPersonMs } from '../../../../../src/modules/agent-harness/domain/execution';
import { ExecutionService } from '../../../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../../../src/modules/agent-harness/application/journal-service';
import { HARNESS_ENABLED } from '../rollout';
import { InMemoryRecordReadAccess } from '../../../../../src/modules/agent-harness/adapters/in-memory-record-access';
import { workerEvent } from '../journal-fixtures';
import { canonicalDigest } from '../../../../../src/modules/agent-harness/contracts/canonical-json';
import { principalRefFor, pinRunSpec, readPinnedSpec, RUN_SPEC_FOLD_VERSION, RUN_SPEC_SCHEMA_VERSION, RunSpecResolver } from '../../../../../src/modules/agent-harness/domain/run-spec';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../../../src/modules/agent-harness/domain/capabilities';
import { CapabilitySource } from '../../../../../src/modules/agent-harness/ports/capability-probe';
import { InlineClaimTarget } from '../../../../../src/modules/agent-harness/ports/inline-claim';
import { testCipher } from '../data-protection';
import { currentWorker, newerWorker, v2OnlyWorker } from '../worker-capabilities';
import { InMemoryWorkerFeatureSightings } from '../../../../../src/modules/agent-harness/adapters/in-memory-worker-feature-sightings';
import { AVAILABILITY_CACHE_MS, WORKER_FEATURE_WINDOW_MS, WorkerFeatureAvailability } from '../../../../../src/modules/agent-harness/application/worker-feature-availability';
import { ClaimingWorker, guardedWorkerFeatures, KNOWN_WORKER_FEATURES, RESOLVED_CONFIG_FEATURE, UNADVERTISED_WORKER } from '../../../../../src/modules/agent-harness/domain/worker-compat';
import { RunSpec } from '../../../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { PolicyPinning } from '../../../../../src/modules/agent-harness/application/admission/policy-pinning';
import { silentHarnessLogger } from '../../../../../src/modules/agent-harness/application/harness-log';
import { aPin, pinnerOf } from '../a-pin';
import { ContractHarness, START, DEADLINE_MS, conflictCode, DURABLE_CAPABILITIES, revisionsOf } from './harness';

const A_PIN = aPin();

export function describeClaimCases(
  harness: () => ContractHarness,
): void {
  let h: ContractHarness;
  beforeEach(() => {
    h = harness();
  });

  it('fences an expired worker and rejects its later commit', async () => {
    const { run } = await h.admit();
    const first = await h.claim(run, 'worker-a', 100);
    h.setNow(START + 101);
    const second = await h.claim(run, 'worker-b', 100);
    expect(second.fence).to.equal(first.fence + 1);

    expect(
      await conflictCode(
        h.service.commit({
          commandId: 'stale-commit',
          orgId: run.orgId,
          runId: run.runId,
          workerId: 'worker-a',
          attemptId: first.attemptId as string,
          fence: first.fence,
          expectedRevision: first.revision,
          status: 'completed',
          outcome: 'answer',
          events: [],
          outbox: [],
        }),
      ),
    ).to.equal('STALE_ATTEMPT');
  });

  describe('a store-side failure never cuts a live attempt short', () => {
    const commitAs = (claimed: HarnessRun, workerId: string) =>
      h.service.commit({
        commandId: `${workerId}:done`,
        orgId: claimed.orgId,
        runId: claimed.runId,
        workerId,
        attemptId: claimed.attemptId as string,
        fence: claimed.fence,
        expectedRevision: claimed.revision,
        status: 'completed',
        outcome: 'answer',
        events: [],
        outbox: [],
      });

    it('FR-1: failRun leaves a run another attempt holds on an unexpired lease, and its holder still commits', async () => {
      const { run } = await h.admit({ deadlineMs: 600_000 });
      const held = await h.claim(run, 'worker-a', 60_000);
      h.setNow(START + 59_999);
      expect(
        await h.backend.executionStore.failRun(run.orgId, run.runId, 'engine_off', []),
      ).to.equal(false);
      const after = await h.service.get(run.orgId, run.runId);
      expect(after).to.include({
        status: 'running',
        workerId: 'worker-a',
        attemptId: held.attemptId,
        leaseExpiresAt: held.leaseExpiresAt,
        revision: held.revision,
      });
      expect((await commitAs(held, 'worker-a')).run.status).to.equal('completed');
    });

    it("FR-2: failRun ends a run whose lease lapsed; the lost holder's commit lands on an ended run", async () => {
      const { run } = await h.admit({ deadlineMs: 600_000 });
      const held = await h.claim(run, 'worker-a', 60_000);
      h.setNow(START + 60_000);
      expect(
        await h.backend.executionStore.failRun(run.orgId, run.runId, 'engine_off', []),
      ).to.equal(true);
      expect(await conflictCode(commitAs(held, 'worker-a'))).to.equal('INVALID_TRANSITION');
      expect((await h.service.get(run.orgId, run.runId))?.status).to.equal('failed');
    });

    it('FR-3: the attempt that holds the run may fail it under its own lease', async () => {
      const { run } = await h.admit({ deadlineMs: 600_000 });
      const held = await h.claim(run, 'worker-a', 60_000);
      expect(
        await h.backend.executionStore.failRun(run.orgId, run.runId, 'journal_payload_missing', [], 'attempt-worker-b'),
      ).to.equal(false);
      expect(
        await h.backend.executionStore.failRun(run.orgId, run.runId, 'journal_payload_missing', [], held.attemptId as string),
      ).to.equal(true);
      expect((await h.service.get(run.orgId, run.runId))?.status).to.equal('failed');
    });

    it('FR-4: a queued run fails as before', async () => {
      const { run } = await h.admit();
      expect(
        await h.backend.executionStore.failRun(run.orgId, run.runId, 'not_started', []),
      ).to.equal(true);
      expect((await h.service.get(run.orgId, run.runId))?.status).to.equal('failed');
    });
  });

  it('bounds lease renewal by the absolute deadline', async () => {
    const { run } = await h.admit({ deadlineMs: 60_000 });
    const deadline = START + 60_000;
    h.setNow(deadline - 500);
    const claimed = await h.claim(run, 'worker-a', 2_000);
    expect(claimed.leaseExpiresAt).to.equal(deadline);
    h.setNow(deadline - 200);
    const renewed = await h.service.renew({
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'worker-a',
      attemptId: claimed.attemptId as string,
      fence: claimed.fence,
      leaseDurationMs: 2_000,
    });
    expect(renewed.leaseExpiresAt).to.equal(deadline);
  });

  it('releases the worker lease while keeping a paused run active', async () => {
    const admitted = await h.admit();
    const { run } = admitted;
    const claimed = await h.claim(run);
    const receipt = await h.service.commit({
      commandId: 'pause-1',
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'worker-a',
      attemptId: claimed.attemptId as string,
      fence: claimed.fence,
      expectedRevision: claimed.revision,
      status: 'paused',
      events: [{ type: 'run_paused', data: {} }],
      outbox: [],
    });
    expect(receipt.run.workerId).to.equal(null);
    expect(receipt.run.attemptId).to.equal(null);
    expect(receipt.run.leaseExpiresAt).to.equal(null);
    expect(
      await conflictCode(h.admit({ sessionId: admitted.sessionId })),
    ).to.equal('RUN_ACTIVE');

    const resumed = await h.service.control({
      commandId: 'resume-1',
      dispatchCommandId: 'dispatch-resume-1',
      orgId: run.orgId,
      runId: run.runId,
      expectedRevision: receipt.run.revision,
      action: 'resume',
    });
    expect(resumed.run.status).to.equal('queued');
    const reclaimed = await h.claim(run, 'worker-b');
    expect(reclaimed.workerId).to.equal('worker-b');
    expect(reclaimed.fence).to.equal(claimed.fence + 1);
  });

  describe('sweep', () => {
    const pastDeadline = START + DEADLINE_MS;

    const commitIdle = async (
      run: HarnessRun,
      status: 'waiting_approval' | 'paused',
    ): Promise<HarnessRun> => {
      const claimed = await h.claim(run);
      return (
        await h.service.commit({
          commandId: `${status}-1`,
          orgId: run.orgId,
          runId: run.runId,
          workerId: 'worker-a',
          attemptId: claimed.attemptId as string,
          fence: claimed.fence,
          expectedRevision: claimed.revision,
          status,
          events: [],
          outbox: [],
        })
      ).run;
    };

    it('re-queues a running run whose lease lapsed, with a recovery dispatch', async () => {
      const { run } = await h.admit();
      const claimed = await h.claim(run, 'worker-a', 100);
      h.setNow(START + 100);
      expect(await h.service.sweep(10)).to.equal(1);
      const recovered = await h.service.get(run.orgId, run.runId);
      expect(recovered?.status).to.equal('queued');
      expect(recovered?.workerId).to.equal(null);
      expect(
        (await h.backend.dispatches(run.orgId, run.runId)).at(-1),
      ).to.deep.equal({
        commandId: `recover:${run.runId}:${String(claimed.fence)}:${String(claimed.revision + 1)}`,
        kind: 'run_resume',
      });
      const reclaimed = await h.claim(run, 'worker-b');
      expect(reclaimed.fence).to.equal(claimed.fence + 1);
    });

    it('records how each claim got the run: queue, expired-lease takeover, or queue after a sweep', async () => {
      const { run } = await h.admit();
      const first = await h.claim(run, 'worker-a', 100);
      expect(first.claimedFrom).to.equal('queued');
      // Lease lapsed and no sweep ran yet: the next worker takes the running run over.
      h.setNow(START + 100);
      const takeover = await h.claim(run, 'worker-b', 100);
      expect(takeover.claimedFrom).to.equal('expired_lease');
      h.setNow(START + 200);
      expect(await h.service.sweep(10)).to.equal(1);
      const afterSweep = await h.claim(run, 'worker-c', 100);
      expect(afterSweep.claimedFrom).to.equal('queued');
    });

    it('finishes cancellation when the cancelling worker lease lapses', async () => {
      const { run, sessionId } = await h.admit();
      const claimed = await h.claim(run, 'worker-a', 100);
      await h.service.control({
        commandId: 'cancel-lapsed',
        dispatchCommandId: 'cancel-lapsed-dispatch',
        orgId: run.orgId,
        runId: run.runId,
        expectedRevision: claimed.revision,
        action: 'cancel',
      });
      h.setNow(START + 100);
      expect(await h.service.sweep(10)).to.equal(1);
      expect((await h.service.get(run.orgId, run.runId))?.status).to.equal(
        'cancelled',
      );
      expect(await h.service.getActive(run.orgId, sessionId)).to.equal(null);
    });

    it('fails a queued run past its deadline and releases the session', async () => {
      const admitted = await h.admit();
      h.setNow(pastDeadline);
      expect(await h.service.sweep(10)).to.equal(1);
      const failed = await h.service.get(
        admitted.run.orgId,
        admitted.run.runId,
      );
      expect(failed?.status).to.equal('failed');
      expect(
        await h.service.getActive(admitted.run.orgId, admitted.sessionId),
      ).to.equal(null);
      const events = await h.service.readEvents(
        admitted.run.orgId,
        admitted.sessionId,
      );
      expect(events.at(-1)?.type).to.equal('run_failed');
      expect(events.at(-1)?.data).to.deep.equal({
        reason: 'deadline_exceeded',
      });
      const next = await h.admit({ sessionId: admitted.sessionId });
      expect(next.created).to.equal(true);
    });

    it('fails a paused run past its deadline, and a run waiting for approval only at the end of the wait window', async () => {
      const approval = await h.admit();
      const paused = await h.admit();
      await commitIdle(approval.run, 'waiting_approval');
      await commitIdle(paused.run, 'paused');
      h.setNow(pastDeadline - 1);
      expect(await h.service.sweep(10)).to.equal(0);
      h.setNow(pastDeadline);
      expect(await h.service.sweep(10)).to.equal(1);
      // FIX-P1-F3: the wait for a person does not count against the run's deadline.
      h.setNow(START + waitForPersonMs() - 1);
      expect(await h.service.sweep(10)).to.equal(0);
      h.setNow(START + waitForPersonMs());
      expect(await h.service.sweep(10)).to.equal(1);
      for (const admitted of [approval, paused]) {
        expect(
          (await h.service.get(admitted.run.orgId, admitted.run.runId))
            ?.status,
        ).to.equal('failed');
        expect(
          await h.service.getActive(admitted.run.orgId, admitted.sessionId),
        ).to.equal(null);
      }
    });

    describe('QA13-8 (JRN-18): every non-terminal state at its absolute deadline', () => {
      /** A run put in `state` by the worker or the person who would put it there. */
      const inState = async (state: RunStatus): Promise<HarnessRun> => {
        const { run } = await h.admit();
        if (state === 'queued') return run;
        // A lease longer than the run: it is bounded by the deadline, so only the deadline ends it.
        const claimed = await h.claim(run, 'worker-a', 60_000);
        if (state === 'running') return claimed;
        if (state === 'cancelling') {
          return (
            await h.service.control({
              commandId: `cancel-${run.runId}`,
              dispatchCommandId: `cancel-${run.runId}:dispatch`,
              orgId: run.orgId,
              runId: run.runId,
              expectedRevision: claimed.revision,
              action: 'cancel',
            })
          ).run;
        }
        return (
          await h.service.commit({
            commandId: `${state}-${run.runId}`,
            orgId: run.orgId,
            runId: run.runId,
            workerId: 'worker-a',
            attemptId: claimed.attemptId as string,
            fence: claimed.fence,
            expectedRevision: claimed.revision,
            status: state,
            events: [],
            outbox: [],
          })
        ).run;
      };
      const NEXT: Array<[RunStatus, RunStatus]> = [
        ['queued', 'failed'],
        ['running', 'failed'],
        ['waiting_approval', 'failed'],
        ['paused', 'failed'],
        ['recovering', 'failed'],
        ['needs_reconciliation', 'failed'],
        ['cancelling', 'cancelled'],
      ];

      for (const [state, next] of NEXT) {
        it(`${state}: untouched a millisecond before, ${next} at it, and its session released`, async () => {
          const run = await inState(state);
          expect(run.status).to.equal(state);
          // FIX-P1-F3: a run waiting for a person is answerable until the end of the wait window instead.
          const deadline = state === 'waiting_approval' ? START + waitForPersonMs() : pastDeadline;
          h.setNow(deadline - 1);
          await h.service.sweep(10);
          expect((await h.service.get(run.orgId, run.runId))?.status).to.equal(state);
          h.setNow(deadline);
          expect(await h.service.sweep(10)).to.equal(1);
          expect((await h.service.get(run.orgId, run.runId))?.status).to.equal(next);
          expect(await h.service.getActive(run.orgId, run.sessionId)).to.equal(null);
        });
      }
    });

    it('UX3 QD-4: an interactive run nobody claimed fails not_started at the claim deadline, not before', async () => {
      const admitted = await h.admit({ deadlineMs: 3_600_000 });
      h.setNow(START + INTERACTIVE_CLAIM_DEADLINE_MS - 1);
      expect(await h.service.sweep(10)).to.equal(0);
      h.setNow(START + INTERACTIVE_CLAIM_DEADLINE_MS);
      expect(await h.service.sweep(10)).to.equal(1);
      const failed = await h.service.get(admitted.run.orgId, admitted.run.runId);
      expect(failed?.status).to.equal('failed');
      expect(await h.service.getActive(admitted.run.orgId, admitted.sessionId)).to.equal(null);
      const events = await h.service.readEvents(admitted.run.orgId, admitted.sessionId);
      expect(events.at(-1)?.data).to.deep.equal({ errorType: NOT_STARTED });
    });

    it('UX3 QD-4: a run a worker once claimed is never failed as not started', async () => {
      const admitted = await h.admit({ deadlineMs: 3_600_000 });
      await h.claim(admitted.run, 'worker-a', 100);
      h.setNow(START + 100);
      expect(await h.service.sweep(10)).to.equal(1);
      expect((await h.service.get(admitted.run.orgId, admitted.run.runId))?.status).to.equal('queued');
      h.setNow(START + INTERACTIVE_CLAIM_DEADLINE_MS * 2);
      await h.service.sweep(10);
      expect((await h.service.get(admitted.run.orgId, admitted.run.runId))?.status).to.equal('queued');
    });

    it('bounds each sweep to its limit and drains the rest on later ticks', async () => {
      for (let index = 0; index < 5; index += 1) await h.admit();
      h.setNow(pastDeadline);
      expect(await h.service.sweep(2)).to.equal(2);
      expect(await h.service.sweep(2)).to.equal(2);
      expect(await h.service.sweep(2)).to.equal(1);
      expect(await h.service.sweep(2)).to.equal(0);
    });
  });

  describe('worker transitions and the queue (S5, P1.16)', () => {
    const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
    const agentRun = { agentKey: 'agent-1', authorization: 'Bearer user' };
    /** run.admitted, run.claimed, run.spec_pinned. */
    const CLAIMED_HEAD = 3;

    beforeEach(() => h.setRollout(V2));

    const claimAs = (
      run: Pick<HarnessRun, 'orgId' | 'runId'>,
      worker: string,
    ): Promise<ClaimedRun> =>
      h.service.claim({
        orgId: run.orgId,
        runId: run.runId,
        workerId: worker,
        attemptId: `attempt-${worker}`,
        leaseDurationMs: 1_000,
      });

    const transition = (
      run: HarnessRun,
      worker: string,
      next: RunTransition,
      commandId = `${worker}:t:${String(run.revision)}`,
      events: CommitBatch['events'] = [],
    ) =>
      h.service.commit({
        commandId,
        orgId: run.orgId,
        runId: run.runId,
        workerId: worker,
        attemptId: `attempt-${worker}`,
        fence: run.fence,
        events,
        legacyEvents: [],
        transition: next,
        outbox: [],
      });

    const tail = async (run: HarnessRun, after = CLAIMED_HEAD) =>
      (
        await h.backend.journal.readStream(run.orgId, run.sessionId, after, 100)
      ).map((event) => [event.type, event.writer, event.data]);

    const requested = (interactionId: string) =>
      workerEvent('interaction.requested', {
        interactionId,
        kind: 'approval',
        bindingDigest: `sha256:${'1'.repeat(64)}`,
      });

    it('D-13: a suspension waits for approval (or pauses) and journals run.suspended', async () => {
      const admitted = await h.admit(agentRun);
      const { run } = await claimAs(admitted.run, 'worker-a');
      const receipt = await transition(
        run,
        'worker-a',
        {
          status: 'waiting_approval',
          detail: {
            kind: 'suspend',
            reason: 'approval',
            interactionId: 'ix-1',
          },
        },
        'worker-a:suspend:1',
        [requested('ix-1')],
      );
      expect(receipt.run.status).to.equal('waiting_approval');
      expect([receipt.run.attemptId, receipt.run.workerId]).to.deep.equal([
        null,
        null,
      ]);
      expect(await tail(run)).to.deep.equal([
        [
          'interaction.requested',
          'worker',
          {
            interactionId: 'ix-1',
            kind: 'approval',
            bindingDigest: `sha256:${'1'.repeat(64)}`,
          },
        ],
        [
          'run.suspended',
          'node',
          { reason: 'approval', interactionId: 'ix-1' },
        ],
      ]);

      const other = await h.admit(agentRun);
      const second = await claimAs(other.run, 'worker-b');
      const paused = await transition(second.run, 'worker-b', {
        status: 'paused',
        detail: { kind: 'suspend', reason: 'capability_lost' },
      });
      expect(paused.run.status).to.equal('paused');
      expect(await tail(second.run)).to.deep.equal([
        ['run.suspended', 'node', { reason: 'capability_lost' }],
      ]);
    });

    it('D-13: a v2 status that names a run.* event needs its detail; a v1 run never carries one', async () => {
      const admitted = await h.admit(agentRun);
      const { run } = await claimAs(admitted.run, 'worker-a');
      for (const next of [
        { status: 'queued' },
        { status: 'waiting_approval' },
        {
          status: 'paused',
          detail: { kind: 'suspend', reason: 'approval' },
        },
        {
          status: 'completed',
          outcome: 'answer',
          detail: { kind: 'fail' },
        },
      ] as RunTransition[]) {
        expect(
          await conflictCode(transition(run, 'worker-a', next)),
        ).to.equal('INVALID_TRANSITION');
      }
      h.setRollout(HARNESS_ENABLED);
      const v1 = await h.admit(agentRun);
      const claimedV1 = await claimAs(v1.run, 'worker-b');
      expect(
        await conflictCode(
          transition(claimedV1.run, 'worker-b', {
            expectedRevision: claimedV1.run.revision,
            status: 'paused',
            detail: { kind: 'suspend', reason: 'input' },
          }),
        ),
      ).to.equal('INVALID_TRANSITION');
      const current = await h.service.get(run.orgId, run.runId);
      expect(current?.status).to.equal('running');
    });

    it('D-13: a yield re-queues with notBefore and its checkpoint, wakes workers and journals run.yielded', async () => {
      const admitted = await h.admit(agentRun);
      const { run } = await claimAs(admitted.run, 'worker-a');
      const notBefore = new Date(START + 5_000).toISOString();
      const receipt = await transition(run, 'worker-a', {
        status: 'queued',
        detail: {
          kind: 'yield',
          reason: 'llm_backoff',
          notBefore,
          checkpointRef: 'ckpt-1',
        },
      });
      expect(receipt.run.status).to.equal('queued');
      expect(receipt.run.notBefore).to.equal(START + 5_000);
      expect(receipt.run.checkpointRef).to.equal('ckpt-1');
      expect(await tail(run)).to.deep.equal([
        ['run.yielded', 'node', { notBefore, reason: 'llm_backoff' }],
      ]);
      expect(
        (await h.backend.dispatches(run.orgId, run.runId)).map(
          (item) => item.commandId,
        ),
      ).to.include(`yield:${run.runId}:${String(run.fence)}`);

      expect(await conflictCode(claimAs(run, 'worker-b'))).to.equal(
        'RUN_NOT_CLAIMABLE',
      );
      expect(
        await h.service.claimNext({
          role: 'dispatched',
          workerId: 'worker-b',
          attemptId: 'attempt-worker-b',
          leaseDurationMs: 1_000,
        }),
      ).to.equal(null);
      h.setNow(START + 5_000);
      const again = await claimAs(run, 'worker-b');
      expect(again.run.checkpointRef).to.equal('ckpt-1');
      expect(again.run.attempts).to.equal(1);
      expect(again.run.fence).to.equal(run.fence + 1);
    });

    describe('MCPFIX F6: a run waiting out its yield is not a run nobody claimed', () => {
      const WAIT_MS = 120_000;
      const yielded = async (): Promise<HarnessRun> => {
        const admitted = await h.admit({ ...agentRun, deadlineMs: 3_600_000 });
        const { run } = await claimAs(admitted.run, 'worker-a');
        const receipt = await transition(run, 'worker-a', {
          status: 'queued',
          detail: {
            kind: 'yield',
            reason: 'capability_wait',
            notBefore: new Date(START + WAIT_MS).toISOString(),
          },
        });
        expect(receipt.run.attempts).to.equal(0);
        return receipt.run;
      };
      const statusOf = async (run: HarnessRun): Promise<RunStatus | undefined> =>
        (await h.service.get(run.orgId, run.runId))?.status;

      it('F6-1: the sweep leaves it queued for the whole wait, past the claim deadline', async () => {
        const run = await yielded();
        for (const at of [INTERACTIVE_CLAIM_DEADLINE_MS + 1_000, WAIT_MS - 1]) {
          h.setNow(START + at);
          await h.service.sweep(10);
          expect(await statusOf(run), `at ${String(at)} ms`).to.equal('queued');
        }
        const types = (await h.service.readEvents(run.orgId, run.sessionId)).map((event) => event.type);
        expect(types).to.not.include('run.failed');
      });

      it('F6-2: once the wait is over, the claim deadline counts from then', async () => {
        const run = await yielded();
        h.setNow(START + WAIT_MS + INTERACTIVE_CLAIM_DEADLINE_MS - 1);
        await h.service.sweep(10);
        expect(await statusOf(run)).to.equal('queued');
        h.setNow(START + WAIT_MS + INTERACTIVE_CLAIM_DEADLINE_MS);
        await h.service.sweep(10);
        expect(await statusOf(run)).to.equal('failed');
        const events = await h.service.readEvents(run.orgId, run.sessionId);
        expect(events.at(-1)?.data).to.deep.equal({ errorType: NOT_STARTED });
      });
    });

    it('D-13: a promotion yield hands an inline run to dispatched workers', async () => {
      h.setInlineHint({ hint: async () => undefined });
      const admitted = await h.admit(agentRun);
      expect(admitted.run.profile).to.equal('inline');
      const { run } = await claimAs(admitted.run, 'worker-a');
      const receipt = await transition(run, 'worker-a', {
        status: 'queued',
        detail: {
          kind: 'yield',
          reason: 'promotion',
          notBefore: new Date(START).toISOString(),
        },
      });
      expect(receipt.run.profile).to.equal('dispatched');
      // Never before DB time, whatever the worker asked for.
      expect(receipt.run.notBefore).to.equal(START);
    });

    it('D-13: quarantine and fail are terminal and journal run.quarantined / run.failed', async () => {
      const first = await h.admit(agentRun);
      const a = await claimAs(first.run, 'worker-a');
      const quarantined = await transition(a.run, 'worker-a', {
        status: 'failed',
        errorType: 'crash_loop',
        detail: {
          kind: 'quarantine',
          attempts: 4,
          lastErrorType: 'crash_loop',
        },
      });
      expect(quarantined.run.status).to.equal('failed');
      expect(await tail(a.run)).to.deep.equal([
        [
          'run.quarantined',
          'node',
          { attempts: 4, lastErrorType: 'crash_loop' },
        ],
      ]);
      expect(
        await h.service.getActive(first.run.orgId, first.sessionId),
      ).to.equal(null);

      const second = await h.admit(agentRun);
      const b = await claimAs(second.run, 'worker-b');
      await transition(b.run, 'worker-b', {
        status: 'failed',
        errorType: 'model_failed',
        detail: { kind: 'fail', message: 'provider said no' },
      });
      expect(await tail(b.run)).to.deep.equal([
        [
          'run.failed',
          'node',
          { errorType: 'model_failed', message: 'provider said no' },
        ],
      ]);
    });

    it('D-15: attempt counts claims since the run last left running cleanly', async () => {
      const admitted = await h.admit(agentRun);
      const claimedEvents = async () =>
        (
          await h.backend.journal.readStream(
            admitted.run.orgId,
            admitted.run.sessionId,
            0,
            100,
          )
        )
          .filter((event) => event.type === 'run.claimed')
          .map((event) => event.data?.['attempt']);
      let now = START;
      const expire = async () => {
        now += 1_001;
        h.setNow(now);
        await h.service.sweep(10);
      };
      const attempts: number[] = [];
      for (let index = 0; index < 4; index += 1) {
        attempts.push((await claimAs(admitted.run, `w${index}`)).run.attempts ?? -1);
        await expire();
      }
      expect(attempts).to.deep.equal([1, 2, 3, 4]);

      const fifth = await claimAs(admitted.run, 'w4');
      await transition(fifth.run, 'w4', {
        status: 'queued',
        detail: {
          kind: 'yield',
          reason: 'drain',
          notBefore: new Date(now).toISOString(),
        },
      });
      const sixth = await claimAs(admitted.run, 'w5');
      expect(sixth.run.attempts).to.equal(1);
      expect(await claimedEvents()).to.deep.equal([1, 2, 3, 4, 5, 1]);
    });

    it('CH-01: a claim after an attempt that died tells the person; a claim after a drain says nothing', async () => {
      const admitted = await h.admit(agentRun);
      const told = async () =>
        (
          await h.backend.journal.readStream(admitted.run.orgId, admitted.run.sessionId, 0, 100)
        )
          .filter((event) => event.type === 'run.claimed' || event.type === 'x.pipeshub.run.notice')
          .map((event) =>
            event.type === 'run.claimed'
              ? `claimed:${String(event.data?.['attempt'])}`
              : `notice:${String(event.data?.['code'])}`,
          );
      const first = await claimAs(admitted.run, 'w0');
      // The first attempt drains: it yields, so the next claim is attempt 1 again and nothing is said (CH-15).
      await transition(first.run, 'w0', {
        status: 'queued',
        detail: { kind: 'yield', reason: 'drain', notBefore: new Date(START).toISOString() },
      });
      await claimAs(admitted.run, 'w1');
      // That attempt dies: its lease lapses and another worker takes the run over.
      h.setNow(START + 1_001);
      await claimAs(admitted.run, 'w2');
      expect(await told()).to.deep.equal([
        'claimed:1',
        'claimed:1',
        'claimed:2',
        'notice:resumed',
      ]);
    });

    it('releases an attempt: queued at once, no journal event, a wake hint, fenced out', async () => {
      const admitted = await h.admit(agentRun);
      const { run } = await claimAs(admitted.run, 'worker-a');
      const released = await h.service.release({
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'worker-a',
        attemptId: 'attempt-worker-a',
        fence: run.fence,
        reason: 'fold_incompatible',
      });
      expect([released.status, released.attemptId, released.fence]).to.deep.equal(
        ['queued', null, run.fence],
      );
      expect(await tail(run)).to.deep.equal([]);
      expect(
        (await h.backend.dispatches(run.orgId, run.runId)).map(
          (item) => item.commandId,
        ),
      ).to.include(`release:${run.runId}:${String(run.fence)}`);
      expect(
        await conflictCode(
          h.service.release({
            orgId: run.orgId,
            runId: run.runId,
            workerId: 'worker-a',
            attemptId: 'attempt-worker-a',
            fence: run.fence,
            reason: 'again',
          }),
        ),
      ).to.equal('STALE_ATTEMPT');
      const again = await claimAs(run, 'worker-b');
      expect([again.run.fence, again.run.attempts]).to.deep.equal([
        run.fence + 1,
        2,
      ]);
    });

    it('O-5a: claim-next takes the oldest due run for its role; a lost race moves on', async () => {
      const claimNext = (role: 'inline' | 'dispatched', worker: string) =>
        h.service.claimNext({
          role,
          workerId: worker,
          attemptId: `attempt-${worker}`,
          leaseDurationMs: 1_000,
        });
      const dispatched = await h.admit(agentRun);
      h.setNow(START + 10);
      h.setInlineHint({ hint: async () => undefined });
      const inline = await h.admit(agentRun);
      h.setInlineHint(undefined);
      h.setRollout(HARNESS_ENABLED);
      await h.admit(agentRun);
      h.setRollout(V2);

      // The inline claimer sees only its inline run; v1 runs never queue here.
      const inlineClaim = await claimNext('inline', 'query-1');
      expect(inlineClaim?.run.runId).to.equal(inline.run.runId);
      expect(await claimNext('inline', 'query-2')).to.equal(null);

      const first = await claimNext('dispatched', 'worker-a');
      expect(first?.run.runId).to.equal(dispatched.run.runId);
      expect(await claimNext('dispatched', 'worker-b')).to.equal(null);
    });

    it('O-5a: dispatched claimers take an inline run only after it waited T_claim', async () => {
      h.setInlineHint({ hint: async () => undefined });
      const inline = await h.admit(agentRun);
      const claimNext = (worker: string) =>
        h.service.claimNext({
          role: 'dispatched',
          workerId: worker,
          attemptId: `attempt-${worker}`,
          leaseDurationMs: 1_000,
        });
      h.setNow(START + T_CLAIM_MS - 1);
      expect(await claimNext('worker-a')).to.equal(null);
      h.setNow(START + T_CLAIM_MS);
      expect((await claimNext('worker-a'))?.run.runId).to.equal(
        inline.run.runId,
      );
    });

    it('D-4: an inline admission hints its claimer and dispatches only after T_claim', async () => {
      const hinted: InlineClaimTarget[] = [];
      h.setInlineHint({
        hint: async (target) => {
          hinted.push(target);
        },
      });
      const admitted = await h.admit(agentRun);
      expect(hinted).to.deep.equal([
        { orgId: admitted.run.orgId, runId: admitted.run.runId },
      ]);
      expect(await h.backend.dispatches(admitted.run.orgId, admitted.run.runId)).to.deep.equal([]);

      h.setNow(START + T_CLAIM_MS - 1);
      expect(await h.service.sweep(10)).to.equal(0);
      h.setNow(START + T_CLAIM_MS);
      expect(await h.service.sweep(10)).to.equal(1);
      const promoted = await h.service.get(admitted.run.orgId, admitted.run.runId);
      expect([promoted?.status, promoted?.profile]).to.deep.equal([
        'queued',
        'dispatched',
      ]);
      expect(
        (await h.backend.dispatches(admitted.run.orgId, admitted.run.runId)).map(
          (item) => item.kind,
        ),
      ).to.deep.equal(['run_ready']);
      expect(await h.service.sweep(10)).to.equal(0);

      // A run its inline claimer took is never promoted.
      const claimedInline = await h.admit(agentRun);
      await claimAs(claimedInline.run, 'query-1');
      h.setNow(START + 3 * T_CLAIM_MS);
      await h.service.sweep(10);
      const kept = await h.service.get(
        claimedInline.run.orgId,
        claimedInline.run.runId,
      );
      expect(kept?.profile).to.equal('inline');
    });

    it('PC-3: the built-in assistant is admitted inline, hinted, dispatched after T_claim, and pinned with its chat grant', async () => {
      const hinted: InlineClaimTarget[] = [];
      h.setInlineHint({
        hint: async (target) => {
          hinted.push(target);
        },
      });
      const assistant = {
        input: {
          query: 'What is the refund policy?',
          recordIds: [],
          filters: { kb: ['kb-1'] },
          attachments: [],
          model: { chatMode: 'internal_search' },
        },
      };
      const admitted = await h.admit(assistant);
      expect(admitted.run.agentKey ?? null).to.equal(null);
      expect(admitted.run.profile).to.equal('inline');
      expect(hinted).to.deep.equal([{ orgId: admitted.run.orgId, runId: admitted.run.runId }]);
      expect(await h.backend.dispatches(admitted.run.orgId, admitted.run.runId)).to.deep.equal([]);

      h.setNow(START + T_CLAIM_MS);
      expect(await h.service.sweep(10)).to.equal(1);
      const promoted = await h.service.get(admitted.run.orgId, admitted.run.runId);
      expect(promoted?.profile).to.equal('dispatched');
      const claimed = await claimAs(admitted.run, 'worker-a');
      const extension = claimed.pinned!.spec.extensions?.['x.pipeshub'] as Record<string, unknown>;
      expect(extension['chat']).to.deep.equal({ mode: 'internal_search', internalSearch: true });
      expect(extension).not.to.have.property('agent');
      expect(extension['knowledge']).to.deep.equal({ apps: [], kb: ['kb-1'], strictScope: false });
      expect(claimed.pinned!.spec.grants.tools).to.deep.equal([]);
    });

    it('D-10: SPEC_UNRESOLVABLE fails the run terminally; claim-next moves on', async () => {
      const logged: Array<[string, string, Record<string, unknown>]> = [];
      const record =
        (level: string) =>
        (message: string, meta: Record<string, unknown> = {}) => {
          logged.push([level, message, meta]);
        };
      const refusing = new ExecutionService({
        store: h.backend.executionStore,
        inputs: h.backend.inputs ?? null,
        journal: h.backend.journal,
        journals: new JournalService(h.backend.payloads, testCipher()),
        capabilities: DURABLE_CAPABILITIES,
        resolveSpec: () => {
          throw new HarnessConflictError('SPEC_UNRESOLVABLE', 'no spec');
        },
        logger: {
          debug: record('debug'),
          info: record('info'),
          warn: record('warn'),
          error: record('error'),
        },
      });
      const admitted = await h.admit(agentRun);
      expect(await conflictCode(refusing.claim({
        orgId: admitted.run.orgId,
        runId: admitted.run.runId,
        workerId: 'worker-a',
        attemptId: 'attempt-a',
        leaseDurationMs: 1_000,
      }))).to.equal('SPEC_UNRESOLVABLE');
      const failed = await h.service.get(admitted.run.orgId, admitted.run.runId);
      expect(failed?.status).to.equal('failed');
      expect(
        await h.service.getActive(admitted.run.orgId, admitted.sessionId),
      ).to.equal(null);
      expect(await tail({ ...admitted.run }, 1)).to.deep.equal([
        ['run.failed', 'node', { errorType: 'spec_unresolvable' }],
      ]);
      // The log is the only place the resolver's refusal reason is kept.
      const [level, message, meta] = logged.find(
        ([, event]) => event === 'run.spec_unresolvable',
      ) ?? ['', '', {}];
      expect([level, message]).to.deep.equal(['warn', 'run.spec_unresolvable']);
      expect(meta).to.include({
        event: 'run.spec_unresolvable',
        orgId: admitted.run.orgId,
        runId: admitted.run.runId,
        reason: 'SPEC_UNRESOLVABLE',
        outcome: 'failed',
      });

      h.setNow(START + 1);
      await h.admit(agentRun);
      expect(
        await refusing.claimNext({
          role: 'dispatched',
          workerId: 'worker-a',
          attemptId: 'attempt-a',
          leaseDurationMs: 1_000,
        }),
      ).to.equal(null);
    });

    it('journals a v2 cancel and an interaction resolution in the control transaction', async () => {
      const admitted = await h.admit(agentRun);
      const { run } = await claimAs(admitted.run, 'worker-a');
      const suspended = await transition(
        run,
        'worker-a',
        {
          status: 'waiting_approval',
          detail: { kind: 'suspend', reason: 'approval', interactionId: 'ix-1' },
        },
        'worker-a:suspend:1',
        [requested('ix-1')],
      );
      const control = (
        commandId: string,
        overrides: Partial<Parameters<ExecutionService['control']>[0]> = {},
      ) =>
        h.service.control({
          commandId,
          dispatchCommandId: `${commandId}:dispatch`,
          orgId: run.orgId,
          runId: run.runId,
          expectedRevision: suspended.run.revision,
          action: 'resume',
          ...overrides,
        });
      expect(await conflictCode(control('resume-bare'))).to.equal(
        'INVALID_TRANSITION',
      );
      expect(
        await conflictCode(
          control('resume-other', {
            resolution: {
              interactionId: 'ix-2',
              decision: 'approved',
              channel: 'web',
            },
          }),
        ),
      ).to.equal('INTERACTION_NOT_OPEN');
      const resumed = await control('resume-1', {
        by: 'p_approver',
        resolution: {
          interactionId: 'ix-1',
          decision: 'approved',
          channel: 'web',
        },
      });
      expect(resumed.run.status).to.equal('queued');
      expect(
        await control('resume-1', {
          by: 'p_approver',
          resolution: {
            interactionId: 'ix-1',
            decision: 'approved',
            channel: 'web',
          },
        }),
      ).to.deep.equal({ ...resumed, replayed: true });
      expect((await tail(run)).slice(2)).to.deep.equal([
        [
          'interaction.resolved',
          'node',
          {
            interactionId: 'ix-1',
            decision: 'approved',
            by: 'p_approver',
            channel: 'web',
          },
        ],
        ['run.resumed', 'node', { cause: 'resolved' }],
      ]);

      const again = await claimAs(run, 'worker-b');
      const cancelled = await h.service.control({
        commandId: 'cancel-1',
        dispatchCommandId: 'cancel-1:dispatch',
        orgId: run.orgId,
        runId: run.runId,
        expectedRevision: again.run.revision,
        action: 'cancel',
      });
      expect(cancelled.run.status).to.equal('cancelling');
      const events = await tail(run);
      expect(events[events.length - 1]).to.deep.equal([
        'run.cancel_requested',
        'node',
        { by: principalRefFor(run.actorId) },
      ]);

      const idle = await h.admit(agentRun);
      const idleCancel = await h.service.control({
        commandId: 'cancel-2',
        dispatchCommandId: 'cancel-2:dispatch',
        orgId: idle.run.orgId,
        runId: idle.run.runId,
        expectedRevision: idle.run.revision,
        action: 'cancel',
      });
      expect(idleCancel.run.status).to.equal('cancelled');
      expect(await tail(idle.run, 1)).to.deep.equal([
        ['run.cancel_requested', 'node', { by: principalRefFor(idle.run.actorId) }],
        ['run.cancelled', 'node', { by: principalRefFor(idle.run.actorId) }],
      ]);
    });

    it('the sweeper journals run.failed when a v2 run passes its deadline', async () => {
      const admitted = await h.admit(agentRun);
      h.setNow(START + DEADLINE_MS);
      expect(await h.service.sweep(10)).to.equal(1);
      expect(await tail(admitted.run, 1)).to.deep.equal([
        ['run.failed', 'node', { errorType: 'deadline_exceeded' }],
      ]);
    });

    it('the latest attempt reads its journal after leaving running; a later claim supersedes it', async () => {
      const admitted = await h.admit(agentRun);
      const { run } = await claimAs(admitted.run, 'worker-a');
      await transition(run, 'worker-a', {
        status: 'paused',
        detail: { kind: 'suspend', reason: 'input' },
      });
      const read = (worker: string, fence: number) =>
        h.service.readRunJournal({
          orgId: run.orgId,
          runId: run.runId,
          workerId: worker,
          attemptId: `attempt-${worker}`,
          fence,
          afterSeq: 0,
          limit: 100,
          scope: 'stream',
        });
      expect((await read('worker-a', run.fence)).map((e) => e.type)).to.include(
        'run.suspended',
      );
      expect(await conflictCode(read('worker-a', run.fence + 1))).to.equal(
        'STALE_ATTEMPT',
      );
    });
  });

  describe('the agent_code deploy-order guard (DUR2)', () => {
    const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
    const LONG = { agentKey: 'agent-1', authorization: 'Bearer user', deadlineMs: 3_600_000 };
    const PROVED: CapabilitySource = {
      current: () =>
        capabilitySnapshot(
          { ...LEAST_CAPABILITIES, 'journal.durable': true, 'sandbox.isolation': 'container' },
          START,
        ),
    };
    const NEW = (): ClaimingWorker => currentWorker();
    const OLD = (): ClaimingWorker =>
      currentWorker({ features: KNOWN_WORKER_FEATURES.filter((f) => f !== 'agent_code') });
    const WAITED = WORKER_FEATURE_WINDOW_MS + AVAILABILITY_CACHE_MS + 1;
    let now: number;
    let availability: WorkerFeatureAvailability;
    const at = (t: number): void => {
      now = t;
      h.setNow(t);
    };
    const poll = (worker: ClaimingWorker, workerId: string, leaseDurationMs = 1_000) =>
      h.service.claimNext({
        role: 'dispatched',
        workerId,
        attemptId: `attempt-${workerId}-${randomUUID()}`,
        leaseDurationMs,
        worker,
      });
    const stored = async (run: Pick<HarnessRun, 'orgId' | 'runId'>) =>
      (await h.backend.executionStore.get(run.orgId, run.runId)) as HarnessRun;
    const requiresAgentCode = (run: HarnessRun): boolean =>
      (run.requires?.features ?? []).includes('agent_code');

    beforeEach(() => {
      at(START);
      h.setRollout(V2);
      h.setCapabilities(PROVED);
      availability = new WorkerFeatureAvailability({
        sightings: new InMemoryWorkerFeatureSightings({ now: () => now }),
        clock: { now: () => now },
      });
      h.setWorkerFeatures(availability);
    });

    it('DUR2-W2: Node first: no live worker runs agent_code, so the run is admitted as before P5.11', async () => {
      expect(await poll(OLD(), 'old')).to.equal(null);
      const admitted = await h.admit(LONG);
      expect(requiresAgentCode(admitted.run)).to.equal(false);
      expect((await poll(OLD(), 'old'))?.run.runId).to.equal(admitted.run.runId);
    });

    it('DUR2-W1/W3: workers first, or mixed: the run requires agent_code and only a newer worker takes it', async () => {
      expect(await poll(NEW(), 'new')).to.equal(null);
      expect(await poll(OLD(), 'old')).to.equal(null);
      const admitted = await h.admit(LONG);
      expect(requiresAgentCode(admitted.run)).to.equal(true);
      expect(await poll(OLD(), 'old')).to.equal(null);
      expect((await poll(NEW(), 'new'))?.run.runId).to.equal(admitted.run.runId);
    });

    it('DUR2-W3: a busy newer worker shows through its renewals; DUR2-W4: silence past the window is a gap', async () => {
      await poll(NEW(), 'new');
      const first = await h.admit(LONG);
      const claimed = await poll(NEW(), 'busy', 600_000);
      expect(claimed?.run.runId).to.equal(first.run.runId);
      at(START + WAITED);
      await h.service.renew({
        orgId: first.run.orgId,
        runId: first.run.runId,
        workerId: 'busy',
        attemptId: claimed!.run.attemptId!,
        fence: claimed!.run.fence,
        leaseDurationMs: 600_000,
      });
      const second = await h.admit(LONG);
      expect(requiresAgentCode(second.run)).to.equal(true);
      at(START + 2 * WAITED + 1);
      // Every worker silent past the window: no live worker shows the feature absent, so it is unknown and required
      // (ADR-CAP-01 D2); DUR2-W5 has the sweeper relax it when the worker that comes lacks it.
      const third = await h.admit(LONG);
      expect(requiresAgentCode(third.run)).to.equal(true);
      // An older worker polling now proves the gap: withheld (once admission's cached answer is past).
      expect(await poll(OLD(), 'old')).to.equal(null);
      at(START + 2 * WAITED + 2 + AVAILABILITY_CACHE_MS);
      const fourth = await h.admit(LONG);
      expect(requiresAgentCode(fourth.run)).to.equal(false);
    });

    it('DUR2-W5: the last newer worker leaves before a claim: the sweeper drops the requirement and an older worker serves the run', async () => {
      await poll(NEW(), 'new');
      const admitted = await h.admit(LONG);
      expect(requiresAgentCode(admitted.run)).to.equal(true);
      at(START + WAITED);
      expect(await poll(OLD(), 'old')).to.equal(null);
      expect((await stored(admitted.run)).waitingForWorker).to.not.equal(null);
      // DUR2-W7: inside the window after the refusal nothing is released.
      await h.service.sweep(10);
      expect(requiresAgentCode(await stored(admitted.run))).to.equal(true);
      at(START + 2 * WAITED);
      await h.service.sweep(10);
      const relaxed = await stored(admitted.run);
      expect([relaxed.status, requiresAgentCode(relaxed), relaxed.waitingForWorker ?? null]).to.deep.equal([
        'queued',
        false,
        null,
      ]);
      expect((await poll(OLD(), 'old'))?.run.runId).to.equal(admitted.run.runId);
    });

    it('DUR2-W7: while a newer worker is live, a waiting run stays waiting for it', async () => {
      await poll(NEW(), 'new');
      const admitted = await h.admit(LONG);
      await poll(OLD(), 'old');
      at(START + 2 * WAITED);
      // A busy newer worker's renewal: live, though it polls for nothing.
      await availability.observe(NEW().features);
      await h.service.sweep(10);
      expect(requiresAgentCode(await stored(admitted.run))).to.equal(true);
      expect((await poll(NEW(), 'new-2'))?.run.runId).to.equal(admitted.run.runId);
    });

    it('DUR2-W6: a run pinned to agent_code whose worker left fails no_compatible_worker, never waits to its deadline', async () => {
      await poll(NEW(), 'new');
      const admitted = await h.admit(LONG);
      const claimed = await poll(NEW(), 'new');
      expect(claimed?.pinned).to.not.equal(undefined);
      at(START + WAITED);
      await h.service.sweep(10); // the lease lapsed: back to the queue, still pinned
      expect((await stored(admitted.run)).status).to.equal('queued');
      expect(await poll(OLD(), 'old')).to.equal(null);
      at(START + 2 * WAITED);
      await h.service.sweep(10);
      const failed = await stored(admitted.run);
      expect(failed.status).to.equal('failed');
      const stranded = await h.backend.executionStore.strandedRuns('agent_code', 0, 10);
      expect(stranded.map((s) => s.run.runId)).to.not.include(admitted.run.runId);
    });

    it('DUR2-W5/W6 store: stranded runs are queued, waited and still requiring the feature; a relax is a CAS that refuses pinned runs', async () => {
      await poll(NEW(), 'new');
      const fresh = await h.admit(LONG);
      const pinned = await h.admit(LONG);
      const claimed = await poll(NEW(), 'new');
      const pinnedRun = claimed!.run.runId === pinned.run.runId ? pinned : fresh;
      const freshRun = pinnedRun === pinned ? fresh : pinned;
      await h.service.release({
        orgId: claimed!.run.orgId,
        runId: claimed!.run.runId,
        workerId: 'new',
        attemptId: claimed!.run.attemptId!,
        fence: claimed!.run.fence,
        reason: 'drain',
      });
      expect(await h.backend.executionStore.strandedRuns('agent_code', 0, 10)).to.deep.equal([]);
      expect(await poll(OLD(), 'old')).to.equal(null);
      at(START + WAITED);
      const found = await h.backend.executionStore.strandedRuns('agent_code', WAITED - 1, 10);
      expect(found.map((s) => [s.run.runId, s.pinned]).sort()).to.deep.equal(
        [
          [freshRun.run.runId, false],
          [pinnedRun.run.runId, true],
        ].sort(),
      );
      expect(await h.backend.executionStore.strandedRuns('agent_code', WAITED + 1, 10)).to.deep.equal([]);
      const store = h.backend.executionStore;
      const current = await stored(freshRun.run);
      expect(await store.relaxRequirement(current.orgId, current.runId, current.revision - 1, 'agent_code')).to.equal(false);
      const pinnedNow = await stored(pinnedRun.run);
      expect(await store.relaxRequirement(pinnedNow.orgId, pinnedNow.runId, pinnedNow.revision, 'agent_code')).to.equal(false);
      expect(await store.relaxRequirement(current.orgId, current.runId, current.revision, 'agent_code')).to.equal(true);
      expect(await store.relaxRequirement(current.orgId, current.runId, current.revision + 1, 'agent_code')).to.equal(false);
    });
  });

  describe('the resolved_config claim gate (PIN-28, CPD6-10, CPD6-11)', () => {
    const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
    const LONG = { agentKey: 'agent-1', authorization: 'Bearer user', deadlineMs: 3_600_000 };
    const NEW = (): ClaimingWorker => currentWorker();
    // A worker of the release before the pin reached workers.
    const OLD = (): ClaimingWorker =>
      currentWorker({ features: KNOWN_WORKER_FEATURES.filter((f) => f !== RESOLVED_CONFIG_FEATURE) });
    const WAITED = WORKER_FEATURE_WINDOW_MS + AVAILABILITY_CACHE_MS + 1;
    let now: number;
    let withheld: number;
    const at = (t: number): void => {
      now = t;
      h.setNow(t);
    };
    const poll = (worker: ClaimingWorker, workerId: string) =>
      h.service.claimNext({
        role: 'dispatched',
        workerId,
        attemptId: `attempt-${workerId}-${randomUUID()}`,
        leaseDurationMs: 1_000,
        worker,
      });
    const stored = async (run: Pick<HarnessRun, 'orgId' | 'runId'>) =>
      (await h.backend.executionStore.get(run.orgId, run.runId)) as HarnessRun;
    const requiresPin = (run: HarnessRun): boolean =>
      (run.requires?.features ?? []).includes(RESOLVED_CONFIG_FEATURE);
    const pinnedConfig = (pinned: { spec: unknown } | undefined): { digest?: string } | undefined =>
      (pinned?.spec as { extensions?: Record<string, { resolved?: { digest?: string } }> } | undefined)
        ?.extensions?.['x.pipeshub']?.resolved;

    beforeEach(() => {
      at(START);
      withheld = 0;
      h.setRollout(V2);
      const availability = new WorkerFeatureAvailability({
        sightings: new InMemoryWorkerFeatureSightings({ now: () => now }),
        clock: { now: () => now },
        metrics: { live: () => undefined, stranded: () => undefined, withheld: () => void (withheld += 1) },
      });
      h.setWorkerFeatures(availability);
      h.setPolicySteps([
        new PolicyPinning({
          mode: 'on',
          pinner: pinnerOf(async () => A_PIN, (orgId) => revisionsOf(h.backend).heads(orgId)),
          metrics: { mismatch: () => undefined },
          logger: silentHarnessLogger,
          workerFeatures: availability,
        }),
      ]);
    });

    afterEach(() => h.setPolicySteps([]));

    it('PIN-28: a worker without resolved_config never claims a run that requires it; one with it gets the pin in its spec', async () => {
      expect(await poll(NEW(), 'new')).to.equal(null);
      const admitted = await h.admit(LONG);
      expect(requiresPin(admitted.run)).to.equal(true);
      expect(await poll(OLD(), 'old')).to.equal(null);
      const claimed = await poll(NEW(), 'new');
      expect(claimed?.run.runId).to.equal(admitted.run.runId);
      expect(pinnedConfig(claimed?.pinned)?.digest).to.equal(A_PIN.digest);
      expect(withheld).to.equal(0);
    });

    it('CPD6-10: no live worker advertises it: admitted without the requirement, the pin kept on the run and out of the spec', async () => {
      expect(await poll(OLD(), 'old')).to.equal(null);
      const admitted = await h.admit(LONG);
      expect(requiresPin(admitted.run)).to.equal(false);
      expect(withheld).to.equal(1);
      const claimed = await poll(OLD(), 'old');
      expect(claimed?.run.runId).to.equal(admitted.run.runId);
      expect(pinnedConfig(claimed?.pinned)).to.equal(undefined);
      const subject = await h.backend.executionStore.readClaimSubject(admitted.run.orgId, admitted.run.runId);
      expect(subject?.resolved?.digest).to.equal(A_PIN.digest);
    });

    it('CPD6-11: the last worker that reads the pin leaves before a claim: the requirement is dropped, never the run', async () => {
      await poll(NEW(), 'new');
      const admitted = await h.admit(LONG);
      expect(requiresPin(admitted.run)).to.equal(true);
      at(START + WAITED);
      expect(await poll(OLD(), 'old')).to.equal(null);
      at(START + 2 * WAITED);
      await h.service.sweep(10);
      const relaxed = await stored(admitted.run);
      expect([relaxed.status, requiresPin(relaxed)]).to.deep.equal(['queued', false]);
      const claimed = await poll(OLD(), 'old');
      expect(claimed?.run.runId).to.equal(admitted.run.runId);
      expect(pinnedConfig(claimed?.pinned)).to.equal(undefined);
    });
  });

  describe('the resolved_config claim gate with the policy store as the record (CPD10-44)', () => {
    const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
    const LONG = { agentKey: 'agent-1', authorization: 'Bearer user', deadlineMs: 3_600_000 };
    const NEW = (): ClaimingWorker => currentWorker();
    const OLD = (): ClaimingWorker =>
      currentWorker({ features: KNOWN_WORKER_FEATURES.filter((f) => f !== RESOLVED_CONFIG_FEATURE) });
    const WAITED = WORKER_FEATURE_WINDOW_MS + AVAILABILITY_CACHE_MS + 1;
    let now: number;
    let withheld: number;
    const at = (t: number): void => {
      now = t;
      h.setNow(t);
    };
    const poll = (worker: ClaimingWorker, workerId: string) =>
      h.service.claimNext({
        role: 'dispatched',
        workerId,
        attemptId: `attempt-${workerId}-${randomUUID()}`,
        leaseDurationMs: 1_000,
        worker,
      });
    const stored = async (run: Pick<HarnessRun, 'orgId' | 'runId'>) =>
      (await h.backend.executionStore.get(run.orgId, run.runId)) as HarnessRun;
    const requiresPin = (run: HarnessRun): boolean =>
      (run.requires?.features ?? []).includes(RESOLVED_CONFIG_FEATURE);

    beforeEach(() => {
      at(START);
      withheld = 0;
      h.setRollout(V2);
      const availability = new WorkerFeatureAvailability({
        sightings: new InMemoryWorkerFeatureSightings({ now: () => now }),
        clock: { now: () => now },
        metrics: { live: () => undefined, stranded: () => undefined, withheld: () => void (withheld += 1) },
      });
      h.setWorkerFeatures(availability, guardedWorkerFeatures(true));
      h.setPolicySteps([
        new PolicyPinning({
          mode: 'on',
          pinner: pinnerOf(async () => A_PIN, (orgId) => revisionsOf(h.backend).heads(orgId)),
          metrics: { mismatch: () => undefined },
          logger: silentHarnessLogger,
          workerFeatures: { pinOnly: true, live: (feature) => availability.live(feature), metrics: availability.metrics },
        }),
      ]);
    });

    afterEach(() => h.setPolicySteps([]));

    it('no worker reads the pin: the run is admitted requiring it, waits past the window, and is never handed to a worker without it', async () => {
      expect(await poll(OLD(), 'old')).to.equal(null);
      const admitted = await h.admit(LONG);
      expect(requiresPin(admitted.run)).to.equal(true);
      expect(withheld).to.equal(0);
      at(START + WAITED);
      expect(await poll(OLD(), 'old')).to.equal(null);
      at(START + 2 * WAITED);
      await h.service.sweep(10);
      const waiting = await stored(admitted.run);
      expect([waiting.status, requiresPin(waiting)]).to.deep.equal(['queued', true]);
      expect(await poll(OLD(), 'old')).to.equal(null);
      const claimed = await poll(NEW(), 'new');
      expect(claimed?.run.runId).to.equal(admitted.run.runId);
      const spec = claimed?.pinned?.spec as { extensions?: Record<string, { resolved?: { digest?: string } }> } | undefined;
      expect(spec?.extensions?.['x.pipeshub']?.resolved?.digest).to.equal(A_PIN.digest);
    });
  });

  describe('schema-gated claims (P1b RU-2)', () => {
    const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
    const agentRun = { agentKey: 'agent-1', authorization: 'Bearer user' };
    const claimAs = (
      run: Pick<HarnessRun, 'orgId' | 'runId'>,
      worker: ClaimingWorker,
      workerId = `w-${String(worker.foldVersion)}`,
    ): Promise<ClaimedRun> =>
      h.service.claim({
        orgId: run.orgId,
        runId: run.runId,
        workerId,
        attemptId: `attempt-${workerId}-${randomUUID()}`,
        leaseDurationMs: 1_000,
        worker,
      });
    const nextFor = (worker: ClaimingWorker, workerId = 'w-next') =>
      h.service.claimNext({
        role: 'dispatched',
        workerId,
        attemptId: `attempt-${workerId}-${randomUUID()}`,
        leaseDurationMs: 1_000,
        worker,
      });
    const release = (run: HarnessRun, reason: string) =>
      h.service.release({
        orgId: run.orgId,
        runId: run.runId,
        workerId: run.workerId ?? '',
        attemptId: run.attemptId ?? '',
        fence: run.fence,
        reason,
      });
    const stored = async (run: Pick<HarnessRun, 'orgId' | 'runId'>) =>
      (await h.backend.executionStore.get(run.orgId, run.runId)) as HarnessRun;
    /** Claimed by a worker of the next release, then handed back: it needs that release now. */
    const touchedByNewer = async (): Promise<HarnessRun> => {
      const admitted = await h.admit(agentRun);
      const { run } = await claimAs(admitted.run, newerWorker());
      await release(run, 'drain');
      return stored(run);
    };

    beforeEach(() => h.setRollout(V2));

    it('RU-2c: admission records what the run needs; a claim raises it to the claiming worker\'s fold', async () => {
      const agent = await h.admit(agentRun);
      expect(agent.run.requires).to.deep.equal({
        foldVersion: RUN_SPEC_FOLD_VERSION,
        features: [],
      });
      const chat = await h.admit({
        input: {
          query: 'hello',
          recordIds: [],
          filters: {},
          attachments: [],
          model: { chatMode: 'internal_search' },
          caller: { email: 'someone@example.com' },
        },
      });
      expect(chat.run.requires?.features).to.deep.equal(['caller', 'chat']);

      await claimAs(agent.run, newerWorker());
      const after = await stored(agent.run);
      expect(after.requires?.foldVersion).to.equal(RUN_SPEC_FOLD_VERSION + 1);
      expect(after.requires?.schemaVersion).to.equal(RUN_SPEC_SCHEMA_VERSION);
      expect([...(after.attemptFeatures ?? [])].sort()).to.deep.equal([
        'agent_code',
        'approval_edits',
        'assistant_agent',
        'caller',
        'chat',
        'child_asks',
        'child_models',
        'child_runs',
        'code_task_tool',
        'command_input',
        'compact_request',
        'deep_mode',
        'deliverable_pin',
        'descriptor_pins',
        'engine_inputs',
        'environment_pin',
        'executor_engine',
        'governed_memory',
        'inherited_grants',
        'inherited_labels',
        'keyword_search_budget',
        'knowledge_selection',
        'mcp_carried_pins',
        'mcp_first_use',
        'model_fallback',
        'output_locale',
        'region_aware',
        'release_pin',
        'resolved_config',
        'run_gates',
        'run_surface',
        'sandbox_command',
        'session_roles',
        'steering',
        'steering_by',
        'steering_references',
        'trace_content',
        'trigger_context',
        'turn_labels',
        'vendor_signin',
        'workspace_forks',
      ]);
    });

    it('RU-2d: claim-next skips runs this worker cannot run and takes the next it can', async () => {
      const hidden: HarnessRun[] = [];
      for (let i = 0; i < 6; i += 1) hidden.push(await touchedByNewer());
      const fresh = await h.admit(agentRun);
      const claimed = await nextFor(currentWorker());
      expect(claimed?.run.runId).to.equal(fresh.run.runId);
      for (const run of hidden) {
        expect((await stored(run)).status).to.equal('queued');
      }
    });

    it('RU-2d: a claim by id (a hint) is refused WORKER_INCOMPATIBLE and claims nothing', async () => {
      const run = await touchedByNewer();
      expect(await conflictCode(claimAs(run, currentWorker()))).to.equal(
        'WORKER_INCOMPATIBLE',
      );
      const after = await stored(run);
      expect([after.status, after.fence]).to.deep.equal(['queued', run.fence]);
      expect(after.waitingForWorker?.requires.foldVersion).to.equal(
        RUN_SPEC_FOLD_VERSION + 1,
      );
      const taken = await claimAs(run, newerWorker());
      expect(taken.run.status).to.equal('running');
      expect((await stored(run)).waitingForWorker ?? null).to.equal(null);
    });

    it('RU-2d: the claim\'s own update re-checks, so a requirement raised after the read holds', async () => {
      const run = await touchedByNewer();
      // The store alone, as if the service's read had seen the run before the newer claim.
      expect(
        await conflictCode(
          h.backend.executionStore.claim({
            orgId: run.orgId,
            runId: run.runId,
            workerId: 'w-stale',
            attemptId: 'attempt-stale',
            leaseDurationMs: 1_000,
            worker: currentWorker(),
          }),
        ),
      ).to.equal('WORKER_INCOMPATIBLE');
      expect((await stored(run)).status).to.equal('queued');
    });

    it('RU-2d: a worker that advertises nothing gets no v2 run', async () => {
      const admitted = await h.admit(agentRun);
      expect(await nextFor(UNADVERTISED_WORKER)).to.equal(null);
      expect(await conflictCode(claimAs(admitted.run, UNADVERTISED_WORKER))).to.equal(
        'WORKER_INCOMPATIBLE',
      );
      expect((await stored(admitted.run)).status).to.equal('queued');
    });

    it('MF-1 (F2): a v3 run never goes to a worker that reads only v2; one that reads v3 takes it', async () => {
      const admitted = await h.admit(agentRun);
      expect(await nextFor(v2OnlyWorker())).to.equal(null);
      expect(await conflictCode(claimAs(admitted.run, v2OnlyWorker()))).to.equal(
        'WORKER_INCOMPATIBLE',
      );
      const waiting = await stored(admitted.run);
      expect(waiting.status).to.equal('queued');
      expect(waiting.waitingForWorker).to.not.equal(undefined);
      expect(waiting.waitingForWorker).to.not.equal(null);

      const taken = await nextFor(currentWorker());
      expect(taken?.run.runId).to.equal(admitted.run.runId);
      expect(taken?.pinned?.spec).to.deep.include({
        schemaVersion: RUN_SPEC_SCHEMA_VERSION,
        executor: { kind: 'loop' },
      });
      expect((await stored(admitted.run)).requires?.schemaVersion).to.equal(
        RUN_SPEC_SCHEMA_VERSION,
      );
    });

    it('MF-2 (F2): a run the previous release pinned at v2 goes to a v2-only worker, served unconverted', async () => {
      // The previous release's Node: the same resolution, without executor, at v2.
      const pinnedAtV2: RunSpecResolver = (subject, capabilities) => {
        const pinned = pinRunSpec(subject, capabilities);
        const { executor: _executor, ...rest } = pinned.spec;
        _executor;
        const spec = { ...rest, schemaVersion: 2 } as RunSpec;
        return { ...pinned, spec, specDigest: canonicalDigest(spec) };
      };
      const previousNode = new ExecutionService({
        store: h.backend.executionStore,
        inputs: h.backend.inputs ?? null,
        journal: h.backend.journal,
        journals: new JournalService(h.backend.payloads, testCipher()),
        capabilities: DURABLE_CAPABILITIES,
        resolveSpec: pinnedAtV2,
      });
      const admitted = await h.admit(agentRun);
      const first = await previousNode.claim({
        orgId: admitted.run.orgId,
        runId: admitted.run.runId,
        workerId: 'w-old-node',
        attemptId: `attempt-old-node-${randomUUID()}`,
        leaseDurationMs: 1_000,
        worker: currentWorker(),
      });
      await release(first.run, 'drain');
      expect((await stored(admitted.run)).requires?.schemaVersion).to.equal(2);

      const taken = await nextFor(v2OnlyWorker(), 'w-v2-only');
      expect(taken?.run.runId).to.equal(admitted.run.runId);
      const served = taken?.pinned?.spec as Record<string, unknown>;
      expect(served['schemaVersion']).to.equal(2);
      expect(served).to.not.have.property('executor');
      expect(taken?.pinned?.specDigest).to.equal(canonicalDigest(served));
      expect(readPinnedSpec(served).executor).to.deep.equal({ kind: 'loop' });
    });

    it('RU-2e: a run no claimer can run waits, marked once, and is never failed by the filter', async () => {
      const run = await touchedByNewer();
      const before = await stored(run);
      expect(before.waitingForWorker ?? null).to.equal(null);
      expect(await nextFor(currentWorker())).to.equal(null);
      const marked = await stored(run);
      expect(marked.status).to.equal('queued');
      expect(marked.waitingForWorker?.requires.foldVersion).to.equal(
        RUN_SPEC_FOLD_VERSION + 1,
      );
      expect(
        await h.backend.executionStore.markWaiting(
          'dispatched',
          T_CLAIM_MS,
          10,
          currentWorker(),
        ),
      ).to.deep.equal([]);
      expect((await stored(run)).waitingForWorker).to.deep.equal(
        marked.waitingForWorker,
      );
      const taken = await nextFor(newerWorker());
      expect(taken?.run.runId).to.equal(run.runId);
    });

    it('RU-2f: queued input needs a worker that folds it; a run held by one that does not refuses it', async () => {
      const admitted = await h.admit(agentRun);
      await claimAs(admitted.run, currentWorker({ features: [] }));
      expect(
        await conflictCode(
          h.admit({ ...agentRun, sessionId: admitted.sessionId }),
        ),
      ).to.equal('RUN_ACTIVE');

      const other = await h.admit(agentRun);
      const { run } = await claimAs(other.run, currentWorker());
      const steered = await h.admit({ ...agentRun, sessionId: other.sessionId });
      expect(steered.queued).to.deep.equal({ messageSeq: 1 });
      expect((await stored(run)).requires?.features).to.include('steering');
      await release(run, 'drain');
      expect(
        await conflictCode(
          claimAs(run, currentWorker({ features: ['caller', 'chat'] })),
        ),
      ).to.equal('WORKER_INCOMPATIBLE');
    });

    it('RU-2g (X1): queued input naming attachments or records needs a worker that renders them', async () => {
      h.setRecordAccess(
        new InMemoryRecordReadAccess()
          .grant('Bearer user', 'rec-file', 'rec-kb')
          .content('rec-file', 'vr-file'),
      );
      const withRefs = {
        input: {
          query: 'compare with the attached plan',
          recordIds: ['rec-kb'],
          filters: {},
          attachments: [{ recordId: 'rec-file', virtualRecordId: 'vr-file' }],
          model: { chatMode: 'auto' },
        },
      };
      const textOnly = currentWorker({ features: ['caller', 'chat', 'steering', 'steering_by'] });

      const held = await h.admit(agentRun);
      await claimAs(held.run, textOnly);
      expect(
        await conflictCode(
          h.admit({ ...agentRun, sessionId: held.sessionId, ...withRefs }),
        ),
      ).to.equal('RUN_ACTIVE');
      const text = await h.admit({ ...agentRun, sessionId: held.sessionId });
      expect(text.queued).to.deep.equal({ messageSeq: 1 });

      const other = await h.admit(agentRun);
      const { run } = await claimAs(other.run, currentWorker());
      const steered = await h.admit({
        ...agentRun,
        sessionId: other.sessionId,
        ...withRefs,
      });
      expect(steered.queued).to.deep.equal({ messageSeq: 1 });
      expect((await stored(run)).requires?.features).to.include.members([
        'steering',
        'steering_references',
      ]);
      await release(run, 'drain');
      expect(await conflictCode(claimAs(run, textOnly))).to.equal(
        'WORKER_INCOMPATIBLE',
      );
    });

    it('QB-2 (SS1): a message names its sender, so a worker from before SS1 cannot take it', async () => {
      const beforeSS1 = currentWorker({ features: ['caller', 'chat', 'steering', 'steering_references'] });
      const held = await h.admit(agentRun);
      await claimAs(held.run, beforeSS1);
      expect(await conflictCode(h.admit({ ...agentRun, sessionId: held.sessionId }))).to.equal('RUN_ACTIVE');

      const queued = await h.admit(agentRun);
      await h.admit({ ...agentRun, sessionId: queued.sessionId });
      expect((await stored(queued.run)).requires?.features).to.include.members(['steering', 'steering_by']);
      expect(await conflictCode(claimAs(queued.run, beforeSS1))).to.equal('WORKER_INCOMPATIBLE');
    });

    it('QB-3 (SS1): a later run of a session whose stream holds queued messages needs what folding them needs', async () => {
      h.setRecordAccess(
        new InMemoryRecordReadAccess().grant('Bearer user', 'rec-kb'),
      );
      const first = await h.admit(agentRun);
      await h.admit({
        ...agentRun,
        sessionId: first.sessionId,
        input: { query: 'and this record', recordIds: ['rec-kb'], filters: {}, attachments: [], model: { chatMode: 'auto' } },
      });
      const ended = await stored(first.run);
      await h.service.control({
        commandId: `cancel-${ended.runId}`,
        dispatchCommandId: `dispatch-cancel-${ended.runId}`,
        orgId: ended.orgId,
        runId: ended.runId,
        expectedRevision: ended.revision,
        action: 'cancel',
      });
      const later = await h.admit({ ...agentRun, sessionId: first.sessionId, idempotencyKey: 'later' });
      expect(later.queued).to.equal(undefined);
      expect(later.run.runId).to.not.equal(first.run.runId);
      expect(later.run.requires?.features).to.deep.equal([
        'steering',
        'steering_by',
        'steering_references',
      ]);
      expect(
        await conflictCode(claimAs(later.run, currentWorker({ features: ['caller', 'chat', 'steering', 'steering_references'] }))),
      ).to.equal('WORKER_INCOMPATIBLE');
      // A session nobody steered asks nothing of its next run.
      expect((await h.admit(agentRun)).run.requires?.features).to.deep.equal([]);
    });

    it('RU-1a: a claim released before its runtime was built (drain) takes back its attempt', async () => {
      const admitted = await h.admit(agentRun);
      const first = await claimAs(admitted.run, currentWorker());
      expect(first.run.attempts).to.equal(1);
      await release(first.run, 'drain');
      expect((await stored(first.run)).attempts).to.equal(0);
      const second = await claimAs(admitted.run, currentWorker());
      expect(second.run.attempts).to.equal(1);
      await release(second.run, 'drain_timeout');
      expect((await stored(second.run)).attempts).to.equal(1);
    });
  });
}
