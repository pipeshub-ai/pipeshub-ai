import { expect } from 'chai';
import sinon from 'sinon';
import { RedisRunWakeup, wakeChannel } from '../../../src/modules/agent-harness/adapters/redis/redis-run-wakeup';
import { LiveBusCapabilityProbe } from '../../../src/modules/agent-harness/adapters/redis/live-bus-probe';
import { liveStreamKey, RedisLiveBus } from '../../../src/modules/agent-harness/adapters/redis/redis-live-bus';

const providerWith = (client: Record<string, unknown>, extra: Record<string, unknown> = {}) =>
  ({ getClient: () => client, keyNamespace: 'tenant-a', ...extra }) as never;

describe('agent-harness Redis adapters, without Redis', () => {
  it('RedisRunWakeup publishes an empty wake on the run\'s channel in the deployment\'s namespace', async () => {
    const publish = sinon.stub().resolves(1);
    await new RedisRunWakeup(providerWith({ publish })).wake('run-1');
    expect(publish.calledOnceWithExactly(wakeChannel('run-1', 'tenant-a'), '')).to.equal(true);
    expect(wakeChannel('run-1', 'tenant-a')).to.equal('tenant-a:harness:interaction-notify:run-1');
  });

  it('RedisRunWakeup lets a publish failure reach the caller (it logs; the resolution stands)', async () => {
    const wakeup = new RedisRunWakeup(providerWith({ publish: sinon.stub().rejects(new Error('down')) }));
    let failed = false;
    try {
      await wakeup.wake('run-1');
    } catch {
      failed = true;
    }
    expect(failed).to.equal(true);
  });

  for (const [label, ping, livebus] of [
    ['a reachable Redis streams tokens live', () => Promise.resolve(true), 'redis'],
    ['an unreachable one falls back to the journal', () => Promise.resolve(false), 'inproc'],
    ['a ping that throws is unreachable, not a failed probe', () => Promise.reject(new Error('ECONNREFUSED')), 'inproc'],
  ] as const) {
    it(`LiveBusCapabilityProbe: ${label}`, async () => {
      const probe = new LiveBusCapabilityProbe({ ping } as never);
      expect(probe.name).to.equal('livebus');
      expect(await probe.probe()).to.deep.equal({ livebus });
    });
  }

  it('RedisLiveBus.erase deletes each run\'s stream with its own DEL and counts what went', async () => {
    const del = sinon.stub();
    del.withArgs(liveStreamKey('run-1', 'tenant-a')).resolves(1);
    del.withArgs(liveStreamKey('run-2', 'tenant-a')).resolves(0);
    const bus = new RedisLiveBus(providerWith({ del }));
    expect(await bus.erase(['run-1', 'run-2'])).to.equal(1);
    expect(del.callCount).to.equal(2);
    expect(await bus.erase([])).to.equal(0);
  });
});
