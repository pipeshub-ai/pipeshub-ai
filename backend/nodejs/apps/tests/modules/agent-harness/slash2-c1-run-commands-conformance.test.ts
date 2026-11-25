/**
 * SLASH2 C1 conformance, Node's half, on the loop (L), opencode (O), Codex (X)
 * and Claude Code (C): the built-ins call what their buttons call, so the case
 * runs at that layer for a run pinned to each executor.
 *
 * - `/stop` is the Stop button's cancel (`SessionRunService.control`, no
 *   revision): mid-turn the run goes `cancelling`, the worker's acknowledgement
 *   ends it `cancelled` with exactly one `run.cancelled`, and a repeated
 *   `/stop` is the same command.
 * - `/model` and `/effort` set the picker the next admission reads: the next
 *   run's pin carries the new model and effort (an engine's model gateway grant
 *   is read from that same `spec.model`), and the earlier run's pin is unchanged.
 *
 * The workers' halves: the loop's and the engine kernel's cancel are
 * `test_slash2_c1_stop.py`; each engine adapter's interrupt is the engine
 * contract's cf8, which every adapter lane runs.
 */
import { expect } from 'chai';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { SessionRunService } from '../../../src/modules/agent-harness/application/session-run-service';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { HarnessRun, RunInputSnapshot } from '../../../src/modules/agent-harness/domain/execution';
import { runSpecResolver } from '../../../src/modules/agent-harness/domain/run-spec';
import { grantingAccess } from './support/agent-definition';
import { HARNESS_ENABLED } from './support/rollout';
import { testCipher } from './support/data-protection';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);

const engine = (kind: string) => ({
  kind: 'engine' as const,
  engine: {
    kind,
    version: '1.0.0',
    binaryDigest: `sha256:${'a'.repeat(64)}`,
    profileDigest: `sha256:${'b'.repeat(64)}`,
  },
});

const EXECUTORS = [
  ['L (durable loop)', { kind: 'loop' as const }],
  ['O (opencode)', engine('opencode')],
  ['X (codex)', engine('codex')],
  ['C (claude_code)', engine('claude_code')],
] as const;

const input = (model: RunInputSnapshot['model']): RunInputSnapshot => ({
  query: 'q',
  recordIds: [],
  filters: {},
  attachments: [],
  model,
});

for (const [name, executor] of EXECUTORS) {
  describe(`SLASH2 C1 conformance on ${name}`, () => {
    let now: number;
    let store: InMemoryExecutionStore;
    let service: ExecutionService;
    let stop: SessionRunService;

    const admit = async (model: RunInputSnapshot['model'], sessionId?: string): Promise<HarnessRun> => {
      const outcome = await new RunAdmissionService({
        store,
        rollout: {
          decide: async () => V2,
          decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }),
        },
        clock: { now: () => now },
        agentAccess: grantingAccess(),
      }).submit({
        orgId: 'org-1',
        actorId: 'user-1',
        agentKey: 'agent-1',
        authorization: 'Bearer t',
        idempotencyKey: `k-${String((now += 1))}`,
        deadlineMs: 60_000,
        ...(sessionId !== undefined ? { sessionId } : {}),
        input: input(model),
      });
      if (outcome.route !== 'durable') expect.fail('expected a durable run');
      return outcome.admission.run;
    };

    const claim = (run: HarnessRun) =>
      service.claim({
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'w',
        attemptId: `attempt-${run.runId}`,
        leaseDurationMs: 60_000,
      });

    const types = async (run: HarnessRun) =>
      (await store.journal.readRun(run.orgId, run.runId, 0, 200)).map((event) => event.type);

    beforeEach(() => {
      now = 1_000;
      store = new InMemoryExecutionStore({ now: () => now });
      service = new ExecutionService({
        store,
        journal: store.journal,
        journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
        capabilities: { current: () => DURABLE },
        resolveSpec: runSpecResolver(() => executor),
      });
      stop = new SessionRunService(service, { findStreamAccess: async () => ({ audience: 'owner' }) } as never);
    });

    it('/stop mid-turn ends the run cancelled with exactly one run.cancelled; a second /stop is the same command', async () => {
      const run = await admit({ chatMode: 'auto' });
      const claimed = await claim(run);
      expect(claimed.pinned?.spec.executor).to.deep.equal(executor);
      const scope = { orgId: run.orgId, actorId: 'user-1', sessionId: run.sessionId, runId: run.runId };
      const requested = await stop.control({ ...scope, idempotencyKey: `stop-${run.runId}`, action: 'cancel' });
      expect(requested.run.status).to.equal('cancelling');
      const again = await stop.control({ ...scope, idempotencyKey: `stop-${run.runId}`, action: 'cancel' });
      expect(again).to.deep.include({ replayed: true });
      await service.commit({
        commandId: 'worker:cancelled',
        orgId: run.orgId,
        runId: run.runId,
        workerId: 'w',
        attemptId: claimed.run.attemptId ?? '',
        fence: claimed.run.fence,
        events: [],
        legacyEvents: [],
        transition: { status: 'cancelled' },
        outbox: [],
      });
      expect((await service.get(run.orgId, run.runId))?.status).to.equal('cancelled');
      expect((await types(run)).filter((type) => type === 'run.cancelled')).to.have.length(1);
    });

    it('/model and /effort land in the next run\'s pin; the earlier run\'s pin is unchanged', async () => {
      const first = await admit({ chatMode: 'auto', modelKey: 'model-1', reasoningEffort: 'low' });
      const before = (await claim(first)).pinned;
      expect(before?.spec.model).to.deep.include({ reasoningEffort: 'low' });
      expect(before?.spec.model.policy[0]).to.deep.include({ model: 'gpt-x' });
      await service.commit({
        commandId: 'worker:completed',
        orgId: first.orgId,
        runId: first.runId,
        workerId: 'w',
        attemptId: `attempt-${first.runId}`,
        fence: (await service.get(first.orgId, first.runId))?.fence ?? 0,
        events: [],
        legacyEvents: [],
        transition: { status: 'completed', outcome: 'answer' },
        outbox: [],
      });

      const next = await admit({ chatMode: 'auto', modelKey: 'model-2', reasoningEffort: 'high' }, first.sessionId);
      expect(next.sessionId).to.equal(first.sessionId);
      const after = (await claim(next)).pinned;
      expect(after?.spec.executor).to.deep.equal(executor);
      expect(after?.spec.model).to.deep.include({ reasoningEffort: 'high' });
      expect(after?.spec.model.policy[0]).to.deep.equal({ provider: 'anthropic', model: 'claude-x' });

      const kept = await service.pinned(first.orgId, first.runId);
      expect(kept?.pinned?.specDigest).to.equal(before?.specDigest);
      expect(kept?.pinned?.spec.model).to.deep.equal(before?.spec.model);
    });
  });
}
