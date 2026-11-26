/**
 * ExecutionStore contract, part of `describeExecutionStoreContract`. ADR-UI-01 (UI-B1): a person's runs across their
 * conversations (G1), the run state of their chat list, and the operator's stop kept on the run (G11), on every store.
 */
import { expect } from 'chai';
import {
  CommitBatch,
  HarnessRun,
} from '../../../../../src/modules/agent-harness/domain/execution';
import {
  PersonRunQuery,
  runListState,
} from '../../../../../src/modules/agent-harness/domain/run-lists';
import type { RunLists } from '../../../../../src/modules/agent-harness/ports/run-lists';
import { HARNESS_ENABLED } from '../rollout';
import { workerEvent } from '../journal-fixtures';
import { ContractHarness } from './harness';

export function describeRunListCases(harness: () => ContractHarness): void {
  describe('UI-B1: run lists (ADR-UI-01) and the operator stop on the run (G11)', () => {
    let h: ContractHarness;
    let lists: RunLists;
    beforeEach(() => {
      h = harness();
      h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 as const });
      lists = h.backend.executionStore as unknown as RunLists;
    });

    const query = (overrides: Partial<PersonRunQuery> = {}): PersonRunQuery => ({
      orgId: h.tenant.orgId,
      actorId: h.tenant.actorId,
      limit: 50,
      ...overrides,
    });
    const ids = async (overrides: Partial<PersonRunQuery> = {}) =>
      (await lists.personRuns(query(overrides))).map(({ run }) => run.runId);

    const cancel = (run: HarnessRun, commandId: string, stop?: { scope: 'session'; reason: 'maintenance' }) =>
      h.service.control({
        commandId,
        dispatchCommandId: `${commandId}:dispatch`,
        orgId: run.orgId,
        runId: run.runId,
        expectedRevision: run.revision,
        action: 'cancel',
        ...(stop !== undefined ? { stop, by: 'p_admin-1' } : {}),
      });

    /** A claimed run that asked its person (an approval nobody answered yet). */
    const asking = async (at: number): Promise<HarnessRun> => {
      h.setNow(at);
      const admitted = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user' });
      const run = await h.claim(admitted.run, 'worker-a', 60_000);
      const batch: CommitBatch = {
        commandId: `ask-${run.runId}`,
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'worker-a',
        attemptId: 'attempt-worker-a',
        fence: run.fence,
        events: [
          workerEvent('interaction.requested', {
            interactionId: 'ix.approve.t1.0',
            kind: 'approval',
            bindingDigest: `sha256:${'1'.repeat(64)}`,
          }),
        ],
        legacyEvents: [],
        outbox: [],
      };
      return (await h.service.commit(batch)).run;
    };

    it("UIB1-01: a person's runs across their conversations, newest first; never another person's or another org's", async () => {
      const at = (value: number) => h.setNow(value);
      at(2_000);
      const first = (await h.admit()).run;
      at(3_000);
      const second = (await h.admit()).run;
      at(4_000);
      const third = (await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user' })).run;
      const peer = h.newTenant();
      await h.admit({ actorId: peer.actorId });
      await h.admit({ orgId: peer.orgId, actorId: h.tenant.actorId });

      expect(await ids()).to.deep.equal([third.runId, second.runId, first.runId]);
      expect(await ids({ agentKey: 'agent-1' })).to.deep.equal([third.runId]);
      expect(await ids({ createdFrom: 3_000 })).to.deep.equal([third.runId, second.runId]);
      expect(await ids({ createdTo: 3_000 })).to.deep.equal([first.runId]);
      expect(await ids({ kind: 'root' })).to.deep.equal([third.runId, second.runId, first.runId]);
      expect(await ids({ kind: 'job' })).to.deep.equal([]);
    });

    it('UIB1-02: a page continues after its cursor with nothing repeated or skipped, also across a tie of creation time', async () => {
      h.setNow(5_000);
      const tied = [(await h.admit()).run, (await h.admit()).run, (await h.admit()).run];
      h.setNow(6_000);
      const newest = (await h.admit()).run;
      const all = await ids();
      expect(all[0]).to.equal(newest.runId);
      expect([...all.slice(1)].sort()).to.deep.equal(tied.map((run) => run.runId).sort());
      const seen: string[] = [];
      let after: PersonRunQuery['after'];
      for (let page = 0; page < 5; page += 1) {
        const runs = await lists.personRuns(query({ limit: 1, ...(after !== undefined ? { after } : {}) }));
        if (runs.length === 0) break;
        const last = runs[runs.length - 1]!;
        seen.push(...runs.map(({ run }) => run.runId));
        after = { createdAt: last.run.createdAt, runId: last.run.runId };
      }
      expect(seen).to.deep.equal(all);
    });

    it('UIB1-03: the state filter reads a run as its list word: working, needs you (an open ask), finished, failed, stopped', async () => {
      h.setNow(7_000);
      const working = await h.claim((await h.admit()).run);
      const waiting = await asking(8_000);
      h.setNow(9_000);
      const stopped = (await h.admit()).run;
      await cancel(stopped, 'uib1-03-stop');

      expect(runListState(waiting)).to.equal('needs_you');
      expect(await ids({ states: ['needs_you'] })).to.deep.equal([waiting.runId]);
      expect(await ids({ states: ['working'] })).to.deep.equal([working.runId]);
      expect(await ids({ states: ['stopped'] })).to.deep.equal([stopped.runId]);
      expect(await ids({ states: ['finished', 'failed'] })).to.deep.equal([]);
      expect(await ids({ states: ['working', 'needs_you'] })).to.deep.equal([waiting.runId, working.runId]);
      for (const { run, engine } of await lists.personRuns(query())) {
        expect(engine, 'a loop run holds no engine').to.equal(null);
        const expected = run.runId === waiting.runId ? 'needs_you' : run.runId === working.runId ? 'working' : 'stopped';
        expect(runListState(run), run.runId).to.equal(expected);
      }
    });

    it("UIB1-05: a chat list's facts: the open runs of its conversations (anyone's), and the person's run that ended last in each", async () => {
      h.setNow(10_000);
      const open = await h.claim((await h.admit()).run);
      h.setNow(11_000);
      const ended = (await h.admit()).run;
      h.setNow(12_000);
      await cancel(ended, 'uib1-05-stop');
      const peer = h.newTenant();
      const foreign = (await h.admit({ orgId: peer.orgId, actorId: peer.actorId })).run;

      const live = await lists.openRunsOfSessions(h.tenant.orgId, [open.sessionId, ended.sessionId, foreign.sessionId]);
      expect(live.map((run) => run.runId)).to.deep.equal([open.runId]);
      const last = await lists.lastEndedRuns(h.tenant.orgId, h.tenant.actorId, [open.sessionId, ended.sessionId, foreign.sessionId]);
      expect(last).to.deep.equal([{ sessionId: ended.sessionId, runId: ended.runId, status: 'cancelled', endedAt: 12_000 }]);
      expect(await lists.lastEndedRuns(peer.orgId, h.tenant.actorId, [ended.sessionId])).to.deep.equal([]);
      expect(await lists.openRunsOfSessions(h.tenant.orgId, [])).to.deep.equal([]);
    });

    it("UIB1-06 (G11): an operator's stop stays on the run with its scope, reason and time; the person's own Stop leaves none, and a later stop never rewrites it", async () => {
      h.setNow(13_000);
      const queued = (await h.admit()).run;
      const { run: cancelled } = await cancel(queued, 'uib1-06-stop', { scope: 'session', reason: 'maintenance' });
      expect(cancelled.operatorStop).to.deep.equal({ scope: 'session', reason: 'maintenance', at: 13_000 });
      expect((await h.service.get(queued.orgId, queued.runId))?.operatorStop).to.deep.equal({
        scope: 'session',
        reason: 'maintenance',
        at: 13_000,
      });

      const running = await h.claim((await h.admit()).run);
      h.setNow(14_000);
      const { run: cancelling } = await cancel(running, 'uib1-06-running', { scope: 'session', reason: 'maintenance' });
      expect(cancelling.status).to.equal('cancelling');
      expect(cancelling.operatorStop).to.deep.equal({ scope: 'session', reason: 'maintenance', at: 14_000 });

      const own = (await h.admit()).run;
      const { run: byPerson } = await cancel(own, 'uib1-06-own');
      expect(byPerson.operatorStop).to.equal(undefined);
      expect((await lists.personRuns(query({ states: ['stopped'] }))).find(({ run }) => run.runId === queued.runId)?.run.operatorStop?.reason).to.equal(
        'maintenance',
      );
    });
  });
}
