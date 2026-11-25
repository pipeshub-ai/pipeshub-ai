import { expect } from 'chai';
import sinon from 'sinon';
import { MongoNotConnectedError } from 'mongodb';
import { HarnessCapabilityService } from '../../../src/modules/agent-harness/application/capability-service';
import { JournalDurabilityProbe } from '../../../src/libs/repositories/agent-harness/journal-durability-probe/mongo';
import { DispatchCapabilityProbe } from '../../../src/modules/agent-harness/adapters/configuration/dispatch-capability-probe';
import {
  capabilitiesDigest,
  DeploymentCapabilities,
  LEAST_CAPABILITIES,
  pinnableCapabilities,
} from '../../../src/modules/agent-harness/domain/capabilities';
import { HarnessUnavailableError } from '../../../src/modules/agent-harness/domain/errors';
import {
  currentJournalCapability,
  isAgentHarnessInfrastructureReady,
  JournalCapability,
  recordJournalCapability,
} from '../../../src/modules/agent-harness/config';
import * as journalStartup from '../../../src/libs/repositories/agent-harness/journal-startup/mongo';
import { probeJournalDurability } from '../../../src/libs/repositories/agent-harness/journal-startup/mongo';
import { decideOrgRollout } from '../../../src/modules/agent-harness/domain/rollout';
import { prepareAgentHarnessStore } from '../../../src/modules/agent-harness/composition';
import { MessageBrokerType } from '../../../src/libs/types/messaging.types';
import { CapabilityProbe } from '../../../src/modules/agent-harness/ports/capability-probe';
import { LiveBusCapabilityProbe } from '../../../src/modules/agent-harness/adapters/redis/live-bus-probe';
import type { IRedisConnectionProvider } from '../../../src/libs/services/redis/connectionProvider.interface';

const ready = {
  REPLICA_SET_AVAILABLE: 'true',
  AGENT_HARNESS_RELAY_ENABLED: 'true',
  AGENT_HARNESS_WORKER_ENABLED: 'true',
};

const probe = (
  name: string,
  result: () => Promise<Partial<DeploymentCapabilities>>,
  provides: CapabilityProbe['provides'] = [],
): CapabilityProbe => ({ name, provides, probe: result });

const standaloneDb = {
  admin: () => ({ command: sinon.stub().resolves({}) }),
  collection: sinon.stub(),
};

