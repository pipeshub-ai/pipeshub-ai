/**
 * ExecutionStore contract, part of `describeExecutionStoreContract`. Admission: idempotent creation, follow-ups, rollout and kill switch, chat-path metrics, spec pinning, quotas.
 */
import { expect } from 'chai';
import { randomUUID } from 'crypto';
import { Types } from 'mongoose';
import { HarnessNotFoundError, HarnessQuotaExceededError } from '../../../../../src/modules/agent-harness/domain/errors';
import { isRunKeyDigest } from '../../../../../src/modules/agent-harness/domain/run-key-digest';
import { HarnessRun } from '../../../../../src/modules/agent-harness/domain/execution';
import { HARNESS_ENABLED, KILL_SWITCH } from '../rollout';
import { ChatPath, ChatPathMetrics, ChatSurface, ChatTurnOutcome, ClaimSource } from '../../../../../src/modules/agent-harness/ports/chat-path-metrics';
import { JOURNAL_INLINE_PAYLOAD_MAX_BYTES } from '../../../../../src/modules/agent-harness/domain/journal';
import { agentDefinition as agentDefinitionFixture } from '../agent-definition';
import { ADMISSION_QUOTAS, AdmissionQuotas } from '../../../../../src/modules/agent-harness/domain/admission-quotas';
import { canonicalDigest } from '../../../../../src/modules/agent-harness/contracts/canonical-json';
import { principalRefFor } from '../../../../../src/modules/agent-harness/domain/run-spec';
import { ContractBackendFactory, ContractHarness, START, conflictCode, contractClock, createContractHarness, freshContractBackend, revisionsOf } from './harness';
import { PinStale } from '../../../../../src/modules/agent-harness/domain/admission';
import type { PolicyContext } from '../../../../../src/modules/agent-harness/application/admission/context';
import type { AdmissionStep } from '../../../../../src/modules/agent-harness/application/admission/pipeline';
import type { Heads } from '../../../../../src/modules/control-plane';
import { aPin } from '../a-pin';
import type { CompactRequests } from '../../../../../src/modules/agent-harness/domain/compact-requests';
import type { NextRunEraser } from '../../../../../src/modules/agent-harness/domain/next-runs';

/** What a replay of `first` answers: the stored admission, not the session `first` created. */
const replayOf = <T extends { createdSession?: unknown }>(first: T): Omit<T, 'createdSession'> & { created: false } => {
  const { createdSession: _created, ...stored } = first;
  _created;
  return { ...stored, created: false };
};

