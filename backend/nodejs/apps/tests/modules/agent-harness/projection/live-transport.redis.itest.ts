/**
 * The LiveBus reader on a real Redis Streams server (REDIS_IT_URL, e.g.
 * redis://localhost:6379); the journal tail is ../journal-tail.mongo.itest.ts.
 * Entries are written in the publisher's wire shape
 * (`contracts/harness/vectors/livebus-frames.json`) with the Python
 * publisher's commands: XADD with MAXLEN ~ and EXPIRE.
 */
import fs from 'fs';
import path from 'path';
import { expect } from 'chai';
import { getRedisProvider } from '../../../../src/libs/services/redis/connectionProviderFactory';
import { redisConnectionConfigFromHostPort } from '../../../../src/libs/services/redis/connectionConfig';
import { liveNotifyChannel, liveStreamKey, RedisLiveBus } from '../../../../src/modules/agent-harness/adapters/redis/redis-live-bus';
import { LiveBusCapabilityProbe } from '../../../../src/modules/agent-harness/adapters/redis/live-bus-probe';
import { LiveDelta } from '../../../../src/modules/agent-harness/projection/frames';

const VECTOR = path.join(__dirname, '..', '..', '..', '..', '..', '..', '..', 'contracts', 'harness', 'vectors', 'livebus-frames.json');
const vectorFrames = (): Array<{ frame: LiveDelta; fields: Record<string, string> }> =>
  (JSON.parse(fs.readFileSync(VECTOR, 'utf8')) as { frames: Array<{ frame: LiveDelta; fields: Record<string, string> }> }).frames;

const redisUrl = (): URL => {
  const raw = process.env.REDIS_IT_URL;
  if (!raw) throw new Error('REDIS_IT_URL must be set for the LiveBus Redis test');
  return new URL(raw);
};

const tick = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

describe('LiveBus on Redis Streams (itest)', function () {
  this.timeout(20_000);
  const url = redisUrl();
  const provider = getRedisProvider(redisConnectionConfigFromHostPort({ host: url.hostname, port: Number(url.port || 6379) }));

  const publish = async (runId: string, fields: Record<string, string>) => {
    const key = liveStreamKey(runId, provider.keyNamespace);
    await provider.getClient().xadd(key, 'MAXLEN', '~', '8000', '*', ...Object.entries(fields).flat());
    await provider.getClient().expire(key, 3600);
  };

  it('reads what the publisher wrote, then follows new frames in order', async () => {
    const [first, second, third] = vectorFrames();
    const runId = first!.frame.runId;
    await provider.getClient().del(liveStreamKey(runId, provider.keyNamespace));
    await publish(runId, first!.fields);
    const bus = new RedisLiveBus(provider, 10);
    const retained = await bus.read(runId);
    expect(retained.deltas).to.deep.equal([first!.frame]);
    const got: LiveDelta[] = [];
    const sub = bus.follow(runId, retained.cursor, (d) => got.push(...d), () => undefined);
    await publish(runId, second!.fields);
    await publish(runId, { ...third!.fields });
    await tick(200);
    sub.close();
    expect(got.map((d) => d.n)).to.deep.equal([second!.frame.n, third!.frame.n]);
    expect(await provider.getClient().ttl(liveStreamKey(runId, provider.keyNamespace))).to.be.greaterThan(0);
  });

  it('reads a run as soon as its notify channel says so, without waiting for the poll', async () => {
    const [first] = vectorFrames();
    const runId = `${first!.frame.runId}-notify`;
    await provider.getClient().del(liveStreamKey(runId, provider.keyNamespace));
    const bus = new RedisLiveBus(provider, 60_000);
    const got: LiveDelta[] = [];
    const sub = bus.follow(runId, '0-0', (d) => got.push(...d), () => undefined);
    await tick(200);
    await publish(runId, first!.fields);
    await provider.getClient().publish(liveNotifyChannel(runId, provider.keyNamespace), '');
    for (let i = 0; i < 40 && got.length === 0; i += 1) await tick(25);
    sub.close();
    expect(got.map((d) => d.n)).to.deep.equal([first!.frame.n]);
  });

  it('probes livebus=redis on a reachable Redis', async () => {
    expect(await new LiveBusCapabilityProbe(provider).probe()).to.deep.equal({ livebus: 'redis' });
  });
});
