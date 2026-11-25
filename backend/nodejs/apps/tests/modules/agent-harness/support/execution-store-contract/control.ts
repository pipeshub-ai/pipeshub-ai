/**
 * ExecutionStore contract, part of `describeExecutionStoreContract`. Control: cancellation, Stop, interactions resolved into a running run, steering.
 */
import { expect } from 'chai';
import { createHash } from 'crypto';
import { Types } from 'mongoose';
import { HarnessConflictError, HarnessNotFoundError, HarnessForbiddenError, HarnessUnavailableError } from '../../../../../src/modules/agent-harness/domain/errors';
import { CommitBatch, HarnessRun, InteractionDecision, RunTransition } from '../../../../../src/modules/agent-harness/domain/execution';
import { JournalService } from '../../../../../src/modules/agent-harness/application/journal-service';
import { InteractionService } from '../../../../../src/modules/agent-harness/application/interaction-service';
import { InMemoryRunWakeup } from '../../../../../src/modules/agent-harness/adapters/in-memory-interactions';
import { HARNESS_ENABLED } from '../rollout';
import { JOURNAL_INLINE_PAYLOAD_MAX_BYTES } from '../../../../../src/modules/agent-harness/domain/journal';
import { InMemoryRecordReadAccess } from '../../../../../src/modules/agent-harness/adapters/in-memory-record-access';
import { workerEvent } from '../journal-fixtures';
import { principalRefFor } from '../../../../../src/modules/agent-harness/domain/run-spec';
import { CONVERSATION_GRANT_ACTOR_ONLY_MESSAGE } from '../../../../../src/modules/agent-harness/domain/conversation-grants';
import { testCipher } from '../data-protection';
import { ContractHarness, START, DEADLINE_MS, conflictCode } from './harness';
import type { OpenRunFilter, OperatorRuns } from '../../../../../src/modules/agent-harness/ports/execution-store';

