/**
 * ExecutionStore contract, part of `describeExecutionStoreContract`. C4.10: the run-scoped dimensions Node applies
 * itself, read from the run's pin and kept on its document so both stores' own pipelines apply them.
 *
 * - `quotas.activeRunsPerOrg` / `quotas.activeRunsPerUser` (C410-Q*): an org's caps lower the release's active-run
 *   quotas at admission, never raise them.
 * - `interactions.waitS` (C410-W*): a run waiting for a person stays answerable for its pin's window, never longer
 *   than the deployment's `HARNESS_WAIT_FOR_PERSON_DAYS`.
 * - `recovery.autoResume` (C410-R*): off, a run whose worker's lease lapsed ends as `worker_lost` instead of being
 *   queued again; on (the release's), and without a pin, it resumes as before.
 * - `interactions.conversationGrants` (WP69-CG-*): off, Node itself refuses an answer "for this conversation", even
 *   when the worker's ask offered one, and writes nothing; allowing once still works.
 */
import { principalRefFor } from '../../../../../src/modules/agent-harness/domain/run-spec';
import { expect } from 'chai';
import { RELEASE_DEFAULTS } from '../../../../../src/modules/agent-harness/contracts/generated/core/policy-data';
import { canonicalDigest } from '../../../../../src/modules/agent-harness/contracts/canonical-json';
import { PolicyResolver } from '../../../../../src/modules/agent-harness/domain/policy';
import { PolicyPinning } from '../../../../../src/modules/agent-harness/application/admission/policy-pinning';
import { silentHarnessLogger } from '../../../../../src/modules/agent-harness/application/harness-log';
import { HarnessConflictError, HarnessQuotaExceededError } from '../../../../../src/modules/agent-harness/domain/errors';
import { HarnessRun, WAIT_FOR_PERSON_ENV } from '../../../../../src/modules/agent-harness/domain/execution';
import { sealPin, type ResolvedRunConfig } from '../../../../../src/modules/control-plane';
import { InteractionService } from '../../../../../src/modules/agent-harness/application/interaction-service';
import { InMemoryRunWakeup } from '../../../../../src/modules/agent-harness/adapters/in-memory-interactions';
import { CONVERSATION_GRANTS_OFF_MESSAGE } from '../../../../../src/modules/agent-harness/domain/run-policy-fields';
import { workerEvent } from '../journal-fixtures';
import { HARNESS_ENABLED } from '../rollout';
import { pinnerOf } from '../a-pin';
import { ContractHarness, START, revisionsOf } from './harness';

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** A pin resolved from the release and an org layer that sets `values`. */
export function pinWith(values: Record<string, unknown>): ResolvedRunConfig {
  const resolved = new PolicyResolver().resolve([
    { kind: 'release', values: RELEASE_DEFAULTS },
    { kind: 'org', values: { schemaVersion: 1, scope: { kind: 'org' }, ...values } },
    { kind: 'request', values: { budgets: { activeTimeS: 3_600 } } },
  ]);
  if (resolved.config === null) throw new Error(`the layers do not resolve: ${JSON.stringify(resolved.refusal)}`);
  return sealPin({
    schemaVersion: 1,
    headRevision: 0,
    routing: { path: 'durable', reason: null },
    scope: { agentKey: null, projectId: null },
    effectiveSet: { items: [], digest: canonicalDigest([]) },
    effectiveConfig: resolved.config,
    llm: null,
    deploymentDigest: canonicalDigest({}),
  });
}

