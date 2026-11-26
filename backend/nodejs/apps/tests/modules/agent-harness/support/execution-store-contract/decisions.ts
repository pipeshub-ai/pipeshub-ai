/**
 * ExecutionStore contract, part of `describeExecutionStoreContract`. DECIDE409: decisions commute.
 *
 * The owner's "Allow all 7" posted seven decisions on one run at once; each was a compare-and-set on the run's
 * revision, the worker's commits moved it too, and after five tries one answered 409 "Run revision changed"
 * ("Your decision could not be sent"). A decision is now checked against the run's status and its own
 * interaction, so decisions on different interactions, and the worker's progress, never refuse each other; and
 * "Allow all" is one command that journals every decision in one transaction.
 */
import { expect } from 'chai';
import { InteractionService } from '../../../../../src/modules/agent-harness/application/interaction-service';
import { InMemoryRunWakeup } from '../../../../../src/modules/agent-harness/adapters/in-memory-interactions';
import {
  CommitBatch,
  HarnessRun,
  RunTransition,
} from '../../../../../src/modules/agent-harness/domain/execution';
import { HARNESS_ENABLED } from '../rollout';
import { workerEvent } from '../journal-fixtures';
import { ContractHarness, conflictCode } from './harness';

const SEVEN = Array.from({ length: 7 }, (_, i) => `ix.approve.t2.${String(i)}`);

