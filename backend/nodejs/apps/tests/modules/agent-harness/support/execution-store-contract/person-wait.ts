/**
 * ExecutionStore contract, part of `describeExecutionStoreContract`. FIX-P1-F3: time a run spends waiting for a
 * person does not count against its working deadline.
 *
 * The sweeper failed a run waiting for an approval once its one-hour deadline passed, so an approval left over lunch
 * ended the task. A run waiting for a person now keeps the working time it had left and stays answerable for
 * `HARNESS_WAIT_FOR_PERSON_DAYS` (7 by default); the answer gives the working time back. Past the window the sweeper
 * ends it, and the person reads that nobody approved in time.
 *
 * WAITCHILD (PW-5 to PW-11): a parent joined on a child that waits for a person holds its working time with it, and
 * gets it back when the child is answered or ends, so a code task that asks overnight no longer fails its parent.
 */
import { expect } from 'chai';
import { InteractionService } from '../../../../../src/modules/agent-harness/application/interaction-service';
import {
  DEFAULT_WAIT_FOR_PERSON_DAYS,
  HarnessRun,
  WAIT_FOR_PERSON_ENV,
  waitForPersonMs,
} from '../../../../../src/modules/agent-harness/domain/execution';
import { workerRun } from '../../../../../src/modules/agent-harness/domain/steering';
import { ChildRunService } from '../../../../../src/modules/agent-harness/application/child-run-service';
import { JournalService } from '../../../../../src/modules/agent-harness/application/journal-service';
import { ChildResult } from '../../../../../src/modules/agent-harness/domain/child-runs';
import { inlineContentRef } from '../../../../../src/modules/agent-harness/domain/run-content';
import { agentDefinition } from '../agent-definition';
import { testCipher } from '../data-protection';
import { HARNESS_ENABLED } from '../rollout';
import { workerEvent } from '../journal-fixtures';
import { ContractHarness, START, conflictCode } from './harness';

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const ASKED_AT = START + 10 * MINUTE;
const INTERACTION = 'ix.approve.t2.0';

