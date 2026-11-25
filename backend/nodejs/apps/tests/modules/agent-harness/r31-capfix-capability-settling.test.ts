/**
 * R31-CAPFIX (ADR-CAP-01, R31UIFU-FU-1): a capability nobody could ask about is unknown, not absent. A job admitted
 * while the sandbox broker is still starting, or before any worker has polled, must not lose its sandbox for good.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { InMemoryWorkerFeatureSightings } from '../../../src/modules/agent-harness/adapters/in-memory-worker-feature-sightings';
import {
  HarnessCapabilityService,
  settledCapabilities,
} from '../../../src/modules/agent-harness/application/capability-service';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { WorkerFeatureAvailability } from '../../../src/modules/agent-harness/application/worker-feature-availability';
import { agentHarnessHealth } from '../../../src/modules/agent-harness/config';
import {
  CAPABILITY_RETRY_MIN_MS,
  CAPABILITY_SETTLE_WINDOW_MS,
  capabilitySnapshot,
  CapabilitySnapshot,
  codeSandboxProof,
  DeploymentCapabilities,
  LEAST_CAPABILITIES,
} from '../../../src/modules/agent-harness/domain/capabilities';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { CapabilityProbe, CapabilitySource } from '../../../src/modules/agent-harness/ports/capability-probe';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { HARNESS_ENABLED } from './support/rollout';
import { testCipher } from './support/data-protection';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const READY_ENV = {
  REPLICA_SET_AVAILABLE: 'true',
  AGENT_HARNESS_RELAY_ENABLED: 'true',
  AGENT_HARNESS_WORKER_ENABLED: 'true',
};

class Lines {
  readonly lines: Array<{ level: string; message: string; meta: Record<string, unknown> }> = [];
  private log = (level: string) => (message: string, meta?: Record<string, unknown>) => {
    this.lines.push({ level, message, meta: meta ?? {} });
  };
  debug = this.log('debug');
  info = this.log('info');
  warn = this.log('warn');
  error = this.log('error');
  named(message: string): Array<Record<string, unknown>> {
    return this.lines.filter((l) => l.message === message).map((l) => l.meta);
  }
}

const journalProbe: CapabilityProbe = {
  name: 'journal',
  provides: ['journal.durable'],
  probe: async () => ({ 'journal.durable': true }),
};

/** The broker's health as a script: `fail` rounds that cannot ask, then the answer. */
const broker = (answer: DeploymentCapabilities['sandbox.isolation'] = 'container') => {
  const state = { fail: 0, calls: 0 };
  const probe: CapabilityProbe = {
    name: 'sandbox-isolation',
    provides: ['sandbox.isolation'],
    probe: async () => {
      state.calls += 1;
      if (state.fail > 0) {
        state.fail -= 1;
        throw new Error('The operation was aborted due to timeout');
      }
      return { 'sandbox.isolation': answer };
    },
  };
  return { state, probe };
};

