/**
 * ExecutionStore contract, part of `describeExecutionStoreContract`. WP-64 (G-13 x G-03..G-05): the turn that answers
 * a run's question is held to the asking run's gates and budget.
 *
 * A run that asks a question ends `needs_input`; the person's answer, from the chat's question card or the inbox, is
 * the conversation's next run. That run was admitted with only its own gates, so a dry run that asked acted for real
 * once answered, a plan-gated run's answer ran without a plan, and a firing's spending cap fell back to the default.
 * The answer now carries every gate and the cap of the run it answers, and may only add to them.
 */
import { expect } from 'chai';
import { HarnessConflictError, HarnessNotFoundError } from '../../../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../../../src/modules/agent-harness/domain/execution';
import {
  RUN_GATES_FEATURE,
  type RunGates,
} from '../../../../../src/modules/agent-harness/domain/run-gates';
import { HARNESS_ENABLED, KILL_SWITCH } from '../rollout';
import { workerEvent } from '../journal-fixtures';
import { ContractHarness, DEADLINE_MS } from './harness';
import type { AdmitRunCommand } from '../../../../../src/modules/agent-harness/domain/admission';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DRY: RunGates = { dryRun: true };
const PLAN: RunGates = { planApproval: 'required' };
const CHECKS: RunGates = {
  verify: { checks: [{ kind: 'citations' }], onFail: 'flag' },
};
const agentRun = { agentKey: 'agent-1', authorization: 'Bearer user' };