describe('agent harness capabilities', () => {
  afterEach(() => {
    sinon.restore();
    recordJournalCapability(null);
  });

  describe('HarnessCapabilityService', () => {
    it('has no capabilities before the first probe', () => {
      const service = new HarnessCapabilityService([]);
      expect(service.current()).to.equal(null);
      expect(() => pinnableCapabilities(service.current())).to.throw(
        HarnessUnavailableError,
      );
    });

    it('merges what each probe proves over the least capabilities', async () => {
      const service = new HarnessCapabilityService(
        [
          probe('journal', async () => ({ 'journal.durable': true })),
          probe('dispatch', async () => ({ 'workers.dispatched': true })),
        ],
        undefined,
        () => 42,
      );
      const snapshot = await service.refresh();
      expect(snapshot.capabilities).to.deep.equal({
        ...LEAST_CAPABILITIES,
        'journal.durable': true,
        'workers.dispatched': true,
      });
      expect(snapshot.digest).to.equal(
        capabilitiesDigest(snapshot.capabilities),
      );
      expect(snapshot.probedAt).to.equal(42);
      expect(service.current()).to.deep.equal(snapshot);
    });

    it('keeps the least value for a probe that fails (fail closed)', async () => {
      const warn = sinon.stub();
      const service = new HarnessCapabilityService(
        [
          probe('journal', async () => ({ 'journal.durable': true })),
          probe(
            'dispatch',
            async () => {
              throw new Error('broker unreadable');
            },
            ['workers.dispatched', 'livebus'],
          ),
        ],
        { info: sinon.stub(), warn },
      );
      const snapshot = await service.refresh();
      expect(snapshot.capabilities['workers.dispatched']).to.equal(false);
      expect(snapshot.capabilities.livebus).to.equal('inproc');
      expect(warn.calledOnce).to.equal(true);
      expect(warn.firstCall.args[1]).to.include({ probe: 'dispatch' });
      // Unknown, not disproved (ADR-CAP-01 D1).
      expect(snapshot.unsettled).to.deep.equal(['workers.dispatched', 'livebus']);
    });

    it('shares one probe round between concurrent refreshes', async () => {
      let calls = 0;
      const service = new HarnessCapabilityService([
        probe('journal', async () => {
          calls += 1;
          await new Promise((resolve) => setTimeout(resolve, 5));
          return { 'journal.durable': true };
        }),
      ]);
      const [first, second] = await Promise.all([
        service.refresh(),
        service.refresh(),
      ]);
      expect(calls).to.equal(1);
      expect(first).to.equal(second);
      await service.refresh();
      expect(calls).to.equal(2);
    });

    it('logs only when the capability set changes', async () => {
      const info = sinon.stub();
      let durable = true;
      const service = new HarnessCapabilityService(
        [probe('journal', async () => ({ 'journal.durable': durable }))],
        { info, warn: sinon.stub() },
      );
      await service.refresh();
      await service.refresh();
      expect(info.callCount).to.equal(1);
      durable = false;
      await service.refresh();
      expect(info.callCount).to.equal(2);
    });

    it('re-probes on its period and stops cleanly', async () => {
      const clock = sinon.useFakeTimers();
      let calls = 0;
      const service = new HarnessCapabilityService([
        probe('journal', async () => {
          calls += 1;
          return { 'journal.durable': true };
        }),
      ]);
      service.start(1_000);
      await clock.tickAsync(3_000);
      expect(calls).to.equal(3);
      service.stop();
      await clock.tickAsync(3_000);
      expect(calls).to.equal(3);
      clock.restore();
    });
  });

  describe('capabilitiesDigest', () => {
    it('is canonical: key order does not change it', () => {
      const reordered = Object.fromEntries(
        Object.entries(LEAST_CAPABILITIES).reverse(),
      ) as DeploymentCapabilities;
      expect(capabilitiesDigest(reordered)).to.equal(
        capabilitiesDigest({ ...LEAST_CAPABILITIES }),
      );
      expect(capabilitiesDigest({ ...LEAST_CAPABILITIES })).to.match(
        /^sha256:[0-9a-f]{64}$/,
      );
    });

    it('changes with any capability', () => {
      expect(
        capabilitiesDigest({ ...LEAST_CAPABILITIES, livebus: 'redis' }),
      ).not.to.equal(capabilitiesDigest({ ...LEAST_CAPABILITIES }));
    });
  });

  describe('JournalDurabilityProbe (reuses the S1 probe)', () => {
    it('reports a durable journal and records it for admission readiness', async () => {
      const recorded: JournalCapability[] = [];
      const journal = new JournalDurabilityProbe(
        async () => ({ durable: true, reason: 'majority' }),
        (capability) => recorded.push(capability),
      );
      expect(await journal.probe()).to.deep.equal({ 'journal.durable': true });
      expect(recorded).to.deep.equal([{ durable: true, reason: 'majority' }]);
    });

    it('records a probe that throws as probe_failed', async () => {
      const journal = new JournalDurabilityProbe(async () => {
        throw new Error('not connected');
      });
      expect(await journal.probe()).to.deep.equal({
        'journal.durable': false,
      });
      expect(currentJournalCapability()).to.deep.equal({
        durable: false,
        reason: 'probe_failed',
      });
    });
  });

  describe('O-3: standalone Mongo keeps the harness off', () => {
    it('probes journal.durable=false, refuses admission and refuses to pin', async () => {
      const service = new HarnessCapabilityService([
        new JournalDurabilityProbe(() =>
          probeJournalDurability(standaloneDb as never),
        ),
        probe('dispatch', async () => ({ 'workers.dispatched': true })),
      ]);
      const snapshot = await service.refresh();
      expect(snapshot.capabilities['journal.durable']).to.equal(false);
      expect(currentJournalCapability()).to.deep.equal({
        durable: false,
        reason: 'standalone',
      });
      expect(isAgentHarnessInfrastructureReady(ready)).to.equal(false);
      expect(
        decideOrgRollout({
          infrastructureReady: isAgentHarnessInfrastructureReady(ready),
          killSwitch: false,
          orgEnabled: true,
          modelFamilies: null,
        }),
      ).to.include({ useHarness: false, reason: 'infrastructure_unavailable' });
      expect(() => pinnableCapabilities(snapshot)).to.throw(
        HarnessUnavailableError,
        /majority/,
      );
    });
  });

  describe('DispatchCapabilityProbe', () => {
    it('derives dispatched workers and a Redis LiveBus from a Redis Streams broker', async () => {
      const dispatch = new DispatchCapabilityProbe(
        ready,
        () => MessageBrokerType.REDIS,
      );
      expect(await dispatch.probe()).to.deep.equal({
        'workers.dispatched': true,
        livebus: 'redis',
      });
    });

    it('keeps the LiveBus in-process on Kafka and reports no workers when the worker role is off', async () => {
      const dispatch = new DispatchCapabilityProbe(
        { ...ready, AGENT_HARNESS_WORKER_ENABLED: 'false' },
        () => MessageBrokerType.KAFKA,
      );
      expect(await dispatch.probe()).to.deep.equal({
        'workers.dispatched': false,
        livebus: 'inproc',
      });
    });
  });

  describe('prepareAgentHarnessStore', () => {
    it('publishes nothing until an unreachable store is prepared', async () => {
      const refresh = sinon.stub().resolves();
      const log = {
        info: sinon.stub(),
        warn: sinon.stub(),
        error: sinon.stub(),
      };
      const migrate = sinon.stub(journalStartup, 'migrateHarnessStore');
      migrate.onFirstCall().rejects(new MongoNotConnectedError('MongoDB is not connected'));
      migrate.resolves({ droppedIndexes: [], createdIndexes: [], orphanedDispatchRows: 0 });
      const publishedWhileWaiting: boolean[] = [];
      await prepareAgentHarnessStore(log as never, ready, { refresh } as never, {
        wait: async () => void publishedWhileWaiting.push(refresh.called),
      });
      expect(publishedWhileWaiting).to.deep.equal([false]);
      expect(refresh.calledOnce).to.equal(true);
    });
  });

  describe('livebus probe', () => {
    const redis = (ping: () => Promise<boolean>) =>
      ({ ping }) as unknown as IRedisConnectionProvider;

    it("streams tokens over Redis only when the deployment's Redis answers", async () => {
      const probe = new LiveBusCapabilityProbe(redis(() => Promise.resolve(true)));
      expect(probe.name).to.equal('livebus');
      expect(await probe.probe()).to.deep.equal({ livebus: 'redis' });
    });

    it('falls back to the journal when Redis does not answer or the ping throws', async () => {
      expect(
        await new LiveBusCapabilityProbe(redis(() => Promise.resolve(false))).probe(),
      ).to.deep.equal({ livebus: 'inproc' });
      expect(
        await new LiveBusCapabilityProbe(
          redis(() => Promise.reject(new Error('ECONNREFUSED'))),
        ).probe(),
      ).to.deep.equal({ livebus: 'inproc' });
    });
  });
});