export function describeControlCases(
  harness: () => ContractHarness,
): void {
  let h: ContractHarness;
  beforeEach(() => {
    h = harness();
  });

  it("DEACT-12 (SEC-AZ-08): one person's unfinished runs, across their sessions; not ended ones, not another person's or org's", async () => {
    const queued = (await h.admit()).run;
    const running = await h.claim((await h.admit()).run);
    const ended = (await h.admit()).run;
    await h.service.control({
      commandId: 'deact-12-cancel',
      dispatchCommandId: 'deact-12-cancel:dispatch',
      orgId: ended.orgId,
      runId: ended.runId,
      expectedRevision: ended.revision,
      action: 'cancel',
    });
    const other = h.newTenant();
    await h.admit({ actorId: other.actorId });
    await h.admit({ orgId: other.orgId, actorId: h.tenant.actorId });
    const store = h.backend.executionStore as unknown as {
      activeRunsOfActor(orgId: string, actorId: string): Promise<HarnessRun[]>;
    };

    const open = await store.activeRunsOfActor(h.tenant.orgId, h.tenant.actorId);

    expect(open.map((run) => run.runId).sort()).to.deep.equal([queued.runId, running.runId].sort());
    expect(open.find((run) => run.runId === running.runId)?.status).to.equal('running');
  });

  it("OPS-03 (ADR-OPS-01): open runs by scope for the operator controls: org, session, agent; never an ended run or another org's; loop runs hold no engine", async () => {
    const queued = (await h.admit()).run;
    const running = await h.claim((await h.admit()).run);
    const ended = (await h.admit()).run;
    await h.service.control({
      commandId: 'ops-03-cancel',
      dispatchCommandId: 'ops-03-cancel:dispatch',
      orgId: ended.orgId,
      runId: ended.runId,
      expectedRevision: ended.revision,
      action: 'cancel',
    });
    const other = h.newTenant();
    await h.admit({ orgId: other.orgId, actorId: other.actorId });
    const store = h.backend.executionStore as unknown as OperatorRuns;
    const ids = async (filter: OpenRunFilter) =>
      (await store.openRuns(filter, 10)).map(({ run }) => run.runId).sort();

    expect(await ids({ orgId: h.tenant.orgId })).to.deep.equal([queued.runId, running.runId].sort());
    expect(await ids({ orgId: h.tenant.orgId, sessionId: running.sessionId })).to.deep.equal([running.runId]);
    expect(await ids({ orgId: h.tenant.orgId, agentKey: 'no-such-agent' })).to.deep.equal([]);
    expect(await ids({ orgId: h.tenant.orgId, engineOnly: true })).to.deep.equal([]);
    expect(await ids({ engine: { kind: 'codex', version: '0.160.0' } })).to.deep.equal([]);
    expect((await store.openRuns({ orgId: h.tenant.orgId }, 10)).every(({ engine }) => engine === null)).to.equal(true);
    expect(await store.openRuns({ orgId: h.tenant.orgId }, 1)).to.have.length(1);
  });

  it('OPS-14 (ADR-OPS-01 D6): a retire suspends a queued or running run, takes its attempt back and holds its working time; only a version_available resume wakes it', async () => {
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
    const running = await h.claim((await h.admit()).run, 'worker-a', 60_000);
    const queued = (await h.admit()).run;
    const holdUntil = h.clock.now() + 3_600_000;
    for (const run of [running, queued]) {
      const commandId = `ops-14-suspend-${run.runId}`;
      const { run: paused } = await h.service.control({
        commandId,
        dispatchCommandId: `${commandId}:dispatch`,
        orgId: run.orgId,
        runId: run.runId,
        expectedRevision: run.revision,
        action: 'suspend',
        holdUntil,
        stop: { scope: 'engine_version', reason: 'maintenance' },
      });
      expect(paused).to.deep.include({ status: 'paused', suspendedFor: 'retired', attemptId: null, leaseExpiresAt: null });
      expect(paused.absoluteDeadlineAt).to.equal(holdUntil);
      expect(paused.workLeftMs).to.be.a('number');
    }
    const types = (await h.backend.journal.readRun(running.orgId, running.runId, 0, 100)).map((e) => e.type);
    expect(types).to.include.members(['run.suspended', 'x.pipeshub.run.notice']);
    // An approval's resume is not this one, and a suspended run is not suspended twice.
    const again = await conflictCode(
      h.service.control({
        commandId: 'ops-14-again',
        dispatchCommandId: 'ops-14-again:dispatch',
        orgId: queued.orgId,
        runId: queued.runId,
        expectedRevision: (await h.service.get(queued.orgId, queued.runId))!.revision,
        action: 'suspend',
        holdUntil,
      }),
    );
    expect(again).to.equal('INVALID_TRANSITION');
    const current = (await h.service.get(running.orgId, running.runId))!;
    const { run: resumed } = await h.service.control({
      commandId: 'ops-14-resume',
      dispatchCommandId: 'ops-14-resume:dispatch',
      orgId: current.orgId,
      runId: current.runId,
      expectedRevision: current.revision,
      action: 'resume',
      cause: 'version_available',
    });
    expect(resumed).to.deep.include({ status: 'queued', suspendedFor: null });
    // The attempt a retire took back counts toward no crash loop.
    expect(resumed.attempts ?? 0).to.equal((running.attempts ?? 1) - 1);
    expect(resumed.absoluteDeadlineAt).to.be.lessThan(holdUntil);
    const after = (await h.backend.journal.readRun(running.orgId, running.runId, 0, 100)).map((e) => e.type);
    expect(after).to.include('run.resumed');
    // The resumed run is claimed again as a lapsed lease's would be.
    expect((await h.claim(resumed, 'worker-b')).status).to.equal('running');
  });

  it('keeps fencing authority while cancellation is delivered to a running worker', async () => {
    const { run } = await h.admit();
    const claimed = await h.claim(run);
    const cancel = {
      commandId: 'cancel-1',
      dispatchCommandId: 'dispatch-cancel-1',
      orgId: run.orgId,
      runId: run.runId,
      expectedRevision: claimed.revision,
      action: 'cancel' as const,
    };
    const requested = await h.service.control(cancel);
    expect(requested.run.status).to.equal('cancelling');
    expect(requested.run.workerId).to.equal('worker-a');
    expect(await h.service.control(cancel)).to.deep.equal({
      ...requested,
      replayed: true,
    });
    expect(
      (await h.backend.dispatches(run.orgId, run.runId)).map((item) => [
        item.commandId,
        item.kind,
      ]),
    ).to.deep.include(['dispatch-cancel-1', 'run_cancel']);

    const finished = await h.service.commit({
      commandId: 'cancel-ack-1',
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'worker-a',
      attemptId: claimed.attemptId as string,
      fence: claimed.fence,
      expectedRevision: requested.run.revision,
      status: 'cancelled',
      events: [{ type: 'run_cancelled', data: {} }],
      outbox: [],
    });
    expect(finished.run.status).to.equal('cancelled');
    expect(finished.run.workerId).to.equal(null);
  });

  it('cancels queued work without waiting for a worker', async () => {
    const admitted = await h.admit();
    const cancelled = await h.service.control({
      commandId: 'cancel-queued',
      dispatchCommandId: 'unused-dispatch',
      orgId: admitted.run.orgId,
      runId: admitted.run.runId,
      expectedRevision: admitted.run.revision,
      action: 'cancel',
    });
    expect(cancelled.run.status).to.equal('cancelled');
    const next = await h.admit({ sessionId: admitted.sessionId });
    expect(next.created).to.equal(true);
  });

  describe('UX3 ST-1: Stop wakes the worker running the run', () => {
    it('a cancel that leaves the run cancelling wakes its channel once', async () => {
      const wakeup = new InMemoryRunWakeup();
      h.service.wakeOnCancel(wakeup);
      const { run } = await h.admit();
      const claimed = await h.claim(run);
      await h.service.control({
        commandId: 'ux3-stop',
        dispatchCommandId: 'ux3-stop-dispatch',
        orgId: run.orgId,
        runId: run.runId,
        expectedRevision: claimed.revision,
        action: 'cancel',
      });
      expect(wakeup.woken).to.deep.equal([run.runId]);
    });

    it('a queued run is cancelled outright: nobody to wake', async () => {
      const wakeup = new InMemoryRunWakeup();
      h.service.wakeOnCancel(wakeup);
      const { run } = await h.admit();
      await h.service.control({
        commandId: 'ux3-stop-queued',
        dispatchCommandId: 'ux3-stop-queued-dispatch',
        orgId: run.orgId,
        runId: run.runId,
        expectedRevision: run.revision,
        action: 'cancel',
      });
      expect(wakeup.woken).to.deep.equal([]);
    });
  });

  describe('interactions resolved into the running run (P2.6c resolveActive)', () => {
    const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
    const agentRun = { agentKey: 'agent-1', authorization: 'Bearer user' };
    const CLAIMED_HEAD = 3;
    const SUSPEND_FOR = (ix: string): RunTransition => ({
      status: 'waiting_approval',
      detail: { kind: 'suspend', reason: 'approval', interactionId: ix },
    });

    beforeEach(() => h.setRollout(V2));

    const claimAs = (run: Pick<HarnessRun, 'orgId' | 'runId'>, worker: string) =>
      h.service.claim({
        orgId: run.orgId,
        runId: run.runId,
        workerId: worker,
        attemptId: `attempt-${worker}`,
        leaseDurationMs: 1_000,
      });
    const requested = (interactionId: string) =>
      workerEvent('interaction.requested', {
        interactionId,
        kind: 'approval',
        bindingDigest: `sha256:${'1'.repeat(64)}`,
      });
    const commit = (
      run: HarnessRun,
      worker: string,
      commandId: string,
      events: CommitBatch['events'],
      transition?: RunTransition,
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
        ...(transition !== undefined ? { transition } : {}),
        outbox: [],
      });
    const tail = async (run: HarnessRun) =>
      (
        await h.backend.journal.readStream(run.orgId, run.sessionId, CLAIMED_HEAD, 100)
      ).map((event) => event.type);
    const resolver = (wakeup = new InMemoryRunWakeup()) =>
      new InteractionService(h.service, wakeup);
    const resolve = (
      run: HarnessRun,
      interactionId: string,
      commandId: string,
      decision: InteractionDecision = 'approved',
      wakeup?: InMemoryRunWakeup,
    ) =>
      resolver(wakeup).resolve({
        orgId: run.orgId,
        runId: run.runId,
        interactionId,
        decision,
        channel: 'web',
        by: 'p_approver',
        commandId,
      });
    /** A claimed run holding `ix` for approval (the ask commit, no transition). */
    const holding = async (ix = 'ix-a') => {
      const admitted = await h.admit(agentRun);
      const { run } = await claimAs(admitted.run, 'worker-a');
      await commit(run, 'worker-a', 'worker-a:ask:1', [requested(ix)]);
      return run;
    };

    it('NR-1, NR-8: a running run learns the decision without resuming, and its holder is woken', async () => {
      const run = await holding();
      expect((await h.service.get(run.orgId, run.runId))?.openInteractions).to.deep.equal(['ix-a']);
      const wakeup = new InMemoryRunWakeup();
      const receipt = await resolve(run, 'ix-a', 'decide-1', 'approved', wakeup);
      expect(receipt.run.status).to.equal('running');
      expect(receipt.run.openInteractions ?? []).to.deep.equal([]);
      expect(await tail(run)).to.deep.equal(['interaction.requested', 'interaction.resolved']);
      expect(wakeup.woken).to.deep.equal([run.runId]);
      expect(
        (await h.backend.dispatches(run.orgId, run.runId)).map((d) => d.kind),
      ).not.to.include('run_resume');
      // The holder goes on: its next commit is accepted under the same attempt.
      await commit(run, 'worker-a', 'worker-a:intent:2', []);
    });

    it('P2.11e AQ-10: the approvals queue reads the org’s decidable runs with an open interaction', async () => {
      const store = h.backend.executionStore;
      const first = await holding('ix-a');
      h.setNow(START + 100);
      const colleague = new Types.ObjectId().toString();
      const admitted = await h.admit({
        agentKey: 'agent-2',
        authorization: 'Bearer user',
        actorId: colleague,
      });
      const { run: second } = await claimAs(admitted.run, 'worker-b');
      await commit(second, 'worker-b', 'worker-b:ask:1', [requested('ix-b')]);
      const quiet = await h.admit(agentRun);
      await claimAs(quiet.run, 'worker-c');
      const elsewhere = h.newTenant();
      const outsider = await h.admit({ ...agentRun, ...elsewhere });
      const { run: outsiderRun } = await claimAs(outsider.run, 'worker-d');
      await commit(outsiderRun, 'worker-d', 'worker-d:ask:1', [requested('ix-d')]);
      const ids = async (filter: Parameters<typeof store.pendingApprovalRuns>[1]) =>
        (await store.pendingApprovalRuns(first.orgId, filter)).map((run) => run.runId);

      expect(await ids({ limit: 10 })).to.deep.equal([second.runId, first.runId]);
      expect(await ids({ limit: 1 })).to.deep.equal([second.runId]);
      expect(await ids({ limit: 10, actorId: first.actorId })).to.deep.equal([first.runId]);
      expect(await ids({ limit: 10, agentKey: 'agent-2' })).to.deep.equal([second.runId]);
      const [listed] = await store.pendingApprovalRuns(first.orgId, { limit: 1 });
      expect(listed?.openInteractions).to.deep.equal(['ix-b']);

      await resolve(first, 'ix-a', 'decide-1');
      expect(await ids({ limit: 10 })).to.deep.equal([second.runId]);
      const current = await h.service.get(second.orgId, second.runId);
      await h.service.control({
        commandId: 'cancel-2',
        dispatchCommandId: 'cancel-2:dispatch',
        orgId: second.orgId,
        runId: second.runId,
        expectedRevision: current?.revision ?? 0,
        action: 'cancel',
      });
      expect(await ids({ limit: 10 })).to.deep.equal([]);
      expect(
        (await store.pendingApprovalRuns(elsewhere.orgId, { limit: 10 })).map((run) => run.runId),
      ).to.deep.equal([outsiderRun.runId]);
    });

    it('NR-2: a suspended run is resumed with the decision', async () => {
      const admitted = await h.admit(agentRun);
      const { run } = await claimAs(admitted.run, 'worker-a');
      await commit(run, 'worker-a', 'worker-a:suspend:1', [requested('ix-a')], SUSPEND_FOR('ix-a'));
      const wakeup = new InMemoryRunWakeup();
      const receipt = await resolve(run, 'ix-a', 'decide-1', 'approved', wakeup);
      expect(receipt.run.status).to.equal('queued');
      expect((await tail(run)).slice(-2)).to.deep.equal(['interaction.resolved', 'run.resumed']);
      expect(
        (await h.backend.dispatches(run.orgId, run.runId)).map((d) => d.kind),
      ).to.include('run_resume');
      expect(wakeup.woken).to.deep.equal([]);
    });

    it('NR-3: a queued run between attempts records the decision for its next claim', async () => {
      const run = await holding();
      await h.service.release({
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'worker-a',
        attemptId: 'attempt-worker-a',
        fence: run.fence,
        reason: 'lease_lost',
      });
      const receipt = await resolve(run, 'ix-a', 'decide-1');
      expect(receipt.run.status).to.equal('queued');
      expect((await tail(run)).slice(-1)).to.deep.equal(['interaction.resolved']);
      const again = await claimAs(run, 'worker-b');
      expect(again.run.status).to.equal('running');
    });

    it('NR-4: an interaction never requested, or already decided, is not open', async () => {
      const run = await holding();
      expect(await conflictCode(resolve(run, 'ix-never', 'decide-0'))).to.equal(
        'INTERACTION_NOT_OPEN',
      );
      await resolve(run, 'ix-a', 'decide-1');
      expect(await conflictCode(resolve(run, 'ix-a', 'decide-2', 'denied'))).to.equal(
        'INTERACTION_NOT_OPEN',
      );
      expect((await tail(run)).filter((t) => t === 'interaction.resolved')).to.have.length(1);
    });

    const explained = (interactionId: string, conversationGrant: boolean) =>
      workerEvent(
        'interaction.explained',
        {
          interactionId,
          rule: 'flow_external_write',
          toolId: 'slack@default/send_message',
          sensitivity: 'private',
          sources: [{ title: 'Q3 plan', recordId: 'rec-1' }],
          moreSources: 0,
          destination: { label: '#sales', key: `sha256:${'2'.repeat(64)}` },
          conversationGrant,
        },
        { critical: false },
      );
    const explainedHold = async (conversationGrant: boolean) => {
      const admitted = await h.admit(agentRun);
      const { run } = await claimAs(admitted.run, 'worker-a');
      await commit(run, 'worker-a', 'worker-a:ask:1', [explained('ix-a', conversationGrant), requested('ix-a')]);
      return run;
    };
    const forConversation = (run: HarnessRun, interactionId: string, commandId: string) =>
      resolver().resolve({
        orgId: run.orgId,
        runId: run.runId,
        interactionId,
        decision: 'approved',
        channel: 'web',
        by: principalRefFor(run.actorId),
        commandId,
        scope: 'conversation',
      });

    it('UX1 CG-1: an approval for the conversation journals the grant with the decision, in one commit', async () => {
      const run = await explainedHold(true);
      const receipt = await forConversation(run, 'ix-a', 'decide-conversation');
      expect(receipt.run.status).to.equal('running');
      expect(await tail(run)).to.deep.equal([
        'interaction.explained',
        'interaction.requested',
        'interaction.resolved',
        'interaction.granted',
      ]);
      const events = await h.backend.journal.readStream(run.orgId, run.sessionId, CLAIMED_HEAD, 100);
      const granted = events.find((event) => event.type === 'interaction.granted');
      expect(granted?.writer).to.equal('node');
      expect(granted?.critical).to.equal(false);
      // FLOWGRANT: the grant names what the worker offered (its tool, destination and the data the run had read).
      expect(granted?.data).to.deep.equal({
        interactionId: 'ix-a',
        scope: 'conversation',
        toolId: 'slack@default/send_message',
        key: 'sha256:2222222222222222222222222222222222222222222222222222222222222222',
        sensitivity: 'private',
        // WP-115: and who gave it, so an ask it answers later is theirs.
        by: principalRefFor(run.actorId),
      });
    });

    it("R38 CG-5 (SEC-C-04): an answer for the conversation from anyone but the run's own person is refused, nothing written", async () => {
      const run = await explainedHold(true);
      for (const [by, commandId] of [
        ['p_approver', 'decide-approver'],
        [`${principalRefFor(run.actorId)} `, 'decide-padded'],
        [principalRefFor(run.actorId).toUpperCase(), 'decide-upper'],
      ] as const) {
        let refused: unknown;
        try {
          await resolver().resolve({
            orgId: run.orgId,
            runId: run.runId,
            interactionId: 'ix-a',
            decision: 'approved',
            channel: 'web',
            by,
            commandId,
            scope: 'conversation',
          });
        } catch (error) {
          refused = error;
        }
        expect((refused as HarnessConflictError).code, by).to.equal('CONVERSATION_GRANT_NOT_OFFERED');
        expect((refused as Error).message).to.equal(CONVERSATION_GRANT_ACTOR_ONLY_MESSAGE);
      }
      expect(await tail(run)).to.deep.equal(['interaction.explained', 'interaction.requested']);
    });

    it('UX1 CG-2: a conversation grant the worker did not offer is refused and nothing is written', async () => {
      const run = await explainedHold(false);
      expect(await conflictCode(forConversation(run, 'ix-a', 'decide-a'))).to.equal(
        'CONVERSATION_GRANT_NOT_OFFERED',
      );
      expect(await tail(run)).to.deep.equal(['interaction.explained', 'interaction.requested']);
      const plain = await holding('ix-b');
      expect(await conflictCode(forConversation(plain, 'ix-b', 'decide-b'))).to.equal(
        'CONVERSATION_GRANT_NOT_OFFERED',
      );
      // Allowing once is always possible.
      await resolve(run, 'ix-a', 'decide-once');
      expect((await tail(run)).slice(-1)).to.deep.equal(['interaction.resolved']);
    });

    it('WP39-EX-7: a policy webhook ask never holds for the conversation, whatever its reason says', async () => {
      const admitted = await h.admit(agentRun);
      const { run } = await claimAs(admitted.run, 'worker-a');
      const webhookAsk = workerEvent(
        'interaction.explained',
        {
          interactionId: 'ix-a',
          rule: 'external_policy',
          layer: 'org',
          toolId: 'slack@default/send_message',
          conversationGrant: true,
        },
        { critical: false },
      );
      await commit(run, 'worker-a', 'worker-a:ask:1', [webhookAsk, requested('ix-a')]);
      expect(await conflictCode(forConversation(run, 'ix-a', 'decide-a'))).to.equal(
        'CONVERSATION_GRANT_NOT_OFFERED',
      );
      expect(await tail(run)).to.deep.equal(['interaction.explained', 'interaction.requested']);
      await resolve(run, 'ix-a', 'decide-once');
      expect((await tail(run)).slice(-1)).to.deep.equal(['interaction.resolved']);
    });

    const editableHold = async (editable: boolean) => {
      const admitted = await h.admit(agentRun);
      const { run } = await claimAs(admitted.run, 'worker-a');
      const offer = workerEvent(
        'interaction.explained',
        {
          interactionId: 'ix-a',
          rule: 'writes',
          layer: 'org',
          toolId: 'mail@default/send_email',
          conversationGrant: false,
          ...(editable ? { editable: true } : {}),
        },
        { critical: false },
      );
      await commit(run, 'worker-a', 'worker-a:ask:1', [offer, requested('ix-a')]);
      return run;
    };
    const EDITS = { to: 'team@acme.example', body: 'Summary only' };
    const withEdits = (run: HarnessRun, commandId: string, decision: InteractionDecision = 'approved') =>
      resolver().resolve({
        orgId: run.orgId,
        runId: run.runId,
        interactionId: 'ix-a',
        decision,
        channel: 'web',
        by: 'p_actor',
        commandId,
        edits: EDITS,
      });

    it('AP-06: an approval with edits journals them inline with their hash, and the run then needs a worker that runs them', async () => {
      const run = await editableHold(true);
      await withEdits(run, 'decide-edit');
      const events = await h.backend.journal.readStream(run.orgId, run.sessionId, CLAIMED_HEAD, 100);
      const resolved = events.find((event) => event.type === 'interaction.resolved');
      const ref = resolved?.data!['answerRef'] as { ref: string; hash: string };
      const bytes = Buffer.from(ref.ref.slice('inline:'.length), 'base64url');
      expect(JSON.parse(bytes.toString('utf8'))).to.deep.equal(EDITS);
      expect(ref.hash).to.equal(`sha256:${createHash('sha256').update(bytes).digest('hex')}`);
      const current = await h.service.get(run.orgId, run.runId);
      expect(current?.requires?.features ?? []).to.include('approval_edits');
    });

    it('AP-06: an edit the worker did not offer, or on a denial, is refused and nothing is written', async () => {
      const plain = await editableHold(false);
      expect(await conflictCode(withEdits(plain, 'decide-a'))).to.equal('EDIT_NOT_OFFERED');
      expect(await tail(plain)).to.deep.equal(['interaction.explained', 'interaction.requested']);
      const offered = await editableHold(true);
      expect(await conflictCode(withEdits(offered, 'decide-b', 'denied'))).to.equal('EDIT_NOT_OFFERED');
      const current = await h.service.get(offered.orgId, offered.runId);
      expect(current?.requires?.features ?? []).not.to.include('approval_edits');
      // Approving it as asked is always possible.
      await resolve(offered, 'ix-a', 'decide-once');
      expect((await tail(offered)).slice(-1)).to.deep.equal(['interaction.resolved']);
    });

    it('NR-5: two people deciding at once resolve it exactly once', async () => {
      const run = await holding();
      const outcomes = await Promise.allSettled([
        resolve(run, 'ix-a', 'decide-alice', 'approved'),
        resolve(run, 'ix-a', 'decide-bob', 'denied'),
      ]);
      expect(outcomes.filter((o) => o.status === 'fulfilled')).to.have.length(1);
      const refused = outcomes.find((o) => o.status === 'rejected') as PromiseRejectedResult;
      expect((refused.reason as HarnessConflictError).code).to.equal('INTERACTION_NOT_OPEN');
      expect((await tail(run)).filter((t) => t === 'interaction.resolved')).to.have.length(1);
    });

    it('NR-6: a decision after a cancel, or after the run ended, is refused and journals nothing', async () => {
      const run = await holding();
      const current = await h.service.get(run.orgId, run.runId);
      await h.service.control({
        commandId: 'cancel-1',
        dispatchCommandId: 'cancel-1:dispatch',
        orgId: run.orgId,
        runId: run.runId,
        expectedRevision: current?.revision ?? 0,
        action: 'cancel',
      });
      expect(await conflictCode(resolve(run, 'ix-a', 'decide-1'))).to.equal('RUN_CANCELLING');
      await commit(run, 'worker-a', 'worker-a:cancelled:2', [], {
        status: 'cancelled',
      });
      expect(await conflictCode(resolve(run, 'ix-a', 'decide-2'))).to.equal(
        'INTERACTION_NOT_OPEN',
      );
      expect(await tail(run)).not.to.include('interaction.resolved');
    });

    it('NR-7: a decision before the suspend refuses it; a suspend before the decision is resumed by it', async () => {
      const first = await holding();
      await resolve(first, 'ix-a', 'decide-1');
      expect(
        await conflictCode(
          commit(first, 'worker-a', 'worker-a:suspend:2', [], SUSPEND_FOR('ix-a')),
        ),
      ).to.equal('INTERACTION_RESOLVED');
      expect((await h.service.get(first.orgId, first.runId))?.status).to.equal('running');

      const second = await holding('ix-b');
      await commit(second, 'worker-a', 'worker-a:suspend:2', [], SUSPEND_FOR('ix-b'));
      const resumed = await resolve(second, 'ix-b', 'decide-2');
      expect(resumed.run.status).to.equal('queued');
    });

    it('NR-7: the decision and the suspend racing never leave a run suspended with nothing open', async () => {
      for (let round = 0; round < 6; round += 1) {
        const run = await holding(`ix-${String(round)}`);
        const ix = `ix-${String(round)}`;
        await Promise.allSettled([
          commit(run, 'worker-a', `worker-a:suspend:${String(round)}`, [], SUSPEND_FOR(ix)),
          resolve(run, ix, `decide-${String(round)}`),
        ]);
        const after = await h.service.get(run.orgId, run.runId);
        expect(['running', 'queued']).to.include(after?.status);
        expect((await tail(run)).filter((t) => t === 'interaction.resolved')).to.have.length(1);
      }
    });

    it('NR-9: the same decision retried replays its receipt', async () => {
      const run = await holding();
      const first = await resolve(run, 'ix-a', 'decide-1');
      expect(await resolve(run, 'ix-a', 'decide-1')).to.deep.equal(first);
      expect((await tail(run)).filter((t) => t === 'interaction.resolved')).to.have.length(1);
    });

    it('NR-10: a run that never tracked its interactions may still suspend for approval', async () => {
      const admitted = await h.admit(agentRun);
      const { run } = await claimAs(admitted.run, 'worker-a');
      const suspended = await commit(run, 'worker-a', 'worker-a:suspend:1', [], SUSPEND_FOR('ix-old'));
      expect(suspended.run.status).to.equal('waiting_approval');
    });

    describe('P2.9 the person the run acts for resolves a parked unknown effect exactly once', () => {
      const UNKNOWN = 'ix.unknown.t1.0';
      const reconciled = workerEvent(
        'tool.reconciled',
        {
          invocationId: 't1.0',
          verdict: 'still_unknown',
          reason: 'lookup_failed',
          lookups: 3,
          source: 'reconciler',
        },
        { critical: false },
      );
      /** A run suspended on an unknown effect the reconciler could not decide. */
      const parked = async () => {
        const admitted = await h.admit(agentRun);
        const { run } = await claimAs(admitted.run, 'worker-a');
        await commit(
          run,
          'worker-a',
          'worker-a:suspend:1',
          [reconciled, workerEvent('tool.unknown', { invocationId: 't1.0' }), requested(UNKNOWN)],
          SUSPEND_FOR(UNKNOWN),
        );
        return run;
      };
      const resolveAs = (
        run: HarnessRun,
        decision: InteractionDecision,
        commandId: string,
        wakeup = new InMemoryRunWakeup(),
      ) =>
        new InteractionService(h.service, wakeup).resolve({
          orgId: run.orgId,
          runId: run.runId,
          interactionId: UNKNOWN,
          decision,
          channel: 'web',
          by: principalRefFor(run.actorId),
          commandId,
        });
      const resolutions = async (run: HarnessRun) =>
        (await h.backend.journal.readStream(run.orgId, run.sessionId, 0, 200))
          .filter((event) => event.type === 'interaction.resolved')
          .map((event) => event.data);

      for (const decision of ['answered', 'denied'] as const) {
        it(`NO-2, NO-3: ${decision} journals the owner's word and resumes the run once`, async () => {
          const run = await parked();
          const receipt = await resolveAs(run, decision, 'k1');
          expect(receipt.run.status).to.equal('queued');
          expect(await resolutions(run)).to.deep.equal([
            { interactionId: UNKNOWN, decision, by: principalRefFor(run.actorId), channel: 'web' },
          ]);
          expect((await tail(run)).slice(-2)).to.deep.equal(['interaction.resolved', 'run.resumed']);
          expect(
            (await h.backend.dispatches(run.orgId, run.runId)).filter((d) => d.kind === 'run_resume'),
          ).to.have.length(1);
        });
      }

      it('NO-6: the same decision retried gets the first receipt', async () => {
        const run = await parked();
        const first = await resolveAs(run, 'answered', 'k1');
        const again = await resolveAs(run, 'answered', 'k1');
        expect(again.commandId).to.equal(first.commandId);
        expect(await resolutions(run)).to.have.length(1);
      });

      it('NO-7: two answers at once resolve it exactly once', async () => {
        const run = await parked();
        const outcomes = await Promise.allSettled([
          resolveAs(run, 'answered', 'k1'),
          resolveAs(run, 'denied', 'k2'),
        ]);
        expect(outcomes.filter((o) => o.status === 'fulfilled')).to.have.length(1);
        const refused = outcomes.find((o) => o.status === 'rejected') as PromiseRejectedResult;
        expect((refused.reason as HarnessConflictError).code).to.equal('INTERACTION_NOT_OPEN');
        expect(await resolutions(run)).to.have.length(1);
        expect((await tail(run)).filter((t) => t === 'run.resumed')).to.have.length(1);
      });
    });
  });

  describe('steering (P1b T2)', () => {
    const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
    const agentRun = { agentKey: 'agent-1', authorization: 'Bearer user' };
    const text = (query: string) => ({
      input: {
        query,
        recordIds: [],
        filters: {},
        attachments: [],
        model: { chatMode: 'auto' },
      },
    });
    // What the worker's inline refs look like: padded base64url, hash and size of the UTF-8.
    const inline = (value: string) => {
      const bytes = Buffer.from(value, 'utf8');
      return {
        ref: `inline:${bytes.toString('base64')}`
          .replace(/\+/g, '-')
          .replace(/\//g, '_'),
        hash: `sha256:${createHash('sha256').update(bytes).digest('hex')}`,
        size: bytes.length,
      };
    };
    const queuedEvents = async (run: HarnessRun) =>
      (
        await h.backend.journal.readStream(run.orgId, run.sessionId, 0, 100)
      ).filter((event) => event.type.startsWith('input.'));
    const claimAs = (run: HarnessRun, worker: string) =>
      h.service.claim({
        orgId: run.orgId,
        runId: run.runId,
        workerId: worker,
        attemptId: `attempt-${worker}`,
        leaseDurationMs: 1_000,
      });
    const commit = (
      run: HarnessRun,
      worker: string,
      commandId: string,
      events: CommitBatch['events'],
      transition?: RunTransition,
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
        ...(transition !== undefined ? { transition } : {}),
        outbox: [],
      });
    const answered: RunTransition = {
      status: 'completed',
      outcome: 'answer',
    };

    beforeEach(() => h.setRollout(V2));

    it('ST-1: a message sent while the run is active joins it as input.queued, not a new run', async () => {
      const first = await h.admit(agentRun);
      const written = (await h.backend.transcripts?.written(first.run)) ?? [];
      const steered = await h.admit({
        ...agentRun,
        sessionId: first.sessionId,
        ...text('also check the 2025 policy'),
      });
      expect(steered.queued).to.deep.equal({ messageSeq: 1 });
      expect(steered.created).to.equal(true);
      expect(steered.run.runId).to.equal(first.run.runId);
      expect(steered.sessionId).to.equal(first.sessionId);
      const active = await h.service.getActive(
        h.tenant.orgId,
        first.sessionId,
      );
      expect(active?.runId).to.equal(first.run.runId);
      expect(active?.pendingInputs).to.deep.equal([1]);
      // No second run, no dispatch, no conversation message: the run's transcript places it.
      expect(
        await h.backend.dispatches(h.tenant.orgId, first.run.runId),
      ).to.have.length(1);
      if (h.backend.transcripts !== undefined) {
        expect(await h.backend.transcripts.written(first.run)).to.deep.equal(
          written,
        );
      }
      const [queued] = await queuedEvents(first.run);
      expect([queued?.type, queued?.writer, queued?.runId]).to.deep.equal([
        'input.queued',
        'node',
        first.run.runId,
      ]);
      expect(queued?.data).to.deep.equal({
        messageSeq: 1,
        by: `p_${h.tenant.actorId}`,
        textRef: inline('also check the 2025 policy'),
      });
    });

    it('ST-1: a retried message (same idempotency key) is the same messageSeq; a changed one conflicts', async () => {
      const first = await h.admit(agentRun);
      const request = {
        ...agentRun,
        sessionId: first.sessionId,
        idempotencyKey: 'steer-1',
        ...text('one more thing'),
      };
      const queued = await h.admit(request);
      const replay = await h.admit(request);
      expect(replay).to.deep.equal({ ...queued, created: false });
      const next = await h.admit({
        ...agentRun,
        sessionId: first.sessionId,
        ...text('and another'),
      });
      expect(next.queued).to.deep.equal({ messageSeq: 2 });
      expect(
        (await queuedEvents(first.run)).map((e) => e.data?.['messageSeq']),
      ).to.deep.equal([1, 2]);
      expect(
        await conflictCode(h.admit({ ...request, ...text('changed') })),
      ).to.equal('IDEMPOTENCY_CONFLICT');
    });

    it('ST-6: a message meant for the running task (steerOnly) never starts a run', async () => {
      const first = await h.admit(agentRun);
      const steered = await h.admit({
        ...agentRun,
        sessionId: first.sessionId,
        steerOnly: true,
      });
      expect(steered.queued).to.deep.equal({ messageSeq: 1 });
      const { run } = await claimAs(first.run, 'worker-a');
      await commit(
        run,
        'worker-a',
        'done',
        [workerEvent('input.consumed', { messageSeq: 1 })],
        answered,
      );
      expect(
        await conflictCode(
          h.admit({ ...agentRun, sessionId: first.sessionId, steerOnly: true }),
        ),
      ).to.equal('RUN_NOT_ACTIVE');
      expect(
        await conflictCode(h.admit({ ...agentRun, steerOnly: true }, 'never')),
      ).to.equal('RUN_NOT_ACTIVE');
      expect(await h.service.get(h.tenant.orgId, 'never')).to.equal(null);
      expect(
        await h.service.getActive(h.tenant.orgId, first.sessionId),
      ).to.equal(null);
    });

    it('ST-1: two messages sent at once to an idle session: one starts the run, the other joins it', async () => {
      const first = await h.admit(agentRun);
      const { run } = await claimAs(first.run, 'worker-a');
      await commit(run, 'worker-a', 'done', [], answered);
      const both = await Promise.all([
        h.admit({ ...agentRun, sessionId: first.sessionId, ...text('one') }),
        h.admit({ ...agentRun, sessionId: first.sessionId, ...text('two') }),
      ]);
      const started = both.filter((admission) => admission.queued === undefined);
      const joined = both.filter((admission) => admission.queued !== undefined);
      expect([started.length, joined.length]).to.deep.equal([1, 1]);
      expect(joined[0]!.run.runId).to.equal(started[0]!.run.runId);
      expect(joined[0]!.queued).to.deep.equal({ messageSeq: 1 });
    });

    it('ST-5: a suspended (approval) run takes the message too', async () => {
      const first = await h.admit(agentRun);
      const { run } = await claimAs(first.run, 'worker-a');
      await commit(
        run,
        'worker-a',
        'worker-a:suspend',
        [
          workerEvent('interaction.requested', {
            interactionId: 'ix-1',
            kind: 'approval',
            bindingDigest: `sha256:${'1'.repeat(64)}`,
          }),
        ],
        {
          status: 'waiting_approval',
          detail: {
            kind: 'suspend',
            reason: 'approval',
            interactionId: 'ix-1',
          },
        },
      );
      const steered = await h.admit({
        ...agentRun,
        sessionId: first.sessionId,
        ...text('go ahead'),
      });
      expect(steered.queued).to.deep.equal({ messageSeq: 1 });
      expect(steered.run.status).to.equal('waiting_approval');
    });

    it('ST-6/ST-7: an idle session starts a new run; a run being cancelled takes no message', async () => {
      const first = await h.admit(agentRun);
      const { run } = await claimAs(first.run, 'worker-a');
      await h.service.control({
        commandId: 'cancel-1',
        dispatchCommandId: 'dispatch-cancel-1',
        orgId: run.orgId,
        runId: run.runId,
        expectedRevision: run.revision,
        action: 'cancel',
      });
      expect(
        await conflictCode(
          h.admit({ ...agentRun, sessionId: first.sessionId }),
        ),
      ).to.equal('RUN_ACTIVE');
      expect(await queuedEvents(first.run)).to.deep.equal([]);
      const cancelling = (await h.service.get(run.orgId, run.runId))!;
      await commit(cancelling, 'worker-a', 'worker-a:cancelled', [], {
        status: 'cancelled',
      });
      const next = await h.admit({ ...agentRun, sessionId: first.sessionId });
      expect(next.queued).to.equal(undefined);
      expect(next.run.runId).not.to.equal(first.run.runId);
    });

    it('ST-1: a message the active run cannot take stays a conflict (a v1 run)', async () => {
      h.setRollout(HARNESS_ENABLED);
      const v1 = await h.admit();
      expect(
        await conflictCode(h.admit({ sessionId: v1.sessionId })),
      ).to.equal('RUN_ACTIVE');
    });

    it('ST-12 (C-21c): text over the inline bound is claim-checked, sealed, and reads back whole', async () => {
      const first = await h.admit(agentRun);
      // The chat limit in characters, three bytes each: over the journal's inline bound once base64'd.
      const long = '\u20ac'.repeat(100_000);
      const steered = await h.admit({ ...agentRun, sessionId: first.sessionId, ...text(long) });
      expect(steered.queued).to.deep.equal({ messageSeq: 1 });
      const [stored] = await queuedEvents(first.run);
      expect(stored?.data, 'stored by ref, not inline').to.equal(undefined);
      expect(stored?.payloadRef?.size).to.be.greaterThan(JOURNAL_INLINE_PAYLOAD_MAX_BYTES);
      const [read] = await new JournalService(h.backend.payloads, testCipher()).resolve([stored!]);
      expect(read?.data).to.deep.equal({ messageSeq: 1, by: `p_${h.tenant.actorId}`, textRef: inline(long) });
      const short = await h.admit({ ...agentRun, sessionId: first.sessionId, ...text('short') });
      const [, inlined] = await queuedEvents(short.run);
      expect(inlined?.payloadRef).to.equal(undefined);
      expect(inlined?.data).to.deep.equal({ messageSeq: 2, by: `p_${h.tenant.actorId}`, textRef: inline('short') });
    });

    describe('ST-13 (C-21b): attachments and records by reference, authorized as the caller', () => {
      const FILE = {
        recordId: 'rec-file',
        virtualRecordId: 'vr-file',
        recordName: 'plan.pdf',
        mimeType: 'application/pdf',
      };
      const withRefs = (
        attachments: Array<Record<string, unknown>>,
        recordIds: string[] = [],
      ) => ({
        input: {
          query: 'compare with the attached plan',
          recordIds,
          filters: {},
          attachments,
          model: { chatMode: 'auto' },
        },
      });
      let access: InMemoryRecordReadAccess;
      beforeEach(() => {
        access = new InMemoryRecordReadAccess()
          .grant('Bearer user', 'rec-file', 'rec-kb')
          .content('rec-file', 'vr-file');
        h.setRecordAccess(access);
      });
      const refusedAs = async (work: Promise<unknown>): Promise<unknown> => {
        try {
          await work;
        } catch (error) {
          return error;
        }
        return expect.fail('expected a refusal');
      };

      it('readable: queued with the references; the check used the caller credential', async () => {
        const first = await h.admit(agentRun);
        const steered = await h.admit({
          ...agentRun,
          sessionId: first.sessionId,
          ...withRefs([{ ...FILE }], ['rec-kb']),
        });
        expect(steered.queued).to.deep.equal({ messageSeq: 1 });
        const [queued] = await queuedEvents(first.run);
        expect(queued?.data).to.deep.include({
          messageSeq: 1,
          attachments: [FILE],
          recordIds: ['rec-kb'],
        });
        expect(access.queries).to.deep.equal([
          {
            authorization: 'Bearer user',
            sources: [
              { kind: 'attachment', recordId: 'rec-file', virtualRecordId: 'vr-file' },
              { kind: 'indexed', recordId: 'rec-kb' },
            ],
          },
        ]);
        // A retry replays the receipt: nothing is checked or queued again.
        const replay = await h.admit({
          ...agentRun,
          sessionId: first.sessionId,
          idempotencyKey: 'refs-1',
          ...withRefs([{ ...FILE }]),
        });
        const again = await h.admit({
          ...agentRun,
          sessionId: first.sessionId,
          idempotencyKey: 'refs-1',
          ...withRefs([{ ...FILE }]),
        });
        expect(again).to.deep.equal({ ...replay, created: false });
        expect(access.queries).to.have.length(2);
      });

      // R36-COVER: a file the caller may not read keeps the message, as on a new turn; the worker reads it again with
      // the caller's access and the answer names the file it was not given (ADR-RUN-03 D5).
      for (const [name, refs] of [
        ['an attachment of another user', withRefs([{ recordId: 'rec-other', virtualRecordId: 'vr-other' }])],
        ['a readable record naming another file', withRefs([{ recordId: 'rec-file', virtualRecordId: 'vr-other' }])],
      ] as Array<[string, ReturnType<typeof withRefs>]>) {
        it(`queued with ${name}, by reference, for the worker to name`, async () => {
          const first = await h.admit(agentRun);
          const steered = await h.admit({ ...agentRun, sessionId: first.sessionId, ...refs });
          expect(steered.queued).to.deep.equal({ messageSeq: 1 });
          const [queued] = await queuedEvents(first.run);
          expect(queued?.data).to.deep.include({ attachments: refs.input.attachments });
        });
      }

      for (const [name, refs, grant] of [
        ['a record the caller cannot read', withRefs([], ['rec-secret']), null],
        ['an unknown record', withRefs([], ['rec-missing']), 'missing'],
      ] as Array<[string, ReturnType<typeof withRefs>, string | null]>) {
        it(`403 for ${name}; nothing queued, nothing counted`, async () => {
          if (grant === 'missing') access.missing('rec-missing');
          const first = await h.admit(agentRun);
          const error = await refusedAs(
            h.admit({ ...agentRun, sessionId: first.sessionId, ...refs }),
          );
          expect(error).to.be.instanceOf(HarnessForbiddenError);
          expect(await queuedEvents(first.run)).to.deep.equal([]);
          expect((await h.service.getActive(h.tenant.orgId, first.sessionId))?.pendingInputs ?? []).to.deep.equal([]);
        });
      }

      it('fails closed: the authority refusing is 403, unreachable or absent is 503', async () => {
        const first = await h.admit(agentRun);
        const steer = () =>
          h.admit({ ...agentRun, sessionId: first.sessionId, ...withRefs([{ ...FILE }]) });
        access.failWith = 'refused';
        expect(await refusedAs(steer())).to.be.instanceOf(HarnessForbiddenError);
        access.failWith = 'unavailable';
        expect(await refusedAs(steer())).to.be.instanceOf(HarnessUnavailableError);
        access.failWith = null;
        access.dropAnswers = true;
        expect(await refusedAs(steer())).to.be.instanceOf(HarnessForbiddenError);
        h.setRecordAccess(undefined);
        expect(await refusedAs(steer())).to.be.instanceOf(HarnessUnavailableError);
        expect(await refusedAs(h.admit({ ...agentRun, sessionId: first.sessionId, ...withRefs([{ ...FILE }]), authorization: undefined }))).to.be.instanceOf(HarnessForbiddenError);
        expect(await queuedEvents(first.run)).to.deep.equal([]);
      });

      it('a new turn with attachments is admitted as before, without this check', async () => {
        await h.admit({ ...agentRun, ...withRefs([{ recordId: 'rec-anything' }]) });
        expect(access.queries).to.deep.equal([]);
      });
    });

    it('ST-1: never another actor: a colleague cannot steer (or see) the session', async () => {
      const first = await h.admit(agentRun);
      const colleague = {
        orgId: h.tenant.orgId,
        actorId: new Types.ObjectId().toString(),
      };
      let refused: unknown;
      try {
        await h.admit({
          ...agentRun,
          ...colleague,
          sessionId: first.sessionId,
        });
      } catch (error) {
        refused = error;
      }
      expect(refused).to.be.instanceOf(HarnessNotFoundError);
      expect(await queuedEvents(first.run)).to.deep.equal([]);
    });

    it('ST-3: a completion is refused while input is queued; consuming it (or leaving it) completes', async () => {
      const first = await h.admit(agentRun);
      const { run } = await claimAs(first.run, 'worker-a');
      await h.admit({
        ...agentRun,
        sessionId: first.sessionId,
        ...text('wait'),
      });
      expect(
        await conflictCode(commit(run, 'worker-a', 'c-1', [], answered)),
      ).to.equal('INPUT_PENDING');
      const consumed = await commit(run, 'worker-a', 'c-2', [
        workerEvent('input.consumed', { messageSeq: 1 }),
      ]);
      expect(consumed.run.pendingInputs).to.deep.equal([]);
      await h.admit({
        ...agentRun,
        sessionId: first.sessionId,
        ...text('one more'),
      });
      expect(
        await conflictCode(commit(run, 'worker-a', 'c-3', [], answered)),
      ).to.equal('INPUT_PENDING');
      const done = await commit(run, 'worker-a', 'c-4', [], {
        ...answered,
        leavesInput: true,
      });
      expect(done.run.status).to.equal('completed');
      expect(done.run.pendingInputs).to.deep.equal([2]);
    });

    it('ST-3: consuming in the completing commit completes; a failure is never refused for input', async () => {
      const first = await h.admit(agentRun);
      const { run } = await claimAs(first.run, 'worker-a');
      await h.admit({
        ...agentRun,
        sessionId: first.sessionId,
        ...text('wait'),
      });
      const done = await commit(
        run,
        'worker-a',
        'c-1',
        [workerEvent('input.consumed', { messageSeq: 1 })],
        answered,
      );
      expect(done.run.status).to.equal('completed');

      const other = await h.admit(agentRun);
      const claimed = await claimAs(other.run, 'worker-b');
      await h.admit({
        ...agentRun,
        sessionId: other.sessionId,
        ...text('late'),
      });
      const failed = await commit(claimed.run, 'worker-b', 'f-1', [], {
        status: 'failed',
        errorType: 'model_failed',
      });
      expect(failed.run.status).to.equal('failed');
    });

    describe('ST-11 (C-21): every ending keeps the messages the run never took', () => {
      const kept = (runId: string) => ({
        type: 'user_input',
        content: 'keep me',
        messageSeq: 1,
        runId,
        consumed: false,
      });
      const answerEvent = (outcome: string, answer: string, extra: Record<string, unknown> = {}) =>
        workerEvent(
          'x.pipeshub.answer.finalized',
          { outcome, answer, partTexts: [answer], citations: [], confidence: 'Low', ...extra },
          { critical: false },
        );
      /** A claimed run with one message queued it has not taken. */
      const steered = async () => {
        const first = await h.admit(agentRun);
        const { run } = await claimAs(first.run, 'worker-a');
        await h.admit({ ...agentRun, sessionId: first.sessionId, ...text('keep me') });
        return run;
      };
      const saved = async (run: HarnessRun) => h.backend.transcripts!.written(run);

      beforeEach(function () {
        if (h.backend.transcripts === undefined) this.skip();
      });

      for (const [name, transition] of [
        ['a worker failure without an answer', { status: 'failed', errorType: 'model_failed', detail: { kind: 'fail' } }],
        ['capability_lost', { status: 'failed', errorType: 'capability_lost', detail: { kind: 'fail' } }],
        ['quarantine', { status: 'failed', detail: { kind: 'quarantine', attempts: 5, lastErrorType: 'crash' } }],
      ] as Array<[string, RunTransition]>) {
        it(`${name}: the error message keeps it, marked not consumed`, async () => {
          const run = await steered();
          await commit(run, 'worker-a', 'end', [], transition);
          const written = await saved(run);
          expect(written.map((m) => m['messageType'])).to.deep.equal(['error']);
          expect(written[0]!['parts']).to.deep.equal([kept(run.runId)]);
        });
      }

      it('a failure keeps the messages it took too, where they were', async () => {
        const run = await steered();
        await commit(run, 'worker-a', 'took', [workerEvent('input.consumed', { messageSeq: 1 })]);
        await h.admit({ ...agentRun, sessionId: run.sessionId, ...text('keep me') });
        await commit(run, 'worker-a', 'end', [], { status: 'failed', errorType: 'model_failed', detail: { kind: 'fail' } });
        expect((await saved(run))[0]!['parts']).to.deep.equal([
          { type: 'user_input', content: 'keep me', messageSeq: 1, runId: run.runId },
          { ...kept(run.runId), messageSeq: 2 },
        ]);
      });

      it('a failed model call: the error answer keeps it after the answer', async () => {
        const run = await steered();
        await commit(run, 'worker-a', 'end', [answerEvent('error', 'The model failed.', { answerMatchType: 'Error', errorCode: 'model_failed' })], {
          status: 'failed',
          errorType: 'model_failed',
          detail: { kind: 'fail' },
        });
        const parts = (await saved(run))[0]!['parts'] as Array<Record<string, unknown>>;
        expect(parts[parts.length - 1]).to.deep.equal(kept(run.runId));
      });

      it('the sweeper failing it at its deadline: the error answer keeps it', async () => {
        const run = await steered();
        h.setNow(START + DEADLINE_MS);
        expect(await h.service.sweep(10)).to.equal(1);
        const written = await saved(run);
        expect(written.map((m) => m['messageType'])).to.deep.equal(['error']);
        const parts = written[0]!['parts'] as Array<Record<string, unknown>>;
        expect(parts[parts.length - 1]).to.deep.equal(kept(run.runId));
      });

      it('a cancel the worker finishes, and one no worker holds: the stopped reply keeps it', async () => {
        const run = await steered();
        await h.service.control({
          commandId: 'cancel-1',
          dispatchCommandId: 'dispatch-cancel-1',
          orgId: run.orgId,
          runId: run.runId,
          expectedRevision: (await h.service.get(run.orgId, run.runId))!.revision,
          action: 'cancel',
        });
        const cancelling = (await h.service.get(run.orgId, run.runId))!;
        await commit(cancelling, 'worker-a', 'cancelled', [], { status: 'cancelled' });
        expect((await saved(run))[0]!['parts']).to.deep.equal([kept(run.runId)]);

        const idle = await h.admit(agentRun);
        await h.admit({ ...agentRun, sessionId: idle.sessionId, ...text('keep me') });
        const queued = (await h.service.get(idle.run.orgId, idle.run.runId))!;
        await h.service.control({
          commandId: 'cancel-2',
          dispatchCommandId: 'dispatch-cancel-2',
          orgId: queued.orgId,
          runId: queued.runId,
          expectedRevision: queued.revision,
          action: 'cancel',
        });
        const written = await saved(idle.run);
        expect(written[0]!['status']).to.equal('stopped');
        expect(written[0]!['parts']).to.deep.equal([kept(idle.run.runId)]);
      });

      it('out of turns: the answer keeps it after the answer', async () => {
        const run = await steered();
        await commit(run, 'worker-a', 'end', [answerEvent('answered', 'Partial answer.')], { ...answered, leavesInput: true });
        const parts = (await saved(run))[0]!['parts'] as Array<Record<string, unknown>>;
        expect(parts[parts.length - 1]).to.deep.equal(kept(run.runId));
      });

      it('a question asked before the run started: the clarification keeps it', async () => {
        const run = await steered();
        const toolData = { name: 'ask_user_question', questions: [{ question: 'Which region?' }] };
        await commit(
          run,
          'worker-a',
          'asked',
          [
            workerEvent('x.pipeshub.question.asked', { callId: 'pre-run', status: 'success', toolData }, { critical: false }),
            answerEvent('needs_input', 'Which region?', { answerMatchType: 'Clarification Needed' }),
          ],
          { status: 'completed', outcome: 'needs_input', leavesInput: true },
        );
        const written = await saved(run);
        expect(written.map((m) => m['messageType'])).to.deep.equal(['tool_call', 'bot_response']);
        expect(written[1]!['parts']).to.deep.equal([kept(run.runId)]);
      });

      it('a run that took every message leaves no extra part (unchanged)', async () => {
        const first = await h.admit(agentRun);
        const { run } = await claimAs(first.run, 'worker-a');
        await commit(run, 'worker-a', 'end', [], { status: 'failed', errorType: 'model_failed', detail: { kind: 'fail' } });
        // Mongoose defaults an absent array to [].
        expect((await saved(run))[0]!['parts'] ?? []).to.deep.equal([]);
      });
    });
  });
}