export function describePersonWaitCases(harness: () => ContractHarness): void {
  describe('FIX-P1-F3: waiting for a person does not use up the run', () => {
    let h: ContractHarness;
    let saved: string | undefined;
    beforeEach(() => {
      h = harness();
      h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 as const });
      saved = process.env[WAIT_FOR_PERSON_ENV];
      delete process.env[WAIT_FOR_PERSON_ENV];
    });
    afterEach(() => {
      if (saved === undefined) delete process.env[WAIT_FOR_PERSON_ENV];
      else process.env[WAIT_FOR_PERSON_ENV] = saved;
    });

    /** An hour-long chat run that asks for an approval ten minutes in and suspends for it. */
    const waiting = async (): Promise<HarnessRun> => {
      const admitted = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user', deadlineMs: HOUR });
      const run = await h.claim(admitted.run, 'worker-a', 20 * MINUTE);
      h.setNow(ASKED_AT);
      const commit = (commandId: string, events: Parameters<typeof h.service.commit>[0]['events'], suspend: boolean) =>
        h.service.commit({
          commandId,
          orgId: run.orgId,
          runId: run.runId,
          workerId: 'worker-a',
          attemptId: 'attempt-worker-a',
          fence: run.fence,
          events,
          legacyEvents: [],
          ...(suspend
            ? { transition: { status: 'waiting_approval', detail: { kind: 'suspend', reason: 'approval', interactionId: INTERACTION } } }
            : {}),
          outbox: [],
        });
      await commit('worker-a:ask', [
        workerEvent('interaction.requested', { interactionId: INTERACTION, kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` }),
      ], false);
      const suspended = (await commit('worker-a:suspend', [], true)).run;
      expect(suspended.status).to.equal('waiting_approval');
      return suspended;
    };
    const approve = (run: HarnessRun, commandId = 'decide') =>
      new InteractionService(h.service).resolve({
        orgId: run.orgId,
        runId: run.runId,
        interactionId: INTERACTION,
        decision: 'approved',
        channel: 'web',
        by: 'p_actor',
        commandId,
      });
    const status = async (run: HarnessRun) => (await h.service.get(run.orgId, run.runId))?.status;

    it('PW-1: an approval answered 16 hours later resumes the run with the 50 working minutes it had left', async () => {
      const run = await waiting();
      expect(run.absoluteDeadlineAt).to.equal(ASKED_AT + DEFAULT_WAIT_FOR_PERSON_DAYS * DAY);
      const answeredAt = START + 16 * HOUR;
      h.setNow(answeredAt);
      expect(await h.service.sweep(10)).to.equal(0);
      expect(await status(run)).to.equal('waiting_approval');

      const resumed = (await approve(run)).run;
      expect(resumed.status).to.equal('queued');
      expect(resumed.absoluteDeadlineAt).to.equal(answeredAt + 50 * MINUTE);
      expect(resumed.workLeftMs ?? null).to.equal(null);
      const claimed = await h.claim(resumed, 'worker-b', HOUR);
      expect(claimed.status).to.equal('running');
      // The worker's run model is strict: the held time is Node's bookkeeping, and a field it does not know would
      // make every claim of the run unreadable to it.
      expect(workerRun(run)).not.to.have.property('workLeftMs');
      expect(workerRun(claimed)).not.to.have.property('workLeftMs');
      expect(claimed.leaseExpiresAt).to.equal(answeredAt + 50 * MINUTE);

      // The working deadline counts again: it ends the run 50 minutes after the answer, not a week later.
      h.setNow(answeredAt + 50 * MINUTE - 1);
      expect(await h.service.sweep(10)).to.equal(0);
      h.setNow(answeredAt + 50 * MINUTE);
      expect(await h.service.sweep(10)).to.equal(1);
      expect(await status(run)).to.equal('failed');
    });

    it('PW-2: nobody answers for the whole window: the sweeper ends the run, and only then', async () => {
      const run = await waiting();
      const expiresAt = ASKED_AT + DEFAULT_WAIT_FOR_PERSON_DAYS * DAY;
      h.setNow(expiresAt - 1);
      expect(await h.service.sweep(10)).to.equal(0);
      expect(await status(run)).to.equal('waiting_approval');
      h.setNow(expiresAt);
      expect(await h.service.sweep(10)).to.equal(1);
      expect(await status(run)).to.equal('failed');
      const failed = (await h.backend.journal.readRun(run.orgId, run.runId, 0, 100)).at(-1);
      expect([failed?.type, failed?.data?.['errorType']]).to.deep.equal(['run.failed', 'deadline_exceeded']);
      // A run failed at its deadline with an approval pending reads "waited for an approval that nobody gave in
      // time" and its card "Expired" (ux1-approval-reason.test.ts, TO-2).
      expect(await conflictCode(approve(run, 'decide-late'))).to.be.a('string');
    });

    it('PW-3: the window is the deployment’s to set, and a typo is refused', async () => {
      process.env[WAIT_FOR_PERSON_ENV] = '0.5';
      const run = await waiting();
      expect(run.absoluteDeadlineAt).to.equal(ASKED_AT + 12 * HOUR);
      h.setNow(ASKED_AT + 12 * HOUR);
      expect(await h.service.sweep(10)).to.equal(1);
      for (const typo of ['abc', '0', '-1', '400']) {
        expect(() => waitForPersonMs({ [WAIT_FOR_PERSON_ENV]: typo })).to.throw(WAIT_FOR_PERSON_ENV);
      }
      expect(waitForPersonMs({})).to.equal(7 * DAY);
    });

    it('PW-4: a cancelled wait never comes back, and a run’s own waits keep their deadline', async () => {
      const run = await waiting();
      h.setNow(START + 2 * HOUR);
      const cancelled = await h.service.control({
        commandId: 'cancel',
        dispatchCommandId: 'cancel:dispatch',
        orgId: run.orgId,
        runId: run.runId,
        expectedRevision: run.revision,
        action: 'cancel',
      });
      expect(cancelled.run.status).to.equal('cancelled');

      const admitted = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user', deadlineMs: HOUR });
      const claimed = await h.claim(admitted.run, 'worker-a', 10 * MINUTE);
      const paused = await h.service.commit({
        commandId: 'worker-a:timer',
        orgId: claimed.orgId,
        runId: claimed.runId,
        workerId: 'worker-a',
        attemptId: 'attempt-worker-a',
        fence: claimed.fence,
        events: [],
        legacyEvents: [],
        transition: { status: 'paused', detail: { kind: 'suspend', reason: 'timer' } },
        outbox: [],
      });
      expect(paused.run.absoluteDeadlineAt).to.equal(admitted.run.absoluteDeadlineAt);
    });
  });

  describe('WAITCHILD: a child waiting for a person does not use up the parents joined on it', () => {
    let h: ContractHarness;
    let children: ChildRunService;
    let saved: string | undefined;
    beforeEach(() => {
      h = harness();
      h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 as const });
      h.setAgentDefinition((agentKey) => agentDefinition({ agentKey, maxChildren: 3 }));
      const store = h.backend.childRuns;
      if (store === undefined) expect.fail('backend has no child store');
      children = new ChildRunService({
        runs: h.backend.executionStore,
        children: store,
        journals: new JournalService(h.backend.payloads, testCipher()),
      });
      saved = process.env[WAIT_FOR_PERSON_ENV];
      delete process.env[WAIT_FOR_PERSON_ENV];
    });
    afterEach(() => {
      if (saved === undefined) delete process.env[WAIT_FOR_PERSON_ENV];
      else process.env[WAIT_FOR_PERSON_ENV] = saved;
    });

    const WINDOW = DEFAULT_WAIT_FOR_PERSON_DAYS * DAY;
    const ANSWERED_AT = START + 16 * HOUR;
    const CHILD_ANSWER: ChildResult = {
      resultRef: inlineContentRef(Buffer.from(JSON.stringify({ output: 'patched', success: true }))),
      labels: { integrity: 'internal' },
    };

    const get = async (runId: string): Promise<HarnessRun> => {
      const run = await h.service.get(h.tenant.orgId, runId);
      if (run === null) expect.fail(`run ${runId} not found`);
      return run;
    };
    const commitRun = (
      run: HarnessRun,
      commandId: string,
      transition?: Record<string, unknown>,
      events: Parameters<typeof h.service.commit>[0]['events'] = [],
    ) =>
      h.service.commit({
        commandId,
        orgId: run.orgId,
        runId: run.runId,
        workerId: run.workerId as string,
        attemptId: run.attemptId as string,
        fence: run.fence,
        events,
        legacyEvents: [],
        ...(transition !== undefined ? { transition: transition as never } : {}),
        outbox: [],
      });
    /** A working run asks a person and suspends for the answer. */
    const ask = async (run: HarnessRun): Promise<HarnessRun> => {
      await commitRun(run, `${run.runId}:ask`, undefined, [
        workerEvent('interaction.requested', { interactionId: INTERACTION, kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` }),
      ]);
      const suspended = (
        await commitRun(run, `${run.runId}:suspend`, {
          status: 'waiting_approval',
          detail: { kind: 'suspend', reason: 'approval', interactionId: INTERACTION },
        })
      ).run;
      expect(suspended.status).to.equal('waiting_approval');
      return suspended;
    };
    const join = async (run: HarnessRun): Promise<HarnessRun> =>
      (await commitRun(run, `${run.runId}:join`, { status: 'paused', detail: { kind: 'suspend', reason: 'join' } })).run;
    const finish = (run: HarnessRun) =>
      commitRun(run, `${run.runId}:done`, {
        status: 'completed',
        outcome: 'answer',
        ...(run.parent !== undefined ? { childResult: CHILD_ANSWER } : {}),
      });
    const spawn = async (parent: HarnessRun, invocationId: string, maxChildren = 0): Promise<HarnessRun> => {
      const { childRunId } = await children.spawn(
        {
          orgId: parent.orgId,
          runId: parent.runId,
          workerId: parent.workerId as string,
          attemptId: parent.attemptId as string,
          fence: parent.fence,
        },
        { invocationId, goal: 'fix the failing test', grants: maxChildren > 0 ? { maxChildren } : {} },
      );
      return get(childRunId);
    };
    const approve = (run: HarnessRun, commandId = `${run.runId}:decide`) =>
      new InteractionService(h.service).resolve({
        orgId: run.orgId,
        runId: run.runId,
        interactionId: INTERACTION,
        decision: 'approved',
        channel: 'web',
        by: 'p_actor',
        commandId,
      });
    /** An hour-long chat run, claimed at START, with one child it joins five minutes in. */
    const parentJoinedOn = async (overrides: Parameters<typeof h.admit>[0] = {}) => {
      const admitted = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user', deadlineMs: HOUR, ...overrides });
      const root = await h.claim(admitted.run, 'worker-p', 20 * MINUTE);
      const child = await spawn(root, 't1.0');
      h.setNow(START + 5 * MINUTE);
      const joined = await join(root);
      expect([joined.status, joined.workLeftMs ?? null]).to.deep.equal(['paused', null]);
      expect(joined.absoluteDeadlineAt).to.equal(START + HOUR);
      return { root, child: await h.claim(child, 'worker-c', 20 * MINUTE) };
    };

    it('PW-5: a code-task child asks overnight; the joined parent survives, gets its time back with the answer, and finishes', async () => {
      const { root, child } = await parentJoinedOn();
      h.setNow(ASKED_AT);
      const waiting = await ask(child);
      expect(waiting.absoluteDeadlineAt).to.equal(ASKED_AT + WINDOW);
      expect(waiting.workLeftMs).to.equal(50 * MINUTE);
      const held = await get(root.runId);
      expect(held.workLeftMs).to.equal(50 * MINUTE);
      // The parent outlives its child's window by its own working time: the child's ending is always read.
      expect(held.absoluteDeadlineAt).to.equal(ASKED_AT + WINDOW + 50 * MINUTE);
      expect(workerRun(held)).not.to.have.property('waitingChildren');
      expect(workerRun(held)).not.to.have.property('workLeftMs');

      h.setNow(ANSWERED_AT);
      expect(await h.service.sweep(10)).to.equal(0);
      expect((await get(root.runId)).status).to.equal('paused');
      const resumed = (await approve(waiting)).run;
      expect([resumed.status, resumed.absoluteDeadlineAt]).to.deep.equal(['queued', ANSWERED_AT + 50 * MINUTE]);
      const released = await get(root.runId);
      expect([released.status, released.absoluteDeadlineAt, released.workLeftMs ?? null, released.waitingChildren ?? []])
        .to.deep.equal(['paused', ANSWERED_AT + 50 * MINUTE, null, []]);

      await finish(await h.claim(resumed, 'worker-c2', 20 * MINUTE));
      const woken = await get(root.runId);
      expect(woken.status).to.equal('queued');
      const claimed = await h.claim(woken, 'worker-p2', HOUR);
      expect(claimed.leaseExpiresAt).to.equal(ANSWERED_AT + 50 * MINUTE);
      expect((await finish(claimed)).run.status).to.equal('completed');
    });

    it('PW-6: a grandchild waits: both joined runs above it hold their time, in order, and get it back together', async () => {
      const admitted = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user', deadlineMs: HOUR });
      const root = await h.claim(admitted.run, 'worker-p', 20 * MINUTE);
      const child = await h.claim(await spawn(root, 't1.0', 1), 'worker-c', 20 * MINUTE);
      await join(root);
      const grandchild = await h.claim(await spawn(child, 't2.0'), 'worker-g', 20 * MINUTE);
      h.setNow(START + 5 * MINUTE);
      await join(child);
      h.setNow(ASKED_AT);
      const waiting = await ask(grandchild);
      const [c, r] = [await get(child.runId), await get(root.runId)];
      expect([c.workLeftMs, r.workLeftMs]).to.deep.equal([50 * MINUTE, 50 * MINUTE]);
      expect(c.absoluteDeadlineAt).to.equal(waiting.absoluteDeadlineAt + 50 * MINUTE);
      expect(r.absoluteDeadlineAt).to.equal(c.absoluteDeadlineAt + 50 * MINUTE);

      h.setNow(ANSWERED_AT);
      expect(await h.service.sweep(10)).to.equal(0);
      await approve(waiting);
      for (const runId of [child.runId, root.runId]) {
        const back = await get(runId);
        expect([back.status, back.absoluteDeadlineAt, back.workLeftMs ?? null]).to.deep.equal(['paused', ANSWERED_AT + 50 * MINUTE, null]);
      }
      await finish(await h.claim(await get(grandchild.runId), 'worker-g2', 20 * MINUTE));
      await finish(await h.claim(await get(child.runId), 'worker-c2', 20 * MINUTE));
      expect((await get(root.runId)).status).to.equal('queued');
    });

    it('PW-7: of two children only one waits: its sibling ending leaves the parent held until the answer', async () => {
      const admitted = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user', deadlineMs: HOUR });
      const root = await h.claim(admitted.run, 'worker-p', 20 * MINUTE);
      const asking = await h.claim(await spawn(root, 't1.0'), 'worker-a', 20 * MINUTE);
      const working = await spawn(root, 't1.1');
      await join(root);
      h.setNow(ASKED_AT);
      const waiting = await ask(asking);
      await finish(await h.claim(working, 'worker-b', 20 * MINUTE));
      const held = await get(root.runId);
      expect([held.status, held.workLeftMs, held.children?.open]).to.deep.equal(['paused', 50 * MINUTE, 1]);

      h.setNow(ANSWERED_AT);
      expect(await h.service.sweep(10)).to.equal(0);
      await approve(waiting);
      expect((await get(root.runId)).absoluteDeadlineAt).to.equal(ANSWERED_AT + 50 * MINUTE);
      await finish(await h.claim(await get(asking.runId), 'worker-a2', 20 * MINUTE));
      expect((await get(root.runId)).status).to.equal('queued');
    });

    it('PW-8: nobody answers: the child ends at its window, and the parent reads that with its working time left', async () => {
      const { root, child } = await parentJoinedOn();
      h.setNow(ASKED_AT);
      await ask(child);
      const windowEnd = ASKED_AT + WINDOW;
      h.setNow(windowEnd - 1);
      expect(await h.service.sweep(10)).to.equal(0);
      h.setNow(windowEnd);
      expect(await h.service.sweep(10)).to.equal(1);
      expect((await get(child.runId)).status).to.equal('failed');
      const woken = await get(root.runId);
      expect([woken.status, woken.absoluteDeadlineAt, woken.workLeftMs ?? null]).to.deep.equal(['queued', windowEnd + 50 * MINUTE, null]);
      const ended = (await h.backend.journal.readRun(root.orgId, root.runId, 0, 100)).find((event) => event.type === 'child.completed');
      expect(ended?.data?.['outcome']).to.equal('failed');
      expect(await h.service.sweep(10)).to.equal(0);
      const claimed = await h.claim(woken, 'worker-p2', HOUR);
      expect(claimed.status).to.equal('running');
      expect((await finish(claimed)).run.status).to.equal('completed');
    });

    it('PW-9: a parent that joins after its child began waiting holds from its join', async () => {
      const admitted = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user', deadlineMs: HOUR });
      const root = await h.claim(admitted.run, 'worker-p', 30 * MINUTE);
      const child = await h.claim(await spawn(root, 't1.0'), 'worker-c', 20 * MINUTE);
      h.setNow(ASKED_AT);
      const waiting = await ask(child);
      // Still working: its clock counts, and its child is bounded by it.
      const working = await get(root.runId);
      expect([working.absoluteDeadlineAt, working.workLeftMs ?? null]).to.deep.equal([START + HOUR, null]);
      h.setNow(START + 20 * MINUTE);
      const joined = await join(root);
      expect([joined.workLeftMs, joined.absoluteDeadlineAt]).to.deep.equal([40 * MINUTE, waiting.absoluteDeadlineAt + 40 * MINUTE]);
      h.setNow(ANSWERED_AT);
      await approve(waiting);
      expect((await get(root.runId)).absoluteDeadlineAt).to.equal(ANSWERED_AT + 40 * MINUTE);
    });

    it('PW-10: a scheduled firing’s child keeps the schedule’s deadline (AP-1), and so does its parent', async () => {
      const { root, child } = await parentJoinedOn({ trigger: { scheduleId: 's-1', firingId: 'f-1', slotKey: 'slot-1' } });
      h.setNow(ASKED_AT);
      const waiting = await ask(child);
      expect([waiting.absoluteDeadlineAt, waiting.workLeftMs ?? null]).to.deep.equal([START + HOUR, null]);
      const parent = await get(root.runId);
      expect([parent.absoluteDeadlineAt, parent.workLeftMs ?? null]).to.deep.equal([START + HOUR, null]);
      h.setNow(START + HOUR);
      expect(await h.service.sweep(10)).to.equal(2);
    });

    it('PW-11: cancelling the parent while its child waits ends both; the late answer is refused', async () => {
      const { root, child } = await parentJoinedOn();
      h.setNow(ASKED_AT);
      const waiting = await ask(child);
      h.setNow(START + 2 * HOUR);
      const parent = await get(root.runId);
      const cancelled = await h.service.control({
        commandId: 'cancel',
        dispatchCommandId: 'cancel:dispatch',
        orgId: parent.orgId,
        runId: parent.runId,
        expectedRevision: parent.revision,
        action: 'cancel',
      });
      expect(cancelled.run.status).to.equal('cancelled');
      expect((await get(child.runId)).status).to.equal('cancelled');
      expect(await conflictCode(approve(waiting, 'decide-late'))).to.be.a('string');
    });

    it('PW-12 (WP-99, ADR-RUN-01 D4): an operator retires the child\'s engine version; the joined parent holds its time with it and gets it back on the resume', async () => {
      const { root, child } = await parentJoinedOn();
      h.setNow(ASKED_AT);
      const holdUntil = ASKED_AT + DAY;
      const retire = await h.service.control({
        commandId: 'pw12-retire',
        dispatchCommandId: 'pw12-retire:dispatch',
        orgId: child.orgId,
        runId: child.runId,
        expectedRevision: child.revision,
        action: 'suspend',
        holdUntil,
        stop: { scope: 'engine_version', reason: 'maintenance' },
      });
      expect([retire.run.status, retire.run.workLeftMs]).to.deep.equal(['paused', 50 * MINUTE]);
      const held = await get(root.runId);
      // Before WP-99 the parent kept START + HOUR and the sweeper failed it deadline_exceeded while its child waited.
      expect(held.workLeftMs).to.equal(50 * MINUTE);
      expect(held.absoluteDeadlineAt).to.equal(holdUntil + 50 * MINUTE);
      expect(held.waitingChildren).to.deep.equal([child.runId]);

      h.setNow(ANSWERED_AT);
      expect(await h.service.sweep(10)).to.equal(0);
      expect((await get(root.runId)).status).to.equal('paused');
      const retired = await get(child.runId);
      const resumed = await h.service.control({
        commandId: 'pw12-resume',
        dispatchCommandId: 'pw12-resume:dispatch',
        orgId: retired.orgId,
        runId: retired.runId,
        expectedRevision: retired.revision,
        action: 'resume',
        cause: 'version_available',
      });
      expect([resumed.run.status, resumed.run.absoluteDeadlineAt]).to.deep.equal(['queued', ANSWERED_AT + 50 * MINUTE]);
      const released = await get(root.runId);
      expect([released.status, released.absoluteDeadlineAt, released.workLeftMs ?? null, released.waitingChildren ?? []])
        .to.deep.equal(['paused', ANSWERED_AT + 50 * MINUTE, null, []]);
      await finish(await h.claim(resumed.run, 'worker-c2', 20 * MINUTE));
      expect((await get(root.runId)).status).to.equal('queued');
    });

    it('R13-RCX-1: a run paused for a reconnect (WP-64) is cancelled at once, and a late reconnect is refused', async () => {
      const admitted = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user', deadlineMs: HOUR });
      const run = await h.claim(admitted.run, 'worker-a', 20 * MINUTE);
      h.setNow(ASKED_AT);
      const reconnect = 'ix.connect.t2.0';
      const commit = (commandId: string, events: Parameters<typeof h.service.commit>[0]['events'], suspend: boolean) =>
        h.service.commit({
          commandId,
          orgId: run.orgId,
          runId: run.runId,
          workerId: 'worker-a',
          attemptId: 'attempt-worker-a',
          fence: run.fence,
          events,
          legacyEvents: [],
          ...(suspend
            ? { transition: { status: 'waiting_approval', detail: { kind: 'suspend', reason: 'approval', interactionId: reconnect } } }
            : {}),
          outbox: [],
        });
      await commit('worker-a:ask', [
        workerEvent('interaction.requested', {
          interactionId: reconnect,
          kind: 'connect',
          bindingDigest: `sha256:${'2'.repeat(64)}`,
          connect: { item: 'GitHub', itemKind: 'toolset', instanceId: 'gh-1', reason: 'sign_in_rejected' },
        }),
      ], false);
      const paused = (await commit('worker-a:suspend', [], true)).run;
      expect(paused.status).to.equal('waiting_approval');

      h.setNow(ASKED_AT + MINUTE);
      const cancelled = await h.service.control({
        commandId: 'cancel',
        dispatchCommandId: 'cancel:dispatch',
        orgId: paused.orgId,
        runId: paused.runId,
        expectedRevision: paused.revision,
        action: 'cancel',
      });
      // Promptly: Node ends it in the cancel's own transaction; no worker holds a paused run.
      expect(cancelled.run.status).to.equal('cancelled');
      const types = (await h.backend.journal.readRun(paused.orgId, paused.runId, 0, 100)).map((e) => e.type);
      expect(types.slice(-2)).to.deep.equal(['run.cancel_requested', 'run.cancelled']);
      const late = new InteractionService(h.service).resolve({
        orgId: paused.orgId,
        runId: paused.runId,
        interactionId: reconnect,
        decision: 'approved',
        channel: 'web',
        by: 'p_actor',
        commandId: 'reconnected-late',
      });
      expect(await conflictCode(late)).to.be.a('string');
      expect((await h.service.get(paused.orgId, paused.runId))?.status).to.equal('cancelled');
    });
  });
}