describe('R31-CAPFIX: unknown is not none (ADR-CAP-01)', () => {
  afterEach(() => sinon.restore());

  describe('D1: the capability service', () => {
    it('CAPFIX-1: a round whose broker probe fails is unsettled, not a proof of none', async () => {
      const b = broker();
      b.state.fail = 1;
      const service = new HarnessCapabilityService([journalProbe, b.probe]);
      const failed = await service.refresh();
      expect(failed.capabilities['sandbox.isolation']).to.equal('none');
      expect(failed.unsettled).to.deep.equal(['sandbox.isolation']);
      expect(codeSandboxProof(failed)).to.equal('unknown');
      expect(service.settled()).to.equal(false);
      const answered = await service.refresh();
      expect(answered.unsettled).to.equal(undefined);
      expect(codeSandboxProof(answered)).to.equal('proved');
      expect(service.settled()).to.equal(true);
      // The pinned digest covers the capabilities only.
      expect(failed.digest).to.equal(capabilitySnapshot(failed.capabilities, 0).digest);
    });

    it('CAPFIX-2: an unsettled round is retried on a 2 s doubling backoff, capped at the period, reset when settled', async () => {
      const clock = sinon.useFakeTimers();
      const b = broker();
      b.state.fail = 3;
      const service = new HarnessCapabilityService([b.probe]);
      service.start(60_000);
      await service.refresh();
      expect(b.state.calls).to.equal(1);
      await clock.tickAsync(CAPABILITY_RETRY_MIN_MS);
      expect(b.state.calls).to.equal(2);
      await clock.tickAsync(2 * CAPABILITY_RETRY_MIN_MS - 1);
      expect(b.state.calls).to.equal(2);
      await clock.tickAsync(1);
      expect(b.state.calls).to.equal(3);
      await clock.tickAsync(4 * CAPABILITY_RETRY_MIN_MS);
      expect(b.state.calls).to.equal(4);
      expect(service.settled()).to.equal(true);
      // Settled: only the period asks again; a later failure starts the ladder from 2 s.
      await clock.tickAsync(30_000);
      expect(b.state.calls).to.equal(4);
      b.state.fail = 1;
      // The period's round at 60 s fails.
      await clock.tickAsync(16_000);
      expect(b.state.calls).to.equal(5);
      expect(service.settled()).to.equal(false);
      await clock.tickAsync(CAPABILITY_RETRY_MIN_MS);
      expect(b.state.calls).to.equal(6);
      expect(service.settled()).to.equal(true);
      service.stop();
      clock.restore();
    });

    it('CAPFIX-2b: the backoff never waits longer than the period', async () => {
      const clock = sinon.useFakeTimers();
      const b = broker();
      b.state.fail = 100;
      const service = new HarnessCapabilityService([b.probe]);
      service.start(5_000);
      await service.refresh();
      await clock.tickAsync(2_000 + 4_000);
      const before = b.state.calls;
      // Interval and retry both at most 5 s apart: two more rounds in 10 s at least.
      await clock.tickAsync(10_000);
      expect(b.state.calls - before).to.be.at.least(2);
      service.stop();
      clock.restore();
    });

    it('CAPFIX-3: a bounded wait returns at once when the capability is known, and after the bound when the round hangs', async () => {
      const known: CapabilitySource = { current: () => capabilitySnapshot({ ...LEAST_CAPABILITIES }, 1) };
      expect(codeSandboxProof(await settledCapabilities(known, 'sandbox.isolation', 50))).to.equal('none');
      const unknown = capabilitySnapshot({ ...LEAST_CAPABILITIES }, 1, ['sandbox.isolation']);
      const hanging: CapabilitySource = {
        current: () => unknown,
        refresh: () => new Promise<CapabilitySnapshot>(() => undefined),
      };
      const started = Date.now();
      expect(await settledCapabilities(hanging, 'sandbox.isolation', 50)).to.equal(unknown);
      expect(Date.now() - started).to.be.below(1_000);
    });
  });

  describe('D3: admission and the claim', () => {
    let now: number;
    let store: InMemoryExecutionStore;
    let logger: Lines;

    const admit = async (
      capabilities: CapabilitySource,
      workerFeatures?: WorkerFeatureAvailability,
    ): Promise<HarnessRun> => {
      const outcome = await new RunAdmissionService({
        store,
        rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
        clock: { now: () => now },
        agentAccess: grantingAccess((key) => agentDefinition({ agentKey: key })),
        capabilities,
        ...(workerFeatures !== undefined ? { workerFeatures } : {}),
        logger,
      }).submit({
        orgId: 'org-1',
        actorId: 'user-1',
        agentKey: 'agent-1',
        authorization: 'Bearer t',
        idempotencyKey: `k-${Math.random()}`,
        deadlineMs: 600_000,
        input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'quick' } },
      });
      if (outcome.route !== 'durable') expect.fail('expected a durable admission');
      return outcome.admission.run;
    };

    const executions = (capabilities: CapabilitySource) =>
      new ExecutionService({
        store,
        journal: store.journal,
        journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
        capabilities,
        clock: { now: () => now },
        logger,
      });

    const claim = (service: ExecutionService, run: HarnessRun, worker = 'w') =>
      service.claim({
        orgId: run.orgId,
        runId: run.runId,
        workerId: worker,
        attemptId: `attempt-${worker}-${Math.random()}`,
        leaseDurationMs: 30_000,
      });

    beforeEach(() => {
      now = 1_000;
      store = new InMemoryExecutionStore({ now: () => now });
      logger = new Lines();
    });

    it('CAPFIX-4: a failed boot probe followed by an admitted job does not pin no-sandbox: the bounded wait sees the broker answer', async () => {
      const b = broker();
      b.state.fail = 1;
      const service = new HarnessCapabilityService([journalProbe, b.probe]);
      await service.refresh();
      expect(codeSandboxProof(service.current())).to.equal('unknown');
      const run = await admit(service);
      expect(run.requires?.features).to.include('agent_code');
      expect(logger.named('run.agent_code_pending')).to.have.length(0);
      const claimed = await claim(executions(service), run);
      expect(claimed.pinned?.spec.grants.sandboxProfile).to.equal('agent-code');
    });

    it('CAPFIX-5: still unknown after the wait: admitted with the sandbox pending; the claim waits for the answer, then pins it', async () => {
      const b = broker();
      b.state.fail = 3;
      const service = new HarnessCapabilityService([journalProbe, b.probe]);
      await service.refresh();
      const run = await admit(service);
      expect(run.requires?.features).to.include('agent_code');
      expect(logger.named('run.agent_code_pending')[0]).to.include({ reason: 'capabilities_unsettled' });
      const exec = executions(service);
      const deferred = await claim(exec, run).catch((error: unknown) => error);
      expect(deferred).to.be.instanceOf(HarnessConflictError);
      expect((deferred as HarnessConflictError).code).to.equal('RUN_NOT_CLAIMABLE');
      expect(logger.named('run.claim_deferred')[0]).to.include({ reason: 'capabilities_unsettled' });
      expect((await store.get(run.orgId, run.runId))?.status).to.equal('queued');
      now += 5_000;
      const claimed = await claim(exec, run);
      expect(claimed.pinned?.spec.grants.sandboxProfile).to.equal('agent-code');
    });

    it('CAPFIX-6: a broker silent past the settle window: the claim pins none ([D-3]) and says so', async () => {
      const b = broker();
      b.state.fail = 100;
      const service = new HarnessCapabilityService([journalProbe, b.probe]);
      await service.refresh();
      const run = await admit(service);
      expect(run.requires?.features).to.include('agent_code');
      now += CAPABILITY_SETTLE_WINDOW_MS;
      const claimed = await claim(executions(service), run);
      expect(claimed.pinned?.spec.grants.sandboxProfile).to.equal('none');
      expect(logger.named('run.sandbox_unsettled')[0]).to.include({ outcome: 'pinned_without_sandbox' });
    });

    it('CAPFIX-7: a proved none withholds agent_code without waiting', async () => {
      const b = broker('none');
      const service = new HarnessCapabilityService([journalProbe, b.probe]);
      await service.refresh();
      const calls = b.state.calls;
      const run = await admit(service);
      expect(run.requires?.features).to.not.include('agent_code');
      expect(b.state.calls).to.equal(calls);
    });

    it('CAPFIX-8: a job admitted before any worker advertised agent_code keeps it; a late worker claims it with its sandbox', async () => {
      const proved: CapabilitySource = {
        current: () =>
          capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true, 'sandbox.isolation': 'container' }, 1),
      };
      const clock = { now: () => now };
      const availability = new WorkerFeatureAvailability({
        sightings: new InMemoryWorkerFeatureSightings(clock),
        clock,
      });
      const early = await admit(proved, availability);
      expect(early.requires?.features).to.include('agent_code');
      expect(logger.named('run.agent_code_pending')[0]).to.include({ reason: 'no_worker_seen' });
      expect(logger.named('run.agent_code_withheld')).to.have.length(0);
      await availability.observe(['chat', 'agent_code']);
      const claimed = await claim(executions(proved), early);
      expect(claimed.pinned?.spec.grants.sandboxProfile).to.equal('agent-code');
    });

    it('CAPFIX-8b: a live worker without agent_code proves absence: withheld, as DUR2 has it', async () => {
      const proved: CapabilitySource = {
        current: () =>
          capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true, 'sandbox.isolation': 'container' }, 1),
      };
      const clock = { now: () => now };
      const availability = new WorkerFeatureAvailability({
        sightings: new InMemoryWorkerFeatureSightings(clock),
        clock,
      });
      await availability.observe(['chat']);
      const run = await admit(proved, availability);
      expect(run.requires?.features).to.not.include('agent_code');
      expect(logger.named('run.agent_code_withheld')[0]).to.include({ reason: 'no_live_worker' });
    });
  });

  describe('D4: readiness', () => {
    it('CAPFIX-9: health says settling until every probe answered, then settled; never unhealthy', async () => {
      const b = broker();
      b.state.fail = 1;
      const service = new HarnessCapabilityService([journalProbe, b.probe]);
      const durable = { durable: true as const, reason: 'majority' as const };
      const health = () => agentHarnessHealth(READY_ENV, durable, () => service.settled());
      expect(health()).to.deep.equal({ status: 'available', capabilities: 'settling' });
      await service.refresh();
      expect(health()).to.deep.equal({ status: 'available', capabilities: 'settling' });
      await service.refresh();
      expect(health()).to.deep.equal({ status: 'available', capabilities: 'settled' });
      expect(agentHarnessHealth(READY_ENV, durable, null)).to.deep.equal({ status: 'available' });
    });
  });
});