export function describeDecisionCases(harness: () => ContractHarness): void {
  describe('DECIDE409: decisions on one run commute', () => {
    let h: ContractHarness;
    beforeEach(() => {
      h = harness();
      h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 as const });
    });
    const agentRun = { agentKey: 'agent-1', authorization: 'Bearer user' };
    const CLAIMED_HEAD = 3;
    const commit = (
      run: HarnessRun,
      commandId: string,
      events: CommitBatch['events'],
      transition?: RunTransition,
    ) =>
      h.service.commit({
        commandId,
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'worker-a',
        attemptId: 'attempt-worker-a',
        fence: run.fence,
        events,
        legacyEvents: [],
        ...(transition !== undefined ? { transition } : {}),
        outbox: [],
      });
    /** A claimed run holding the batch for approval, as `tool.approval_hold` does. */
    const holding = async (ids: readonly string[] = SEVEN) => {
      const admitted = await h.admit(agentRun);
      const { run } = await h.service.claim({
        orgId: admitted.run.orgId,
        runId: admitted.run.runId,
        workerId: 'worker-a',
        attemptId: 'attempt-worker-a',
        leaseDurationMs: 1_000,
      });
      await commit(
        run,
        'worker-a:ask:1',
        ids.map((interactionId) =>
          workerEvent('interaction.requested', {
            interactionId,
            kind: 'approval',
            bindingDigest: `sha256:${'1'.repeat(64)}`,
          }),
        ),
      );
      return run;
    };
    const resolved = async (run: HarnessRun) =>
      (
        await h.backend.journal.readStream(
          run.orgId,
          run.sessionId,
          CLAIMED_HEAD,
          200,
        )
      )
        .filter((event) => event.type === 'interaction.resolved')
        .map((event) => event.data?.['interactionId']);
    const decide = (
      service: InteractionService,
      run: HarnessRun,
      interactionId: string,
      commandId: string,
    ) =>
      service.resolve({
        orgId: run.orgId,
        runId: run.runId,
        interactionId,
        decision: 'approved',
        channel: 'web',
        by: 'p_actor',
        commandId,
      });
    const decideAll = (
      service: InteractionService,
      run: HarnessRun,
      interactionIds: readonly string[],
      commandId: string,
    ) =>
      service.resolveAll({
        orgId: run.orgId,
        runId: run.runId,
        interactionIds,
        decision: 'approved',
        channel: 'web',
        by: 'p_actor',
        commandId,
      });

    it('DC-1: seven decisions on seven interactions of one run, sent at once, all land', async () => {
      const run = await holding();
      const service = new InteractionService(
        h.service,
        new InMemoryRunWakeup(),
      );
      const outcomes = await Promise.allSettled(
        SEVEN.map((ix, i) => decide(service, run, ix, `decide-${String(i)}`)),
      );
      expect(outcomes.filter((o) => o.status === 'rejected')).to.deep.equal([]);
      expect([...(await resolved(run))].sort()).to.deep.equal(SEVEN);
      expect(
        (await h.service.get(run.orgId, run.runId))?.openInteractions,
      ).to.deep.equal([]);
    });

    it('DC-2: a decision read before the worker committed still lands', async () => {
      const run = await holding();
      const read = await h.service.get(run.orgId, run.runId);
      // The worker's own progress (an intent, a heartbeat commit) moves the revision under the decision.
      await commit(run, 'worker-a:intent:2', []);
      const receipt = await h.service.control({
        commandId: 'decide-late',
        dispatchCommandId: 'decide-late:dispatch',
        orgId: run.orgId,
        runId: run.runId,
        expectedRevision: read!.revision,
        expectedStatus: read!.status,
        action: 'resolve',
        by: 'p_actor',
        resolution: {
          interactionId: SEVEN[3]!,
          decision: 'approved',
          channel: 'web',
        },
      });
      expect(receipt.run.status).to.equal('running');
      expect(await resolved(run)).to.deep.equal([SEVEN[3]]);
    });

    it('DC-3: decisions racing the worker’s commits all land', async () => {
      const run = await holding();
      const service = new InteractionService(
        h.service,
        new InMemoryRunWakeup(),
      );
      const outcomes = await Promise.allSettled([
        ...SEVEN.map((ix, i) =>
          decide(service, run, ix, `decide-${String(i)}`),
        ),
        ...[2, 3, 4].map((n) =>
          commit(run, `worker-a:intent:${String(n)}`, []),
        ),
      ]);
      expect(outcomes.filter((o) => o.status === 'rejected')).to.deep.equal([]);
      expect(await resolved(run)).to.have.length(7);
    });

    it('DC-4: a decision whose run changed status under it re-reads and resumes it', async () => {
      const run = await holding([SEVEN[0]!]);
      const read = await h.service.get(run.orgId, run.runId);
      await commit(run, 'worker-a:suspend:2', [], {
        status: 'waiting_approval',
        detail: {
          kind: 'suspend',
          reason: 'approval',
          interactionId: SEVEN[0]!,
        },
      });
      const stale = () =>
        h.service.control({
          commandId: 'decide-stale',
          dispatchCommandId: 'decide-stale:dispatch',
          orgId: run.orgId,
          runId: run.runId,
          expectedRevision: read!.revision,
          expectedStatus: read!.status,
          action: 'resolve',
          by: 'p_actor',
          resolution: {
            interactionId: SEVEN[0]!,
            decision: 'approved',
            channel: 'web',
          },
        });
      // Its events were prepared for a running run: no resume. The store refuses it rather than strand the run.
      expect(await conflictCode(stale())).to.equal('REVISION_CONFLICT');
      const receipt = await decide(
        new InteractionService(h.service),
        run,
        SEVEN[0]!,
        'decide-fresh',
      );
      expect(receipt.run.status).to.equal('queued');
    });

    it('DC-5: "Allow all 7" is one command: seven decisions, one transaction, one wake', async () => {
      const run = await holding();
      const wakeup = new InMemoryRunWakeup();
      const service = new InteractionService(h.service, wakeup);
      const receipt = await decideAll(service, run, SEVEN, 'decide-all');
      expect(receipt.run.openInteractions).to.deep.equal([]);
      expect(await resolved(run)).to.deep.equal(SEVEN);
      expect(wakeup.woken).to.deep.equal([run.runId]);
      // A retried request replays: nothing is journaled twice.
      expect(await decideAll(service, run, SEVEN, 'decide-all')).to.deep.equal(
        receipt,
      );
      expect(await resolved(run)).to.have.length(7);
    });

    it('DC-6: a batch naming an interaction already decided decides none of it', async () => {
      const run = await holding();
      const service = new InteractionService(h.service);
      await decide(service, run, SEVEN[2]!, 'decide-one');
      expect(
        await conflictCode(decideAll(service, run, SEVEN, 'decide-all')),
      ).to.equal('INTERACTION_NOT_OPEN');
      expect(await resolved(run)).to.deep.equal([SEVEN[2]]);
    });

    it('DC-7: a batch on a suspended run resumes it once', async () => {
      const run = await holding();
      await commit(run, 'worker-a:suspend:2', [], {
        status: 'waiting_approval',
        detail: {
          kind: 'suspend',
          reason: 'approval',
          interactionId: SEVEN[0]!,
        },
      });
      const receipt = await decideAll(
        new InteractionService(h.service),
        run,
        SEVEN,
        'decide-all',
      );
      expect(receipt.run.status).to.equal('queued');
      const tail = (
        await h.backend.journal.readStream(
          run.orgId,
          run.sessionId,
          CLAIMED_HEAD,
          200,
        )
      ).map((e) => e.type);
      expect(tail.filter((t) => t === 'run.resumed')).to.have.length(1);
      expect(tail.at(-1)).to.equal('run.resumed');
    });
  });
}
