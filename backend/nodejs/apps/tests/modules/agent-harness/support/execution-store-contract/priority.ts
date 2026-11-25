/**
 * ExecutionStore contract, part of `describeExecutionStoreContract`. G-45 / GA-24: priority classes on the run
 * claim queue.
 *
 * Interactive runs waited behind every scheduled run admitted before them: the queue was oldest-first, so a
 * person's turn sat behind a burst of schedule firings. Claim-next now serves the highest class first, oldest first
 * within it; every fifth claim of a Node process (AGENT_HARNESS_CLAIM_FLOOR_EVERY) takes the oldest due run of any
 * class, so background work keeps moving; a full worker that will preempt asks only above the class it preempts.
 */
import { expect } from 'chai';
import { randomUUID } from 'crypto';
import { InteractionService } from '../../../../../src/modules/agent-harness/application/interaction-service';
import { ChildRunService } from '../../../../../src/modules/agent-harness/application/child-run-service';
import { JournalService } from '../../../../../src/modules/agent-harness/application/journal-service';
import type { HarnessRun } from '../../../../../src/modules/agent-harness/domain/execution';
import type { PriorityClass } from '../../../../../src/modules/agent-harness/domain/priority-class';
import { workerRun } from '../../../../../src/modules/agent-harness/domain/steering';
import { agentDefinition } from '../agent-definition';
import { testCipher } from '../data-protection';
import { HARNESS_ENABLED } from '../rollout';
import { workerEvent } from '../journal-fixtures';
import { ContractHarness, START } from './harness';

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const INTERACTION = 'ix.approve.t2.0';