export function describeRunPolicyCases(harness: () => ContractHarness): void {
  describe('C4.10: the dimensions Node applies from a run pin', () => {
    let h: ContractHarness;
    let savedWait: string | undefined;
    const pinned = (values: Record<string, unknown>): void => {
      const pin = pinWith(values);
      h.setPolicySteps([
        new PolicyPinning({
          mode: 'on',
          pinner: pinnerOf(async () => pin, (orgId) => revisionsOf(h.backend).heads(orgId)),
          metrics: { mismatch: () => undefined },
          logger: silentHarnessLogger,
        }),
      ]);
    };
    beforeEach(() => {
      h = harness();
      h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 as const });
      savedWait = process.env[WAIT_FOR_PERSON_ENV];
      delete process.env[WAIT_FOR_PERSON_ENV];
    });
    afterEach(() => {
      h.setPolicySteps([]);
      if (savedWait === undefined) delete process.env[WAIT_FOR_PERSON_ENV];
      else process.env[WAIT_FOR_PERSON_ENV] = savedWait;
    });

    const quotaRefusal = async (work: Promise<unknown>): Promise<HarnessQuotaExceededError> => {
      try {
        await work;
      } catch (error) {
        expect(error).to.be.instanceOf(HarnessQuotaExceededError);
        return error as HarnessQuotaExceededError;
      }
      return expect.fail('Expected the admission quota to refuse');
    };

    it("C410-Q1: the org's cap on a person's active runs refuses the run past it, with the release's retry-after", async () => {
      pinned({ quotas: { activeRunsPerUser: 1 } });
      await h.admit();
      const refused = await quotaRefusal(h.admit({}, 'over-the-cap'));
      expect([refused.scope, refused.quota, refused.retryAfterS]).to.deep.equal(['user', 'active_runs', 15]);
      expect(await h.service.get(h.tenant.orgId, 'over-the-cap')).to.equal(null);
    });

    it("C410-Q2: the org's cap on its own active runs counts every person of the org", async () => {
      pinned({ quotas: { activeRunsPerOrg: 1 } });
      await h.admit();
      const refused = await quotaRefusal(h.admit({ actorId: '64b000000000000000000002' }));
      expect([refused.scope, refused.quota]).to.deep.equal(['org', 'active_runs']);
    });

    /** An hour-long run that suspends for an approval ten minutes in. */
    const waiting = async (): Promise<{ run: HarnessRun; askedAt: number }> => {
      const admitted = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user', deadlineMs: HOUR });
      const run = await h.claim(admitted.run, 'worker-a', 20 * MINUTE);
      const askedAt = START + 10 * MINUTE;
      h.setNow(askedAt);
      const base = {
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'worker-a',
        attemptId: 'attempt-worker-a',
        fence: run.fence,
        legacyEvents: [],
        outbox: [],
      };
      await h.service.commit({
        ...base,
        commandId: 'ask',
        events: [workerEvent('interaction.requested', { interactionId: 'ix.1', kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` })],
      });
      const suspended = (
        await h.service.commit({
          ...base,
          commandId: 'suspend',
          events: [],
          transition: { status: 'waiting_approval', detail: { kind: 'suspend', reason: 'approval', interactionId: 'ix.1' } },
        })
      ).run;
      expect(suspended.status).to.equal('waiting_approval');
      return { run: suspended, askedAt };
    };

    it("C410-W1: a run waits for a person as long as its pin's interactions.waitS, not the deployment's 7 days", async () => {
      pinned({ interactions: { waitS: 2 * 3_600 } });
      const { run, askedAt } = await waiting();
      expect(run.absoluteDeadlineAt).to.equal(askedAt + 2 * HOUR);
      h.setNow(askedAt + 2 * HOUR + 1);
      expect(await h.service.sweep(10)).to.equal(1);
      expect((await h.service.get(run.orgId, run.runId))?.status).to.equal('failed');
    });

    it("C410-W2: a pin's wait longer than the deployment's keeps the deployment's", async () => {
      process.env[WAIT_FOR_PERSON_ENV] = '0.5';
      pinned({ interactions: { waitS: 30 * 86_400 } });
      const { run, askedAt } = await waiting();
      expect(run.absoluteDeadlineAt).to.equal(askedAt + DAY / 2);
    });

    it("C410-W3: a pin's wait shorter than the work left keeps the run's working deadline", async () => {
      pinned({ interactions: { waitS: 60 } });
      const admittedDeadline = START + HOUR;
      const { run } = await waiting();
      expect(run.absoluteDeadlineAt).to.equal(admittedDeadline);
    });

    const lapsedLease = async (): Promise<HarnessRun> => {
      const { run } = await h.admit();
      await h.claim(run, 'worker-a', 100);
      h.setNow(START + 100);
      return run;
    };

    it('C410-R1: recovery.autoResume off: a run whose worker was lost ends as worker_lost and is never queued again', async () => {
      pinned({ recovery: { autoResume: false } });
      const run = await lapsedLease();
      expect(await h.service.sweep(10)).to.equal(1);
      const ended = await h.service.get(run.orgId, run.runId);
      expect(ended?.status).to.equal('failed');
      expect((await h.backend.dispatches(run.orgId, run.runId)).map((d) => d.kind)).not.to.include('run_resume');
      const failed = (await h.service.readEvents(run.orgId, run.sessionId, 0, 100)).filter(
        (event) => event.runId === run.runId && event.type === 'run_failed',
      );
      expect(failed.map((event) => event.data)).to.deep.equal([{ reason: 'worker_lost' }]);
    });

    it('C410-R2: recovery.autoResume on in the pin (the release) resumes the run, as a run without a pin does', async () => {
      pinned({ recovery: { autoResume: true } });
      const run = await lapsedLease();
      expect(await h.service.sweep(10)).to.equal(1);
      expect((await h.service.get(run.orgId, run.runId))?.status).to.equal('queued');
    });

    /** A claimed run whose worker asked for `ix.1` and offered to hold the answer for the conversation. */
    const offeredForConversation = async (): Promise<HarnessRun> => {
      const admitted = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user' });
      const run = await h.claim(admitted.run, 'worker-a', 20 * MINUTE);
      await h.service.commit({
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'worker-a',
        attemptId: 'attempt-worker-a',
        fence: run.fence,
        legacyEvents: [],
        outbox: [],
        commandId: 'ask',
        events: [
          workerEvent(
            'interaction.explained',
            { interactionId: 'ix.1', rule: 'flow_external_write', toolId: 'slack@default/send_message', conversationGrant: true },
            { critical: false },
          ),
          workerEvent('interaction.requested', { interactionId: 'ix.1', kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` }),
        ],
      });
      return run;
    };
    const answer = (run: HarnessRun, commandId: string, scope?: 'conversation') =>
      new InteractionService(h.service, new InMemoryRunWakeup()).resolve({
        orgId: run.orgId,
        runId: run.runId,
        interactionId: 'ix.1',
        decision: 'approved',
        channel: 'web',
        // ADR-GRANT-01 D9: only the run's own person answers for the conversation.
        by: principalRefFor(run.actorId),
        commandId,
        ...(scope !== undefined ? { scope } : {}),
      });
    const journalTypes = async (run: HarnessRun): Promise<string[]> =>
      (await h.backend.journal.readStream(run.orgId, run.sessionId, 0, 100)).map((event) => event.type);

    it('WP69-CG-1: interactions.conversationGrants off: Node refuses an answer for the conversation the worker offered, and writes nothing', async () => {
      pinned({ interactions: { conversationGrants: false } });
      const run = await offeredForConversation();
      const before = await journalTypes(run);
      let refused: unknown;
      try {
        await answer(run, 'for-conversation', 'conversation');
      } catch (error) {
        refused = error;
      }
      expect(refused).to.be.instanceOf(HarnessConflictError);
      expect((refused as HarnessConflictError).code).to.equal('CONVERSATION_GRANT_NOT_OFFERED');
      expect((refused as Error).message).to.equal(CONVERSATION_GRANTS_OFF_MESSAGE);
      expect(await journalTypes(run)).to.deep.equal(before);
      // Allowing once is always possible.
      await answer(run, 'once');
      expect((await journalTypes(run)).slice(before.length)).to.deep.equal(['interaction.resolved']);
    });

    it('WP69-CG-2: conversationGrants on in the pin (the release) keeps the worker-offered grant', async () => {
      pinned({ interactions: { conversationGrants: true } });
      const run = await offeredForConversation();
      await answer(run, 'for-conversation', 'conversation');
      expect((await journalTypes(run)).slice(-2)).to.deep.equal(['interaction.resolved', 'interaction.granted']);
    });

    it('C410-R3: with autoResume off, a run past its deadline still ends as deadline_exceeded', async () => {
      pinned({ recovery: { autoResume: false } });
      const { run } = await h.admit({ deadlineMs: 50 });
      await h.claim(run, 'worker-a', 100);
      h.setNow(START + 100);
      expect(await h.service.sweep(10)).to.equal(1);
      const failed = (await h.service.readEvents(run.orgId, run.sessionId, 0, 100)).filter(
        (event) => event.runId === run.runId && event.type === 'run_failed',
      );
      expect(failed.map((event) => event.data)).to.deep.equal([{}]);
    });
  });
}