export function describeAnswerCases(harness: () => ContractHarness): void {
  describe('WP-64: an answer keeps the asking run’s gates and budget', () => {
    let h: ContractHarness;
    beforeEach(() => {
      h = harness();
      h.setRollout(V2);
    });

    /** Claims the run and ends it asking the person a question, as the worker does. */
    const ask = async (admitted: HarnessRun, worker = 'worker-a'): Promise<HarnessRun> => {
      const run = await h.claim(admitted, worker);
      await h.service.commit({
        commandId: `${worker}:asked:${run.runId}`,
        orgId: run.orgId,
        runId: run.runId,
        workerId: worker,
        attemptId: `attempt-${worker}`,
        fence: run.fence,
        events: [
          workerEvent('x.pipeshub.question.asked', { callId: 'call-ask', status: 'success', toolData: 'Which region?' }, { critical: false }),
          workerEvent(
            'x.pipeshub.answer.finalized',
            { outcome: 'needs_input', answer: 'Which region?', partTexts: ['Which region?'], citations: [], confidence: null },
            { critical: false },
          ),
        ],
        legacyEvents: [],
        transition: { status: 'completed', outcome: 'needs_input' },
        outbox: [],
      });
      return run;
    };

    /** Claims the run and ends it with an answer. */
    const answer = async (admitted: HarnessRun, worker = 'worker-a'): Promise<void> => {
      const run = await h.claim(admitted, worker);
      await h.service.commit({
        commandId: `${worker}:answered:${run.runId}`,
        orgId: run.orgId,
        runId: run.runId,
        workerId: worker,
        attemptId: `attempt-${worker}`,
        fence: run.fence,
        events: [
          workerEvent(
            'x.pipeshub.answer.finalized',
            { outcome: 'answered', answer: 'Done.', partTexts: ['Done.'], citations: [], confidence: null },
            { critical: false },
          ),
        ],
        legacyEvents: [],
        transition: { status: 'completed', outcome: 'answer' },
        outbox: [],
      });
    };

    /** The spec the run's claim pinned, claiming it first when nothing has. */
    const pinned = async (run: HarnessRun) => {
      const read = () => h.backend.executionStore.readClaimSubject(run.orgId, run.runId);
      if ((await read())?.pinned == null) await h.claim(run, 'pin-reader');
      const pin = (await read())?.pinned;
      expect(pin, 'the claim pins the spec').to.not.equal(null);
      return pin!.spec;
    };

    /** A run that asked, with these gates; its session. */
    const asked = async (gates?: RunGates, extra: Record<string, unknown> = {}) => {
      const admitted = await h.admit({ ...agentRun, ...(gates !== undefined ? { gates } : {}), ...extra });
      return ask(admitted.run);
    };

    it('WP64-AG-1: a dry run that asks and is answered stays a dry run', async () => {
      const question = await asked(DRY);
      const reply = await h.admit({ ...agentRun, sessionId: question.sessionId });
      expect(reply.run.gates).to.deep.equal(DRY);
      expect(reply.run.requires?.features).to.include(RUN_GATES_FEATURE);
      expect((await pinned(reply.run)).gates).to.deep.equal(DRY);
      // The store returns the gates it keeps on the run.
      expect((await h.service.get(reply.run.orgId, reply.run.runId))?.gates).to.deep.equal(DRY);
    });

    it('WP64-AG-2: a plan-gated run’s answer still needs the plan, and checks carry over too', async () => {
      const question = await asked({ ...PLAN, ...CHECKS });
      const reply = await h.admit({ ...agentRun, sessionId: question.sessionId });
      expect((await pinned(reply.run)).gates).to.deep.equal({ ...PLAN, ...CHECKS });
    });

    it('WP64-AG-3: an answer only narrows: it adds its own gates and never drops the asking run’s', async () => {
      const question = await asked(PLAN);
      const reply = await h.admit({ ...agentRun, sessionId: question.sessionId, gates: DRY });
      expect((await pinned(reply.run)).gates).to.deep.equal({ ...DRY, ...PLAN });
    });

    it('WP64-AG-4: the asking run’s budget carries over, to an answer of the answer too', async () => {
      const trigger = { scheduleId: 'sch-1', firingId: 'fir-1', slotKey: 'slot-1', maxCostUsd: 1.25 };
      const question = await asked(undefined, { trigger });
      expect((await pinned(question)).budgets.maxCostUsd).to.equal(1.25);
      const reply = await h.admit({ ...agentRun, sessionId: question.sessionId });
      await ask(reply.run, 'worker-b');
      const spec = await pinned(reply.run);
      expect(spec.budgets.maxCostUsd).to.equal(1.25);
      // The answer is the person's turn, not another firing.
      expect(spec.runKind).to.not.equal('scheduled');
      const again = await h.admit({ ...agentRun, sessionId: question.sessionId });
      expect((await pinned(again.run)).budgets.maxCostUsd).to.equal(1.25);
    });

    it('WP64-AG-5: the inbox (naming the run) and the chat card (the next message) admit the same answer', async () => {
      const fromInbox = await asked({ ...DRY, ...PLAN }, { trigger: { scheduleId: 's', firingId: 'f1', slotKey: 'k1', maxCostUsd: 2 } });
      const fromCard = await asked({ ...DRY, ...PLAN }, { trigger: { scheduleId: 's', firingId: 'f2', slotKey: 'k2', maxCostUsd: 2 } });
      const inbox = await h.admit({ ...agentRun, sessionId: fromInbox.sessionId, answers: fromInbox.runId });
      const card = await h.admit({ ...agentRun, sessionId: fromCard.sessionId });
      const [a, b] = [await pinned(inbox.run), await pinned(card.run)];
      expect(a.gates).to.deep.equal({ ...DRY, ...PLAN });
      expect(b.gates).to.deep.equal(a.gates);
      expect(a.budgets.maxCostUsd).to.equal(2);
      expect(b.budgets.maxCostUsd).to.equal(2);
    });

    it('WP64-AG-6: an answer naming another org’s run, or another conversation’s, is refused and nothing is admitted', async () => {
      const question = await asked(DRY);
      const stranger = h.newTenant();
      const theirs = await h.admit({ ...agentRun, ...stranger });
      const refused = await h
        .admit({ ...agentRun, sessionId: theirs.run.sessionId, ...stranger, answers: question.runId })
        .catch((error: unknown) => error);
      expect(refused).to.be.instanceOf(HarnessNotFoundError);
      const other = await h.admit(agentRun);
      await answer(other.run, 'worker-b');
      const elsewhere = await h
        .admit({ ...agentRun, sessionId: other.run.sessionId, answers: question.runId })
        .catch((error: unknown) => error);
      expect(elsewhere).to.be.instanceOf(HarnessNotFoundError);
      expect(await h.service.getActive(h.tenant.orgId, other.run.sessionId)).to.equal(null);
    });

    it('WP64-AG-7: a turn after a run that answered is not an answer: it keeps only its own gates', async () => {
      const admitted = await h.admit({ ...agentRun, gates: DRY });
      await answer(admitted.run);
      const next = await h.admit({ ...agentRun, sessionId: admitted.run.sessionId });
      expect(next.run.gates).to.equal(undefined);
      expect((await pinned(next.run)).gates).to.equal(undefined);
    });

    it('WP64-AG-9 (FUQUICK-FU-1): a queued message is decided as an answer when it drains, from what the store holds then', async () => {
      const trigger = { scheduleId: 'sch-9', firingId: 'fir-9', slotKey: 'slot-9', maxCostUsd: 0.75 };
      const question = await asked(DRY, { trigger });
      const queued = (extra: Record<string, unknown> = {}) =>
        ({
          orgId: question.orgId,
          actorId: question.actorId,
          agentKey: agentRun.agentKey,
          sessionId: question.sessionId,
          idempotencyKey: `queued-${Object.keys(extra).length}`,
          absoluteDeadlineAt: h.clock.now() + DEADLINE_MS,
          via: 'session',
          input: { query: 'EU', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'agent' } },
          ...extra,
        }) as unknown as AdmitRunCommand;
      // Queued before the question: nothing it answered then, the question it drains after.
      const drained = await h.readmit(queued());
      expect(drained.gates).to.deep.equal(DRY);
      expect(drained.answered).to.deep.equal({ runId: question.runId, maxCostUsd: 0.75 });
      // Queued naming a run (the inbox), it answers that run as the store holds it now, never a stale copy.
      const named = await h.readmit(queued({ answered: { runId: question.runId, maxCostUsd: 9 }, gates: PLAN }));
      expect(named.gates).to.deep.equal({ ...DRY, ...PLAN });
      expect(named.answered).to.deep.equal({ runId: question.runId, maxCostUsd: 0.75 });
      // A turn after an answered run is not an answer when it drains either.
      const plain = await h.admit({ ...agentRun, gates: DRY });
      await answer(plain.run, 'worker-c');
      const after = await h.readmit({ ...queued(), sessionId: plain.run.sessionId } as AdmitRunCommand);
      expect(after.gates).to.equal(undefined);
      expect(after.answered).to.equal(undefined);
    });

    it('WP64-AG-8: an answer to a gated run is refused rather than run ungated on today’s loop', async () => {
      const question = await asked(DRY);
      h.setRollout(KILL_SWITCH);
      const refused = await h
        .submit({ ...agentRun, sessionId: question.sessionId, firstSubmission: true })
        .catch((error: unknown) => error);
      expect(refused).to.be.instanceOf(HarnessConflictError);
      expect((refused as HarnessConflictError).code).to.equal('GATES_NEED_RUNTIME');
      // An answer to an ungated run still goes to today's loop as before.
      h.setRollout(V2);
      const plain = await asked();
      h.setRollout(KILL_SWITCH);
      const legacy = await h.submit({ ...agentRun, sessionId: plain.sessionId, firstSubmission: true });
      expect(legacy.route).to.equal('legacy');
    });
  });
}
