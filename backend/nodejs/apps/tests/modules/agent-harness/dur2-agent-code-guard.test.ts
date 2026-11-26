/**
 * DUR2-W1, W2, W4, W8: whether a live worker advertises a feature, as admission
 * and the sweeper read it (the deploy-order guard for `agent_code`). The rolling
 * deploy cases on both stores are in the ExecutionStore contract
 * ("the agent_code deploy-order guard (DUR2)").
 */
import { expect } from 'chai';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryWorkerFeatureSightings } from '../../../src/modules/agent-harness/adapters/in-memory-worker-feature-sightings';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import {
  AVAILABILITY_CACHE_MS,
  SIGHTING_WRITE_INTERVAL_MS,
  WORKER_FEATURE_WINDOW_MS,
  WorkerFeatureAvailability,
} from '../../../src/modules/agent-harness/application/worker-feature-availability';
import {
  capabilitySnapshot,
  LEAST_CAPABILITIES,
} from '../../../src/modules/agent-harness/domain/capabilities';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import {
  GUARDED_WORKER_FEATURES,
  guardedWorkerFeatures,
  WorkerFeature,
} from '../../../src/modules/agent-harness/domain/worker-compat';
import {
  FeatureSighting,
  StrandedOutcome,
  WorkerFeatureMetrics,
  WorkerFeatureSightings,
} from '../../../src/modules/agent-harness/ports/worker-features';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { HARNESS_ENABLED } from './support/rollout';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };

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

class Metrics implements WorkerFeatureMetrics {
  gauge: Array<[WorkerFeature, boolean]> = [];
  withheldFeatures: WorkerFeature[] = [];
  strandedCount: Array<[WorkerFeature, StrandedOutcome]> = [];
  live(feature: WorkerFeature, isLive: boolean): void {
    this.gauge.push([feature, isLive]);
  }
  withheld(feature: WorkerFeature): void {
    this.withheldFeatures.push(feature);
  }
  stranded(feature: WorkerFeature, outcome: StrandedOutcome): void {
    this.strandedCount.push([feature, outcome]);
  }
}

class CountingSightings implements WorkerFeatureSightings {
  writes = 0;
  reads = 0;
  failWrites = false;
  failReads = false;
  constructor(private readonly inner: WorkerFeatureSightings) {}
  async saw(features: readonly WorkerFeature[]): Promise<void> {
    this.writes += 1;
    if (this.failWrites) throw new Error('mongo down');
    await this.inner.saw(features);
  }
  async sighting(feature: WorkerFeature): Promise<FeatureSighting> {
    this.reads += 1;
    if (this.failReads) throw new Error('mongo down');
    return this.inner.sighting(feature);
  }
}

const world = () => {
  let now = 1_000;
  const clock = { now: () => now };
  const sightings = new CountingSightings(new InMemoryWorkerFeatureSightings(clock));
  const logger = new Lines();
  const metrics = new Metrics();
  const availability = new WorkerFeatureAvailability({ sightings, logger, metrics, clock });
  return {
    sightings,
    logger,
    metrics,
    availability,
    clock,
    advance: (ms: number) => {
      now += ms;
    },
  };
};