export function describeAdmissionCases(
  harness: () => ContractHarness,
  createBackend: ContractBackendFactory,
): void {
  let h: ContractHarness;
  beforeEach(() => {
    h = harness();
  });

  describe('admission (the only way runs are created)', () => {
    it('replays an identical admission and rejects a changed payload', async () => {
      const request = { idempotencyKey: 'admit-1' };
      const first = await h.admit(request);
      const replay = await h.admit(request);
      expect(first.created).to.equal(true);
      // WP88-FU-4: the conversation the admission created, as stored; a replay reads what is stored now.
      expect(first.createdSession).to.deep.equal({ title: first.run.input?.query.slice(0, 100) });
      expect(replay).to.deep.equal(replayOf(first));
      expect(
        await h.backend.dispatches(h.tenant.orgId, first.run.runId),
      ).to.have.length(1);

      expect(
        await conflictCode(
          h.admit({
            ...request,
            input: {
              query: 'changed',
              recordIds: [],
              filters: {},
              attachments: [],
              model: { chatMode: 'auto' },
            },
          }),
        ),
      ).to.equal('IDEMPOTENCY_CONFLICT');
    });

    it("QA15F-2: the run stores a keyed digest of the caller's key, never the key; a retry replays it, another key admits another run", async () => {
      const key = `caller-text:${randomUUID()}`;
      const first = await h.admit({ idempotencyKey: key });
      const stored = (await h.service.get(h.tenant.orgId, first.run.runId))!;
      expect(isRunKeyDigest(stored.idempotencyKey), stored.idempotencyKey).to.equal(true);
      expect(JSON.stringify(stored)).to.not.include(key);
      expect(first.run.idempotencyKey).to.equal(stored.idempotencyKey);
      const retry = await h.admit({ idempotencyKey: key });
      expect(retry).to.deep.include({ created: false, sessionId: first.sessionId });
      expect(retry.run.runId).to.equal(first.run.runId);
      const other = await h.admit({ idempotencyKey: `${key}-other` });
      expect(other.run.runId).to.not.equal(first.run.runId);
      const otherStored = (await h.service.get(h.tenant.orgId, other.run.runId))!;
      expect(isRunKeyDigest(otherStored.idempotencyKey)).to.equal(true);
      expect(otherStored.idempotencyKey).to.not.equal(stored.idempotencyKey);
    });

    it('allows one active root run per tenant session', async () => {
      const first = await h.admit();
      expect(
        await conflictCode(h.admit({ sessionId: first.sessionId })),
      ).to.equal('RUN_ACTIVE');
    });

    it('queues the run with a run_queued event and one run_ready dispatch', async () => {
      const admitted = await h.admit();
      expect(admitted.run.status).to.equal('queued');
      expect(admitted.run.revision).to.equal(0);
      const events = await h.service.readEvents(
        h.tenant.orgId,
        admitted.sessionId,
      );
      expect(events.map((event) => event.type)).to.deep.equal(['run_queued']);
      expect(events[0]?.eventSeq).to.equal(admitted.firstEventSeq);
      const dispatches = await h.backend.dispatches(
        h.tenant.orgId,
        admitted.run.runId,
      );
      expect(dispatches.map((item) => item.kind)).to.deep.equal([
        'run_ready',
      ]);
    });

    it('does not admit into another actor session', async () => {
      const first = await h.admit();
      await h.service.control({
        commandId: 'cancel-for-owner-check',
        dispatchCommandId: 'unused',
        orgId: h.tenant.orgId,
        runId: first.run.runId,
        expectedRevision: first.run.revision,
        action: 'cancel',
      });
      try {
        await h.admit({
          actorId: new Types.ObjectId().toString(),
          sessionId: first.sessionId,
        });
        expect.fail('Expected not found');
      } catch (error) {
        expect(error).to.be.instanceOf(HarnessNotFoundError);
      }
    });
  });

  describe('follow-ups of a completed run (UX5)', () => {
    const complete = async () => {
      const { run } = await h.admit();
      const claimed = await h.claim(run);
      await h.service.commit({
        commandId: 'complete-for-followups',
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'worker-a',
        attemptId: claimed.attemptId as string,
        fence: claimed.fence,
        expectedRevision: claimed.revision,
        status: 'completed',
        outcome: 'answer',
        events: [{ type: 'run_completed', data: { answer: 'done' } }],
        outbox: [],
      });
      return (await h.service.get(run.orgId, run.runId)) as HarnessRun;
    };
    const store = () => h.backend.executionStore;

    it('stores them once on the run document, leaving its revision alone', async () => {
      const run = await complete();
      const offered = [{ text: 'Compare with last quarter' }, { text: 'Draft an email' }];
      expect(await store().storeFollowups(run.orgId, run.runId, offered)).to.deep.equal({
        outcome: 'stored',
        followups: offered,
      });
      const after = await h.service.get(run.orgId, run.runId);
      expect(after?.followups).to.deep.equal(offered);
      expect(after?.revision).to.equal(run.revision);
      expect(after?.status).to.equal('completed');
    });

    it('the first write wins: a second one reads it back and writes nothing', async () => {
      const run = await complete();
      await store().storeFollowups(run.orgId, run.runId, [{ text: 'First' }]);
      expect(await store().storeFollowups(run.orgId, run.runId, [{ text: 'Second' }, { text: 'Third' }])).to.deep.equal({
        outcome: 'kept',
        followups: [{ text: 'First' }],
      });
      expect((await h.service.get(run.orgId, run.runId))?.followups).to.deep.equal([{ text: 'First' }]);
    });

    it('refuses a run that has not completed, and finds no run of another org', async () => {
      const { run } = await h.admit();
      expect(await store().storeFollowups(run.orgId, run.runId, [{ text: 'Early' }])).to.deep.equal({
        outcome: 'not_completed',
      });
      expect((await h.service.get(run.orgId, run.runId))?.followups).to.equal(undefined);
      const done = await complete();
      expect(await store().storeFollowups(h.newTenant().orgId, done.runId, [{ text: 'Elsewhere' }])).to.equal(null);
      expect(await store().storeFollowups(done.orgId, 'no-such-run', [{ text: 'Nowhere' }])).to.equal(null);
      expect((await h.service.get(done.orgId, done.runId))?.followups).to.equal(undefined);
    });
  });

  describe('a pin is admitted only at the revisions it was resolved at (PIN-22, CPD10-40)', () => {
    /** A pinning step whose pin was resolved at `at`, as the control plane's states it beside the pin. */
    const pinnedAt = (at: Heads | undefined): AdmissionStep<PolicyContext> => ({
      name: 'policy_pinning',
      run: async (context) => {
        context.resolved = { ...A_PIN, headRevision: at?.org ?? 0 };
        if (at !== undefined) context.resolvedAt = at;
      },
    });
    const A_PIN = aPin();
    const written = async (runId: string) => h.service.get(h.tenant.orgId, runId);
    let runId: string;

    beforeEach(() => {
      h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
      runId = randomUUID();
    });
    afterEach(() => h.setPolicySteps([]));

    it('at the org head and the deployment revision it was resolved at: admitted, and the run keeps it', async () => {
      await revisionsOf(h.backend).commit(h.tenant.orgId);
      await revisionsOf(h.backend).moveDeployment();
      const at = await revisionsOf(h.backend).heads(h.tenant.orgId);
      h.setPolicySteps([pinnedAt(at)]);
      const { run } = await h.admit({}, runId);
      expect((await h.backend.executionStore.readClaimSubject(run.orgId, run.runId))?.resolved).to.deep.equal({ ...A_PIN, headRevision: at.org });
    });

    it('CPD10-40: an AI-model save commits after the pin was resolved (the deployment revision moved, the org head did not): stale, nothing written', async () => {
      const at = await revisionsOf(h.backend).heads(h.tenant.orgId);
      await revisionsOf(h.backend).moveDeployment();
      expect((await revisionsOf(h.backend).heads(h.tenant.orgId)).org).to.equal(at.org);
      h.setPolicySteps([pinnedAt(at)]);
      const refused = await h.submit({}, runId).catch((error: unknown) => error);
      expect(refused).to.be.instanceOf(PinStale);
      expect(await written(runId)).to.equal(null);
    });

    it('PIN-22: a control-plane write of the org commits after the pin was resolved: stale, nothing written', async () => {
      const at = await revisionsOf(h.backend).heads(h.tenant.orgId);
      await revisionsOf(h.backend).commit(h.tenant.orgId);
      h.setPolicySteps([pinnedAt(at)]);
      expect(await h.submit({}, runId).catch((error: unknown) => error)).to.be.instanceOf(PinStale);
      expect(await written(runId)).to.equal(null);
    });

    it('another org’s write moves neither: admitted', async () => {
      const at = await revisionsOf(h.backend).heads(h.tenant.orgId);
      await revisionsOf(h.backend).commit(h.newTenant().orgId);
      h.setPolicySteps([pinnedAt(at)]);
      expect((await h.admit({}, runId)).run.runId).to.equal(runId);
    });

    it('a pin that does not say where it was resolved is not admitted', async () => {
      h.setPolicySteps([pinnedAt(undefined)]);
      expect(await h.submit({}, runId).catch((error: unknown) => error)).to.be.instanceOf(PinStale);
      expect(await written(runId)).to.equal(null);
    });
  });

  describe('SLASH2 C2: the /compact waiting on a session', () => {
    const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
    beforeEach(() => h.setRollout(V2));

    const compacts = (): CompactRequests => h.backend.admissionStore as unknown as CompactRequests;
    const ask = (focus?: string) => ({ ...(focus !== undefined ? { focus } : {}), requestedAt: START, requestedBy: 'p_user' });
    const finish = async (run: HarnessRun): Promise<void> => {
      const claimed = await h.claim(run);
      await h.service.commit({
        commandId: `complete-${run.runId}`,
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'worker-a',
        attemptId: claimed.attemptId as string,
        fence: claimed.fence,
        events: [],
        legacyEvents: [],
        transition: { status: 'completed', outcome: 'answer' },
        outbox: [],
      });
    };

    it('a session no run was admitted in has nothing to compact', async () => {
      expect(await compacts().requestCompact(h.tenant.orgId, new Types.ObjectId().toString(), ask())).to.equal(false);
    });

    it("the session's next run takes it in its input, once; the last request's focus wins; the run requires a worker that honours it", async () => {
      const first = await h.admit();
      expect(first.run.input?.compact).to.equal(undefined);
      await finish(first.run);
      expect(await compacts().requestCompact(h.tenant.orgId, first.sessionId, ask('old focus'))).to.equal(true);
      expect(await compacts().requestCompact(h.tenant.orgId, first.sessionId, ask('keep the numbers'))).to.equal(true);
      const next = await h.admit({ sessionId: first.sessionId });
      const stored = await h.service.get(h.tenant.orgId, next.run.runId);
      expect(stored?.input?.compact).to.deep.equal({ focus: 'keep the numbers' });
      expect(stored?.requires?.features).to.include('compact_request');
      await finish(next.run);
      const after = await h.admit({ sessionId: first.sessionId });
      const later = await h.service.get(h.tenant.orgId, after.run.runId);
      expect(later?.input?.compact).to.equal(undefined);
      expect(later?.requires?.features ?? []).to.not.include('compact_request');
    });

    it("erasing the session drops it with the session's waiting messages: its focus is the person's text", async () => {
      const first = await h.admit();
      await finish(first.run);
      expect(await compacts().requestCompact(h.tenant.orgId, first.sessionId, ask('keep the numbers'))).to.equal(true);
      await (h.backend.admissionStore as unknown as NextRunEraser).dropNextRuns(h.tenant.orgId, first.sessionId);
      const next = await h.admit({ sessionId: first.sessionId });
      expect((await h.service.get(h.tenant.orgId, next.run.runId))?.input?.compact).to.equal(undefined);
    });

    it('another org never reaches the session', async () => {
      const first = await h.admit();
      expect(await compacts().requestCompact(h.newTenant().orgId, first.sessionId, ask())).to.equal(false);
    });
  });

  describe('rollout pinning and kill switch (F2)', () => {
    it('pins the harness major on the admitted run record', async () => {
      const admitted = await h.admit();
      expect(admitted.run.harnessMajor).to.equal(1);
      expect(
        (await h.service.get(h.tenant.orgId, admitted.run.runId))
          ?.harnessMajor,
      ).to.equal(1);
    });

    it('routes a new admission to legacy and writes nothing', async () => {
      const first = await h.admit();
      const claimed = await h.claim(first.run);
      await h.service.commit({
        commandId: 'complete-before-kill',
        orgId: first.run.orgId,
        runId: first.run.runId,
        workerId: 'worker-a',
        attemptId: claimed.attemptId as string,
        fence: claimed.fence,
        expectedRevision: claimed.revision,
        status: 'completed',
        outcome: 'answer',
        events: [{ type: 'run_completed', data: {} }],
        outbox: [],
      });
      const eventsBefore = await h.service.readEvents(
        h.tenant.orgId,
        first.sessionId,
      );
      h.setRollout(KILL_SWITCH);

      const outcome = await h.submit(
        { sessionId: first.sessionId },
        'run-after-kill',
      );

      expect(outcome).to.deep.equal({
        route: 'legacy',
        rollout: KILL_SWITCH,
      });
      expect(await h.service.get(h.tenant.orgId, 'run-after-kill')).to.equal(
        null,
      );
      expect(
        await h.service.getActive(h.tenant.orgId, first.sessionId),
      ).to.equal(null);
      expect(
        await h.service.readEvents(h.tenant.orgId, first.sessionId),
      ).to.deep.equal(eventsBefore);
      expect(
        await h.backend.dispatches(h.tenant.orgId, 'run-after-kill'),
      ).to.deep.equal([]);
    });

    it('replays an already admitted submission instead of rerouting it', async () => {
      const request = { idempotencyKey: 'admitted-before-kill' };
      const first = await h.admit(request);
      h.setRollout(KILL_SWITCH);
      const retry = await h.submit(request);
      expect(retry).to.deep.equal({
        route: 'durable',
        admission: replayOf(first),
      });
    });

    it('lets an in-flight run be claimed, paused, resumed and completed', async () => {
      const { run } = await h.admit();
      h.setRollout(KILL_SWITCH);

      const claimed = await h.claim(run);
      const paused = await h.service.commit({
        commandId: 'pause-under-kill',
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
      const resumed = await h.service.control({
        commandId: 'resume-under-kill',
        dispatchCommandId: 'dispatch-resume-under-kill',
        orgId: run.orgId,
        runId: run.runId,
        expectedRevision: paused.run.revision,
        action: 'resume',
      });
      expect(resumed.run.status).to.equal('queued');
      const reclaimed = await h.claim(run, 'worker-b');
      const done = await h.service.commit({
        commandId: 'complete-under-kill',
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'worker-b',
        attemptId: reclaimed.attemptId as string,
        fence: reclaimed.fence,
        expectedRevision: reclaimed.revision,
        status: 'completed',
        outcome: 'answer',
        events: [{ type: 'run_completed', data: {} }],
        outbox: [],
      });
      expect(done.run.status).to.equal('completed');
      expect(done.run.harnessMajor).to.equal(1);
    });
  });

  describe('chat path metrics and mid-flight rollback (DUR3)', () => {
    type Turn = [ChatPath, ChatSurface, ChatTurnOutcome];
    const recording = () => {
      const seen = {
        turns: [] as Turn[],
        claims: [] as ClaimSource[],
        waits: [] as number[],
        replays: 0,
        quarantined: 0,
        approvals: [] as Array<[string, number]>,
      };
      const metrics: ChatPathMetrics = {
        turnEnded: (path, surface, outcome) => seen.turns.push([path, surface, outcome]),
        claimed: (from, firstClaimWaitS) => {
          seen.claims.push(from);
          if (firstClaimWaitS !== undefined) seen.waits.push(firstClaimWaitS);
        },
        commitReplayed: () => {
          seen.replays += 1;
        },
        modelCharged: () => undefined,
        runCacheRead: () => undefined,
        runQuarantined: () => {
          seen.quarantined += 1;
        },
        approvalsRequested: (orgId, count) => {
          if (count > 0) seen.approvals.push([orgId, count]);
        },
      };
      return { seen, metrics };
    };
    const complete = (run: HarnessRun, claimed: HarnessRun, commandId: string) =>
      h.service.commit({
        commandId,
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'worker-a',
        attemptId: claimed.attemptId as string,
        fence: claimed.fence,
        expectedRevision: claimed.revision,
        status: 'completed',
        outcome: 'answer',
        events: [{ type: 'run_completed', data: {} }],
        outbox: [],
      });

    it('DUR3-F1/F4: a durable run is one claim from the queue and one answered turn on the durable path', async () => {
      const { seen, metrics } = recording();
      h.service.measureWith(metrics);
      const { run } = await h.admit();
      const claimed = await h.claim(run);
      await complete(run, claimed, 'dur3-complete');
      expect(seen.claims).to.deep.equal(['queued']);
      expect(seen.turns).to.have.length(1);
      expect(seen.turns[0]?.[0]).to.equal('durable');
      expect(seen.turns[0]?.[2]).to.equal('answered');
    });

    it('DUR3-F4: a command sent again is a replay, never a second turn', async () => {
      const { seen, metrics } = recording();
      h.service.measureWith(metrics);
      const { run } = await h.admit();
      const claimed = await h.claim(run);
      await complete(run, claimed, 'dur3-twice');
      await complete(run, claimed, 'dur3-twice');
      expect(seen.replays).to.equal(1);
      expect(seen.turns).to.have.length(1);
    });

    it('OB-04: a first claim is timed from admission (claim latency); a take-over after a lost lease is not', async () => {
      const { seen, metrics } = recording();
      h.service.measureWith(metrics);
      const { run } = await h.admit();
      h.setNow(run.createdAt + 1_500);
      await h.claim(run);
      expect(seen.waits).to.deep.equal([1.5]);
      h.setNow(run.createdAt + 2_600);
      await h.claim(run, 'worker-b');
      expect(seen.claims).to.deep.equal(['queued', 'expired_lease']);
      expect(seen.waits, 'a resume is not a first claim').to.deep.equal([1.5]);
    });

    it('OB-05: a quarantined run is counted once, a replay of the command never again', async () => {
      const { seen, metrics } = recording();
      h.service.measureWith(metrics);
      h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
      const { run } = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user' });
      const claimed = await h.claim(run);
      const quarantine = () =>
        h.service.commit({
          commandId: 'ob05-quarantine',
          orgId: run.orgId,
          runId: run.runId,
          workerId: 'worker-a',
          attemptId: 'attempt-worker-a',
          fence: claimed.fence,
          events: [],
          legacyEvents: [],
          transition: {
            status: 'failed',
            errorType: 'crash_loop',
            detail: { kind: 'quarantine', attempts: 4, lastErrorType: 'crash_loop' },
          },
          outbox: [],
        });
      expect((await quarantine()).run.status).to.equal('failed');
      await quarantine();
      expect(seen.replays).to.equal(1);
      expect(seen.quarantined).to.equal(1);
    });

    it('SEC-D-13: the approvals a commit asks are counted by its org once, a replay of the command never again', async () => {
      const { seen, metrics } = recording();
      h.service.measureWith(metrics);
      h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
      const { run } = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user' });
      const claimed = await h.claim(run);
      const requested = (interactionId: string) => ({
        type: 'interaction.requested',
        schemaVersion: 1,
        critical: true,
        visibility: 'owner' as const,
        data: { interactionId, kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` },
      });
      const ask = () =>
        h.service.commit({
          commandId: 'sec-d-13-ask',
          orgId: run.orgId,
          runId: run.runId,
          workerId: 'worker-a',
          attemptId: claimed.attemptId as string,
          fence: claimed.fence,
          events: [requested('ix-a'), requested('ix-b')],
          legacyEvents: [],
          transition: {
            status: 'waiting_approval',
            detail: { kind: 'suspend', reason: 'approval', interactionId: 'ix-a' },
          },
          outbox: [],
        });
      expect((await ask()).run.status).to.equal('waiting_approval');
      await ask();
      expect(seen.replays).to.equal(1);
      expect(seen.approvals).to.deep.equal([[run.orgId, 2]]);
    });

    it('DUR3-F6: with the kill switch on, a queued durable run still ends cleanly (cancel) and counts as cancelled', async () => {
      const { seen, metrics } = recording();
      h.service.measureWith(metrics);
      h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
      const { run } = await h.admit();
      h.setRollout(KILL_SWITCH);
      const current = (await h.service.get(run.orgId, run.runId)) as HarnessRun;
      const cancelled = await h.service.control({
        commandId: 'dur3-cancel-under-kill',
        dispatchCommandId: 'dur3-cancel-under-kill:dispatch',
        orgId: run.orgId,
        runId: run.runId,
        expectedRevision: current.revision,
        action: 'cancel',
      });
      expect(cancelled.run.status).to.equal('cancelled');
      expect(seen.turns.map((t) => t[2])).to.deep.equal(['cancelled']);
      expect((await h.submit({ sessionId: run.sessionId }, 'dur3-after-kill')).route).to.equal('legacy');
    });
  });

  describe('spec pinning (P1.2)', () => {
    const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
    const agentRun = { agentKey: 'agent-1', authorization: 'Bearer user' };

    const claimAs = (run: HarnessRun, worker: string) =>
      h.service.claim({
        orgId: run.orgId,
        runId: run.runId,
        workerId: worker,
        attemptId: `attempt-${worker}`,
        leaseDurationMs: 1_000,
      });

    const journal = (run: HarnessRun) =>
      h.backend.journal.readStream(run.orgId, run.sessionId, 0, 100);

    beforeEach(() => h.setRollout(V2));

    it('EP-24: keeps the admitted engine profile for the claim (the turn\'s engine authority), never on the run', async () => {
      h.setAgentDefinition((agentKey) =>
        agentDefinitionFixture({
          agentKey,
          engineProfile: { engines: ['opencode'], limits: { maxSteps: 12 } },
        }),
      );
      const admitted = await h.admit(agentRun);
      const subject = await h.backend.executionStore.readClaimSubject(
        admitted.run.orgId,
        admitted.run.runId,
      );
      expect(subject?.engineProfile?.limits.maxSteps).to.equal(12);
      expect(subject?.run).to.not.have.property('engineProfile');
      // CTL-6: the turn runs on the loop; its engine child's access is decided when it hands off.
      expect(subject?.modelAccess ?? null).to.equal(null);
      expect(
        await h.backend.executionStore.get(admitted.run.orgId, admitted.run.runId),
      ).to.not.have.property('engineProfile');
      const spec = (await claimAs(admitted.run, 'worker-a')).pinned!.spec;
      expect(spec.executor).to.deep.equal({ kind: 'loop' });
      // The kept profile is the turn's engine authority, pinned for its code_task child.
      expect(
        (spec.extensions?.['x.pipeshub'] as { codeTask?: { profile: unknown } } | undefined)?.codeTask?.profile,
      ).to.deep.equal(subject?.engineProfile);
      h.setAgentDefinition((agentKey) => agentDefinitionFixture({ agentKey }));
      const loop = await h.admit(agentRun);
      const loopSubject = await h.backend.executionStore.readClaimSubject(loop.run.orgId, loop.run.runId);
      expect(loopSubject?.engineProfile ?? null).to.equal(null);
      expect(loopSubject?.modelAccess ?? null).to.equal(null);
    });

    it('VA-3/VA-5: run.admitted records the admitted via, and the pin carries the same', async () => {
      for (const via of ['session', 'pat', 'oauth_app', 'service'] as const) {
        const admitted = await h.admit({ ...agentRun, via });
        const claimed = await claimAs(admitted.run, `worker-${via}`);
        const [admittedEvent] = await journal(admitted.run);
        expect(admittedEvent?.type).to.equal('run.admitted');
        expect(admittedEvent?.data?.['via'], via).to.equal(via);
        expect(
          (claimed.pinned!.spec.extensions?.['x.pipeshub'] as Record<string, unknown>)['via'],
          via,
        ).to.equal(via);
      }
    });

    it('P-1/P-11: pins once on the first claim; a re-claim reads the pin even after the agent changed', async () => {
      const admitted = await h.admit(agentRun);
      const first = await claimAs(admitted.run, 'worker-a');
      const pinned = first.pinned!;
      expect(pinned.specDigest).to.equal(canonicalDigest(pinned.spec));
      expect(pinned.spec.capabilitiesDigest).to.equal(
        pinned.capabilitiesDigest,
      );
      expect(pinned.spec.principalRef).to.equal(
        principalRefFor(admitted.run.actorId),
      );
      expect(pinned.spec.extensions?.['x.pipeshub']?.['agent']).to.deep.equal(
        { agentKey: 'agent-1', definitionVersion: 7, mode: 'auto' },
      );

      // The agent is edited after admission: the run keeps its pinned grant.
      h.setAgentDefinition((agentKey) =>
        agentDefinitionFixture({
          agentKey,
          definitionVersion: 8,
          toolsets: [],
        }),
      );
      h.setNow(START + 1_001);
      const second = await claimAs(admitted.run, 'worker-b');
      expect(second.pinned).to.deep.equal(pinned);

      const events = await journal(admitted.run);
      expect(events.map((event) => [event.type, event.writer])).to.deep.equal(
        [
          ['run.admitted', 'node'],
          ['run.claimed', 'node'],
          ['run.spec_pinned', 'node'],
          ['run.claimed', 'node'],
          // The first attempt's lease lapsed under it: the takeover says so (CH-01).
          ['x.pipeshub.run.notice', 'node'],
        ],
      );
      expect(events[4]?.data).to.deep.equal({ code: 'resumed' });
      expect(events[0]?.data).to.deep.equal({
        runKind: 'interactive',
        principalRef: pinned.spec.principalRef,
        via: 'session',
      });
      expect(events[1]?.data).to.deep.equal({
        attempt: 1,
        fence: 1,
        leaseMs: 1_000,
      });
      expect(events[3]?.data).to.deep.equal({
        attempt: 2,
        fence: 2,
        leaseMs: 1_000,
      });
      expect(events[2]?.data).to.deep.equal({
        specDigest: pinned.specDigest,
        spec: pinned.spec,
        capabilities: pinned.capabilities,
      });
      expect([events[1]?.attemptId, events[1]?.fence]).to.deep.equal([
        'attempt-worker-a',
        1,
      ]);
      expect(first.journal.lastSeq[admitted.run.sessionId]).to.equal(3);
      expect(second.journal.lastSeq[admitted.run.sessionId]).to.equal(5);
    });

    it('P-2: client tools only narrow the pinned grant', async () => {
      const admitted = await h.admit({
        ...agentRun,
        input: {
          query: 'q',
          recordIds: [],
          filters: {},
          attachments: [],
          tools: ['jira.search_issues', 'slack.send_message'],
          model: { chatMode: 'auto' },
        },
      });
      const { pinned } = await claimAs(admitted.run, 'worker-a');
      expect(
        pinned!.spec.grants.tools.map((grant) => grant.toolId),
      ).to.deep.equal(['jira@inst-42/search_issues']);
    });

    it('P-7: a spec over the inline limit is claim-checked and reads back whole', async () => {
      const tools = Array.from({ length: 2_500 }, (_, index) => ({
        name: `tool_${String(index)}`,
        fullName: `bulk.tool_${String(index)}`,
      }));
      h.setAgentDefinition((agentKey) =>
        agentDefinitionFixture({
          agentKey,
          toolsets: [{ name: 'bulk', instanceId: 'inst-1', tools }],
        }),
      );
      const admitted = await h.admit(agentRun);
      const claim = await claimAs(admitted.run, 'worker-a');
      const [stored] = (await journal(admitted.run)).filter(
        (event) => event.type === 'run.spec_pinned',
      );
      expect(stored?.data).to.equal(undefined);
      expect(stored?.payloadRef?.size).to.be.greaterThan(
        JOURNAL_INLINE_PAYLOAD_MAX_BYTES,
      );
      const read = await h.service.readRunJournal({
        orgId: admitted.run.orgId,
        runId: admitted.run.runId,
        workerId: 'worker-a',
        attemptId: 'attempt-worker-a',
        fence: claim.run.fence,
        afterSeq: 0,
        limit: 10,
        scope: 'run',
      });
      const resolved = read.find((event) => event.type === 'run.spec_pinned');
      expect(resolved?.data?.['specDigest']).to.equal(
        claim.pinned!.specDigest,
      );
      expect(canonicalDigest(resolved?.data?.['spec'])).to.equal(
        claim.pinned!.specDigest,
      );
      expect(claim.pinned!.spec.grants.tools).to.have.length(2_500 + 2);
    });

    it('P-10: a harness major 1 run claims as before: no pin, no typed events', async () => {
      h.setRollout(HARNESS_ENABLED);
      const admitted = await h.admit(agentRun);
      const claim = await claimAs(admitted.run, 'worker-a');
      expect(claim).not.to.have.property('pinned');
      expect(await journal(admitted.run)).to.deep.equal([]);
    });

    it('refuses an unpinned v2 claim that brings no spec, and changes nothing', async () => {
      const admitted = await h.admit(agentRun);
      expect(
        await conflictCode(
          h.backend.executionStore.claim({
            orgId: admitted.run.orgId,
            runId: admitted.run.runId,
            workerId: 'worker-a',
            attemptId: 'attempt-a',
            leaseDurationMs: 1_000,
          }),
        ),
      ).to.equal('SPEC_NOT_PINNED');
      const current = await h.service.get(
        admitted.run.orgId,
        admitted.run.runId,
      );
      expect([current?.status, current?.fence]).to.deep.equal(['queued', 0]);
      expect(
        (await journal(admitted.run)).map((event) => event.type),
      ).to.deep.equal(['run.admitted']);
    });
  });

  describe('admission quotas (P1.14)', () => {
    const QUOTAS: AdmissionQuotas = {
      ...ADMISSION_QUOTAS,
      activeRunsPerUser: 2,
      activeRunsPerOrg: 3,
      admissionsPerUser: 4,
      admissionsPerOrg: 6,
      windowMs: 60_000,
      activeRetryAfterS: 15,
    };
    let q: ContractHarness;

    beforeEach(async () => {
      const clock = contractClock();
      q = createContractHarness(await freshContractBackend(createBackend, clock, { quotas: QUOTAS }), clock);
    });

    const refusal = async (
      work: Promise<unknown>,
    ): Promise<HarnessQuotaExceededError> => {
      try {
        await work;
      } catch (error) {
        expect(error).to.be.instanceOf(HarnessQuotaExceededError);
        return error as HarnessQuotaExceededError;
      }
      return expect.fail('Expected the admission quota to refuse');
    };

    const cancel = async (run: HarnessRun) => {
      await q.service.control({
        commandId: `cancel-${run.runId}`,
        dispatchCommandId: `dispatch-cancel-${run.runId}`,
        orgId: run.orgId,
        runId: run.runId,
        expectedRevision: run.revision,
        action: 'cancel',
      });
    };

    it('P-5: a full active-run quota refuses with retry-after and writes nothing', async () => {
      const first = await q.admit();
      await q.admit();
      const refused = await refusal(q.admit({}, 'refused-run'));
      expect([
        refused.scope,
        refused.quota,
        refused.retryAfterS,
      ]).to.deep.equal(['user', 'active_runs', 15]);
      expect(await q.service.get(q.tenant.orgId, 'refused-run')).to.equal(
        null,
      );
      expect(
        await q.backend.dispatches(q.tenant.orgId, 'refused-run'),
      ).to.deep.equal([]);
      // An ended run frees its slot.
      await cancel(first.run);
      expect((await q.admit()).created).to.equal(true);
    });

    it('P-5: the org quota counts every actor of the org', async () => {
      await q.admit();
      await q.admit();
      const colleague = {
        orgId: q.tenant.orgId,
        actorId: new Types.ObjectId().toString(),
      };
      await q.admit(colleague);
      const refused = await refusal(q.admit(colleague));
      expect([refused.scope, refused.quota]).to.deep.equal([
        'org',
        'active_runs',
      ]);
      // Another org is not affected.
      expect((await q.admit(q.newTenant())).created).to.equal(true);
    });

    it('P-5: the admission rate refuses until the window ends; refusals do not count', async () => {
      const held = [await q.admit(), await q.admit()];
      // Refused by the active quota; had it counted, the window would be full two admissions early.
      await refusal(q.admit());
      for (const admitted of held) await cancel(admitted.run);
      for (let index = 0; index < 2; index += 1) {
        await cancel((await q.admit()).run);
      }
      const limited = await refusal(q.admit());
      expect([
        limited.scope,
        limited.quota,
        limited.retryAfterS,
      ]).to.deep.equal(['user', 'admission_rate', 60]);
      q.setNow(START + 30_000);
      expect((await refusal(q.admit())).retryAfterS).to.equal(30);
      q.setNow(START + QUOTAS.windowMs);
      expect((await q.admit()).created).to.equal(true);
    });

    it('ST-Q: a steering message counts against the admission rate, not the active runs', async () => {
      q.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
      const agentRun = { agentKey: 'agent-1', authorization: 'Bearer user' };
      const first = await q.admit(agentRun);
      await q.admit(agentRun);
      // Both active runs are held: a new run is refused, a message to a running one is not.
      await refusal(q.admit(agentRun));
      const steer = () => q.admit({ ...agentRun, sessionId: first.sessionId });
      expect((await steer()).queued).to.deep.equal({ messageSeq: 1 });
      expect((await steer()).queued).to.deep.equal({ messageSeq: 2 });
      const limited = await refusal(steer());
      expect([limited.scope, limited.quota]).to.deep.equal([
        'user',
        'admission_rate',
      ]);
      expect(
        (await q.service.getActive(q.tenant.orgId, first.sessionId))
          ?.pendingInputs,
      ).to.deep.equal([1, 2]);
    });

    it('P-13: an idempotent replay is not counted and replays when the quota is full', async () => {
      const request = { idempotencyKey: 'replayed' };
      const first = await q.admit(request);
      await q.admit();
      await refusal(q.admit());
      const replay = await q.admit(request);
      expect(replay).to.deep.equal(replayOf(first));
    });

    it('P-12: concurrent admissions at the boundary admit exactly the quota', async () => {
      const outcomes = await Promise.allSettled(
        Array.from({ length: 8 }, () => q.admit()),
      );
      const admitted = outcomes.filter(
        (outcome) => outcome.status === 'fulfilled',
      );
      const refused = outcomes.filter(
        (outcome): outcome is PromiseRejectedResult =>
          outcome.status === 'rejected',
      );
      expect(admitted).to.have.length(QUOTAS.activeRunsPerUser);
      expect(
        refused.every(
          (outcome) => outcome.reason instanceof HarnessQuotaExceededError,
        ),
      ).to.equal(true);
    });

    it('DOS-MONGO-1 (QA21-1): fifty resends of one turn at once, past every quota of the person: one run, the others replay it', async () => {
      const outcomes = await Promise.allSettled(
        Array.from({ length: 50 }, () => q.admit({ idempotencyKey: 'resent-turn' })),
      );
      const answers = outcomes.map((outcome) => {
        if (outcome.status === 'rejected') expect.fail(String(outcome.reason));
        return outcome.value;
      });
      expect(answers.filter((answer) => answer.created)).to.have.length(1);
      expect(new Set(answers.map((answer) => answer.run.runId)).size).to.equal(1);
      // The resends took nothing: the person's next turn still has its places.
      expect((await q.admit()).created).to.equal(true);
    });

    it('SEC-DOS-01: one person opening 500 sessions at once gets their own cap; colleagues and other orgs still start', async () => {
      const flood = Array.from({ length: 500 }, () => q.admit());
      // In the same instant as the flood: its admissions in flight must not hold the org's places (DOS-MONGO-1).
      const colleague = q.admit({ orgId: q.tenant.orgId, actorId: new Types.ObjectId().toString() });
      const otherOrg = q.admit(q.newTenant());
      const outcomes = await Promise.allSettled(flood);
      const admitted = outcomes.filter((outcome) => outcome.status === 'fulfilled');
      const reasons = outcomes
        .filter((outcome): outcome is PromiseRejectedResult => outcome.status === 'rejected')
        .map((outcome) => outcome.reason as HarnessQuotaExceededError);
      expect(admitted).to.have.length(QUOTAS.activeRunsPerUser);
      expect(reasons).to.have.length(500 - QUOTAS.activeRunsPerUser);
      // Every refusal is the person's own quota: none spent the org's share, which their colleagues draw on.
      expect(
        reasons.every((reason) => reason instanceof HarnessQuotaExceededError && reason.scope === 'user'),
      ).to.equal(true);
      expect((await colleague).created).to.equal(true);
      expect((await otherOrg).created).to.equal(true);
    });
  });
}