export function describePriorityCases(harness: () => ContractHarness): void {
  describe('G-45: priority classes on the claim queue', () => {
    let h: ContractHarness;
    let firings = 0;
    beforeEach(() => {
      h = harness();
      h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 as const });
    });

    const agentRun = { agentKey: 'agent-1', authorization: 'Bearer user', deadlineMs: HOUR };
    const interactive = async (): Promise<HarnessRun> => (await h.admit(agentRun)).run;
    const scheduled = async (): Promise<HarnessRun> => {
      firings += 1;
      return (
        await h.admit({
          ...agentRun,
          trigger: { scheduleId: 's-1', firingId: `f-${String(firings)}`, slotKey: `slot-${String(firings)}` },
        })
      ).run;
    };
    const claimNext = async (aboveClass?: PriorityClass): Promise<HarnessRun | null> => {
      const workerId = `w-${randomUUID()}`;
      const claimed = await h.service.claimNext({
        role: 'dispatched',
        workerId,
        attemptId: `attempt-${workerId}`,
        leaseDurationMs: 10 * MINUTE,
        ...(aboveClass !== undefined ? { aboveClass } : {}),
      });
      return claimed?.run ?? null;
    };
    /** Each admission one millisecond after the last, so oldest-first has one answer. */
    const later = (): void => {
      h.setNow(h.clock.now() + 1);
    };

    it('GA-24: an interactive run is claimed before every scheduled run admitted ahead of it', async () => {
      const firingsAhead: HarnessRun[] = [];
      for (let index = 0; index < 3; index += 1) {
        firingsAhead.push(await scheduled());
        later();
      }
      const person = await interactive();
      expect(firingsAhead.map((run) => run.priorityClass)).to.deep.equal(['background', 'background', 'background']);
      expect(person.priorityClass).to.equal('interactive');
      expect((await claimNext())?.runId).to.equal(person.runId);
      // Then the scheduled runs, oldest first.
      expect((await claimNext())?.runId).to.equal(firingsAhead[0]?.runId);
      expect((await claimNext())?.runId).to.equal(firingsAhead[1]?.runId);
    });

    it('GA-24 floor: every fifth claim takes the oldest due run of any class, so background work is not starved', async () => {
      const firing = await scheduled();
      later();
      const people: HarnessRun[] = [];
      for (let index = 0; index < 6; index += 1) {
        people.push(await interactive());
        later();
      }
      const order: string[] = [];
      for (let index = 0; index < 6; index += 1) {
        order.push((await claimNext())?.runId ?? 'none');
      }
      expect(order).to.deep.equal([
        people[0]?.runId,
        people[1]?.runId,
        people[2]?.runId,
        people[3]?.runId,
        firing.runId,
        people[4]?.runId,
      ]);
    });

    it('GA-24 preemption: a full worker asking above a class gets only a strictly higher one, never the floor', async () => {
      await scheduled();
      later();
      // Five asks, each of which would have been a floor claim at some point: none takes the background run.
      for (let index = 0; index < 5; index += 1) {
        expect(await claimNext('background')).to.equal(null);
      }
      const person = await interactive();
      expect(await claimNext('interactive')).to.equal(null);
      expect((await claimNext('background'))?.runId).to.equal(person.runId);
    });

    it('GA-24: a scheduled run a person answered rises to approval_resume, ahead of background runs older than it', async () => {
      const asked = await h.claim(await scheduled(), 'worker-a', 20 * MINUTE);
      later();
      const firing = await scheduled();
      const commit = (commandId: string, transition?: Record<string, unknown>, events: Parameters<typeof h.service.commit>[0]['events'] = []) =>
        h.service.commit({
          commandId,
          orgId: asked.orgId,
          runId: asked.runId,
          workerId: 'worker-a',
          attemptId: asked.attemptId as string,
          fence: asked.fence,
          events,
          legacyEvents: [],
          ...(transition !== undefined ? { transition: transition as never } : {}),
          outbox: [],
        });
      await commit('ask', undefined, [
        workerEvent('interaction.requested', { interactionId: INTERACTION, kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` }),
      ]);
      await commit('suspend', { status: 'waiting_approval', detail: { kind: 'suspend', reason: 'approval', interactionId: INTERACTION } });
      h.setNow(START + 10 * MINUTE);
      const resumed = (
        await new InteractionService(h.service).resolve({
          orgId: asked.orgId,
          runId: asked.runId,
          interactionId: INTERACTION,
          decision: 'approved',
          channel: 'web',
          by: 'p_actor',
          commandId: 'decide',
        })
      ).run;
      expect([resumed.status, resumed.priorityClass, resumed.priorityRank]).to.deep.equal(['queued', 'approval_resume', 1]);
      // The firing was due first; the answered run goes first all the same.
      expect((await claimNext())?.runId).to.equal(asked.runId);
      expect((await claimNext())?.runId).to.equal(firing.runId);
      // The worker's run model is strict: the class rides beside the run in the claim answer, never inside it.
      expect(workerRun(resumed)).not.to.have.property('priorityClass');
      expect(workerRun(resumed)).not.to.have.property('priorityRank');
    });

    it('R11-PRI-1: a scheduled run a person answers while it still holds its worker (the approval hold) rises too', async () => {
      const asked = await h.claim(await scheduled(), 'worker-a', 20 * MINUTE);
      await h.service.commit({
        commandId: 'ask',
        orgId: asked.orgId,
        runId: asked.runId,
        workerId: 'worker-a',
        attemptId: asked.attemptId as string,
        fence: asked.fence,
        events: [
          workerEvent('interaction.requested', { interactionId: INTERACTION, kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` }),
        ],
        legacyEvents: [],
        outbox: [],
      });
      const answered = (
        await new InteractionService(h.service).resolve({
          orgId: asked.orgId,
          runId: asked.runId,
          interactionId: INTERACTION,
          decision: 'approved',
          channel: 'web',
          by: 'p_actor',
          commandId: 'decide-in-attempt',
        })
      ).run;
      // Still the worker's, and the class the person's wait gives it: a later claim of the run is served first.
      expect([answered.status, answered.workerId, answered.priorityClass, answered.priorityRank]).to.deep.equal([
        'running', 'worker-a', 'approval_resume', 1,
      ]);
    });

    it('R11D-PRI-1: the answered run keeps its attempt; its raised class is served at its next claim', async () => {
      const asked = await h.claim(await scheduled(), 'worker-a', 20 * MINUTE);
      expect(asked.priorityClass).to.equal('background');
      later();
      const firing = await scheduled();
      await h.service.commit({
        commandId: 'ask',
        orgId: asked.orgId,
        runId: asked.runId,
        workerId: 'worker-a',
        attemptId: asked.attemptId as string,
        fence: asked.fence,
        events: [
          workerEvent('interaction.requested', { interactionId: INTERACTION, kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` }),
        ],
        legacyEvents: [],
        outbox: [],
      });
      await new InteractionService(h.service).resolve({
        orgId: asked.orgId,
        runId: asked.runId,
        interactionId: INTERACTION,
        decision: 'approved',
        channel: 'web',
        by: 'p_actor',
        commandId: 'decide-in-attempt',
      });
      // The class is not pushed into the running attempt: the attempt and its fence are unchanged.
      const during = await h.service.get(asked.orgId, asked.runId);
      expect([during?.workerId, during?.attemptId, during?.fence]).to.deep.equal(['worker-a', asked.attemptId, asked.fence]);
      // The attempt ends (its lease runs out and the sweeper re-queues it); the next claim takes the answered run
      // first, at its raised class, ahead of the older firing.
      h.setNow(h.clock.now() + 21 * MINUTE);
      await h.service.sweep(10);
      const next = await claimNext();
      expect([next?.runId, next?.priorityClass]).to.deep.equal([asked.runId, 'approval_resume']);
      expect((await claimNext())?.runId).to.equal(firing.runId);
    });

    it('GA-24: a child takes its parent’s class, so a scheduled run’s children do not jump the queue', async () => {
      h.setAgentDefinition((agentKey) => agentDefinition({ agentKey, maxChildren: 3 }));
      const store = h.backend.childRuns;
      if (store === undefined) expect.fail('backend has no child store');
      const children = new ChildRunService({
        runs: h.backend.executionStore,
        children: store,
        journals: new JournalService(h.backend.payloads, testCipher()),
      });
      const spawnFrom = async (parent: HarnessRun): Promise<HarnessRun> => {
        const { childRunId } = await children.spawn(
          {
            orgId: parent.orgId,
            runId: parent.runId,
            workerId: parent.workerId as string,
            attemptId: parent.attemptId as string,
            fence: parent.fence,
          },
          { invocationId: 't1.0', goal: 'summarise the night’s tickets', grants: {} },
        );
        const child = await h.service.get(parent.orgId, childRunId);
        if (child === null) expect.fail('child not found');
        return child;
      };
      const background = await spawnFrom(await h.claim(await scheduled(), 'worker-a', 20 * MINUTE));
      const foreground = await spawnFrom(await h.claim(await interactive(), 'worker-b', 20 * MINUTE));
      expect([background.priorityClass, foreground.priorityClass]).to.deep.equal(['background', 'interactive']);
    });
  });
}
