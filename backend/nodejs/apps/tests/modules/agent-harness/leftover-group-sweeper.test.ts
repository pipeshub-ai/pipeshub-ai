/**
 * OPS1-4-8: the sweep of earlier builds' consumer groups runs on the lease
 * holder only, asks the broker for exactly the declared names, and a broker
 * failure costs one pass, not the process.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import type { IdleGroupSweep, IdleGroupSweepResult } from '../../../src/libs/types/messaging.types';
import {
  HISTORICAL_GROUP_FORMATS,
  LEFTOVER_GROUP_IDLE_MS,
  LEFTOVER_GROUPS,
  LEFTOVER_SWEEP_INTERVAL_MS,
  LEFTOVER_SWEEP_LEASE_MS,
  LEFTOVER_SWEEP_LEASE_NAME,
  LeftoverGroupSweeper,
} from '../../../src/modules/agent-harness/application/leftover-group-sweeper';
import { LeaderLease } from '../../../src/modules/agent-harness/ports/leader-lease';
import { createMockLogger } from '../../helpers/mock-logger';

class Lease implements LeaderLease {
  holder: string | null = null;
  async tryAcquire(_name: string, holder: string): Promise<boolean> {
    this.holder ??= holder;
    return this.holder === holder;
  }
  async release(_name: string, holder: string): Promise<void> {
    if (this.holder === holder) this.holder = null;
  }
}

class Broker {
  readonly asked: IdleGroupSweep[] = [];
  constructor(private readonly dead: Record<string, string[]> = {}) {}
  async removeIdleGroups(sweep: IdleGroupSweep): Promise<IdleGroupSweepResult> {
    this.asked.push(sweep);
    const all = this.dead[sweep.topic] ?? [];
    const removed = all.filter((name) => sweep.formats.some((format) => format.name.test(name)));
    this.dead[sweep.topic] = all.filter((name) => !removed.includes(name));
    return { removed, left: all.filter((name) => !removed.includes(name)) };
  }
}

describe('OPS1-4-8 LeftoverGroupSweeper', () => {
  afterEach(() => sinon.restore());

  it('the lease holder removes the dead groups of the declared names; another replica asks the broker nothing', async () => {
    const lease = new Lease();
    const broker = new Broker({
      'control-plane-events': ['control-plane-events-node-7f9-1', 'control_plane_events_group-41-0a1b2c3d', 'someone-elses'],
      'ai-config-events': ['aiconfig_consumer_group-7-deadbeef', 'aiconfig_consumer_group'],
    });
    const logger = createMockLogger();
    const leader = new LeftoverGroupSweeper({ admin: broker, lease, holderId: 'a', logger });
    const follower = new LeftoverGroupSweeper({ admin: broker, lease, holderId: 'b', logger });

    expect(await leader.tick()).to.deep.equal([
      'control-plane-events-node-7f9-1',
      'control_plane_events_group-41-0a1b2c3d',
      'aiconfig_consumer_group-7-deadbeef',
    ]);
    const asked = broker.asked.length;
    expect(await follower.tick()).to.deep.equal([]);

    expect(broker.asked.length, 'the follower asked nothing').to.equal(asked);
    expect(broker.asked.map(({ topic, idleMs }) => [topic, idleMs])).to.deep.equal(
      LEFTOVER_GROUPS.map(({ topic }) => [topic, LEFTOVER_GROUP_IDLE_MS]),
    );
    expect(logger.info.getCalls().filter((call) => call.args[0] === 'broker.leftover_groups_removed').map((call) => (call.args[1] as { topic: string; groups: string[] }).groups.length)).to.deep.equal([2, 1]);
  });

  it('the declared names are the earlier builds\' own, and nothing a shared or operator group is called', () => {
    const matches = (name: string): boolean =>
      LEFTOVER_GROUPS.some(({ formats }) => formats.some((format) => format.name.test(name)));
    for (const ours of ['control-plane-events-pipeshub-7d9f-abcde-1', 'control_plane_events_group-12345-0a1b2c3d', 'aiconfig_consumer_group-1-ffffffff']) {
      expect(matches(ours), ours).to.equal(true);
    }
    for (const notOurs of [
      'records_consumer_group', 'entity_consumer_group', 'sync_consumer_group', 'aiconfig_consumer_group',
      'notification-consumer-group', 'mail-consumer-group', 'agent-harness-triggers', 'control-plane-events',
      'control_plane_events_group-12345-xyz', 'aiconfig_consumer_group-1-ffffffff-extra',
      'control-plane-events--1', 'control-plane-events-host-0', 'control-plane-events-host-12345678',
    ]) {
      expect(matches(notOurs), notOurs).to.equal(false);
    }
  });

  it('OPS1-4-14: each historical format names the consumer that build read with, so a look-alike group is not proven ours', () => {
    expect(Object.keys(HISTORICAL_GROUP_FORMATS).sort()).to.deep.equal(['node', 'pythonAiConfig', 'pythonControlPlane']);
    const node = 'control-plane-events-pipeshub-7d9f-abcde-41';
    expect(HISTORICAL_GROUP_FORMATS.node.consumer(node)).to.equal(node);
    expect(HISTORICAL_GROUP_FORMATS.pythonControlPlane.consumer()).to.equal('control_plane_events_client');
    expect(HISTORICAL_GROUP_FORMATS.pythonAiConfig.consumer()).to.equal('aiconfig_consumer_client');
  });

  it('a group left alone is named in the log once per process', async () => {
    const left = { removeIdleGroups: async () => ({ removed: [], left: ['control-plane-events-x-2'] }) };
    const logger = createMockLogger();
    const sweeper = new LeftoverGroupSweeper({ admin: left, lease: new Lease(), holderId: 'a', logger, groups: [{ topic: 't', formats: [] }] });
    await sweeper.tick();
    await sweeper.tick();
    expect(logger.info.getCalls().map((call) => [call.args[0], (call.args[1] as { groups: string[] }).groups])).to.deep.equal([
      ['broker.leftover_groups_left', ['control-plane-events-x-2']],
    ]);
  });

  it('a broker that fails costs that pass only; stopping hands the lease to the next replica', async () => {
    const lease = new Lease();
    const failing = { removeIdleGroups: sinon.stub().rejects(new Error('broker away')) };
    const logger = createMockLogger();
    const first = new LeftoverGroupSweeper({ admin: failing, lease, holderId: 'a', logger });

    expect(await first.tick()).to.deep.equal([]);
    expect(logger.warn.firstCall.args[0]).to.equal('broker.leftover_groups_sweep_failed');
    await first.stop();

    const broker = new Broker({ 'ai-config-events': ['aiconfig_consumer_group-7-deadbeef'] });
    const second = new LeftoverGroupSweeper({ admin: broker, lease, holderId: 'b' });
    expect(await second.tick()).to.deep.equal(['aiconfig_consumer_group-7-deadbeef']);
  });

  it('sweeps on its interval once started, holds the lease for three of them, and not after stop', async () => {
    const g = { name: /^g$/, consumer: (group: string): string => group };
    const clock = sinon.useFakeTimers();
    const lease = new Lease();
    const acquire = sinon.spy(lease, 'tryAcquire');
    const broker = new Broker();
    const sweeper = new LeftoverGroupSweeper({ admin: broker, lease, holderId: 'a', groups: [{ topic: 't', formats: [g] }], idleMs: 5 });
    sweeper.start();
    sweeper.start();

    await clock.tickAsync(LEFTOVER_SWEEP_INTERVAL_MS);
    expect(broker.asked).to.deep.equal([{ topic: 't', formats: [g], idleMs: 5 }]);
    expect(acquire.firstCall.args).to.deep.equal([LEFTOVER_SWEEP_LEASE_NAME, 'a', LEFTOVER_SWEEP_LEASE_MS]);
    expect(LEFTOVER_SWEEP_LEASE_MS).to.equal(3 * LEFTOVER_SWEEP_INTERVAL_MS);

    await sweeper.stop();
    await clock.tickAsync(3 * LEFTOVER_SWEEP_INTERVAL_MS);
    expect(broker.asked).to.have.length(1);
    expect(lease.holder).to.equal(null);
  });

  it('a lease that cannot be released on stop is logged, not thrown', async () => {
    const lease: LeaderLease = { tryAcquire: async () => true, release: sinon.stub().rejects(new Error('store away')) };
    const logger = createMockLogger();
    await new LeftoverGroupSweeper({ admin: new Broker(), lease, holderId: 'a', logger }).stop();
    expect(logger.warn.firstCall.args[0]).to.equal('broker.leftover_groups_lease_release_failed');
  });
});