describe('DUR2: which features live workers advertise', () => {
  it('agent_code, resolved_config, run_surface, release_pin, deliverable_pin and output_locale are guarded: requiring one is an upgrade, never a correctness need', () => {
    expect(GUARDED_WORKER_FEATURES).to.deep.equal(['agent_code', 'resolved_config', 'run_surface', 'release_pin', 'deliverable_pin', 'output_locale', 'keyword_search_budget']);
  });

  it('CPD10-44: with the policy store as the record resolved_config is a correctness need, no longer guarded', () => {
    expect(guardedWorkerFeatures(false)).to.deep.equal(['agent_code', 'resolved_config', 'run_surface', 'release_pin', 'deliverable_pin', 'output_locale', 'keyword_search_budget']);
    expect(guardedWorkerFeatures(true)).to.deep.equal(['agent_code', 'run_surface', 'release_pin', 'deliverable_pin', 'output_locale', 'keyword_search_budget']);
  });

  it('DUR2-W2: nobody seen: not live; the gap is logged once and the gauge reads 0', async () => {
    const w = world();
    expect(await w.availability.live('agent_code')).to.equal(false);
    w.advance(AVAILABILITY_CACHE_MS);
    expect(await w.availability.live('agent_code')).to.equal(false);
    expect(w.logger.named('worker.feature_gap')).to.deep.equal([
      { event: 'worker.feature_gap', feature: 'agent_code', reason: 'no_live_worker', windowMs: WORKER_FEATURE_WINDOW_MS },
    ]);
    expect(w.metrics.gauge).to.deep.equal([
      ['agent_code', false],
      ['agent_code', false],
    ]);
  });

  it('DUR2-W1: a sighting makes it live; DUR2-W4: past the window it is a gap again, and back', async () => {
    const w = world();
    await w.availability.observe(['agent_code', 'chat']);
    expect(await w.availability.live('agent_code')).to.equal(true);
    w.advance(WORKER_FEATURE_WINDOW_MS + 1);
    expect(await w.availability.live('agent_code')).to.equal(false);
    expect(w.logger.named('worker.feature_gap')).to.have.length(1);
    await w.availability.observe(['agent_code']);
    w.advance(AVAILABILITY_CACHE_MS);
    expect(await w.availability.live('agent_code')).to.equal(true);
    expect(w.logger.named('worker.feature_live')).to.deep.equal([{ event: 'worker.feature_live', feature: 'agent_code' }]);
  });

  it('DUR2-W8: polls inside the write interval cost one store write; reads are cached', async () => {
    const w = world();
    for (let i = 0; i < 50; i += 1) await w.availability.observe(['agent_code']);
    expect(w.sightings.writes).to.equal(1);
    w.advance(SIGHTING_WRITE_INTERVAL_MS);
    await w.availability.observe(['agent_code']);
    expect(w.sightings.writes).to.equal(2);
    for (let i = 0; i < 20; i += 1) await w.availability.live('agent_code');
    expect(w.sightings.reads).to.equal(1);
  });

  it('a store that cannot be written or read never throws; unreadable reads as not live', async () => {
    const w = world();
    w.sightings.failWrites = true;
    await w.availability.observe(['agent_code']);
    await w.availability.observe(['agent_code']);
    expect(w.logger.named('worker.sighting_failed')).to.have.length(1);
    w.sightings.failReads = true;
    expect(await w.availability.live('agent_code')).to.equal(false);
    expect(w.logger.named('worker.sighting_unreadable')).to.have.length(1);
  });

  describe('admission (DUR2-W1, W2)', () => {
    const PROVED = capabilitySnapshot(
      { ...LEAST_CAPABILITIES, 'journal.durable': true, 'sandbox.isolation': 'container' },
      1,
    );
    const admit = async (w: ReturnType<typeof world>): Promise<HarnessRun> => {
      const outcome = await new RunAdmissionService({
        store: new InMemoryExecutionStore({ now: () => 1_000 }),
        rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
        clock: { now: () => 1_000 },
        agentAccess: grantingAccess((key) => agentDefinition({ agentKey: key })),
        capabilities: { current: () => PROVED },
        workerFeatures: w.availability,
        logger: w.logger,
      }).submit({
        orgId: 'org-1',
        actorId: 'user-1',
        agentKey: 'agent-1',
        authorization: 'Bearer t',
        idempotencyKey: `k-${Math.random()}`,
        deadlineMs: 60_000,
        input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'quick' } },
      });
      if (outcome.route !== 'durable') expect.fail('expected a durable admission');
      return outcome.admission.run;
    };

    it('a live agent_code worker: required', async () => {
      const w = world();
      await w.availability.observe(['agent_code']);
      expect((await admit(w)).requires?.features).to.include('agent_code');
      expect(w.metrics.withheldFeatures).to.not.include('agent_code');
    });

    it('a live worker without it: admitted as before P5.11, counted and logged with its reason', async () => {
      const w = world();
      await w.availability.observe(['chat']);
      expect((await admit(w)).requires?.features).to.not.include('agent_code');
      expect(w.metrics.withheldFeatures.filter((f) => f === 'agent_code')).to.have.length(1);
      expect(w.logger.named('run.agent_code_withheld')[0]).to.include({ reason: 'no_live_worker', orgId: 'org-1' });
    });
  });
});
