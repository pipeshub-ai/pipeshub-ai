/**
 * PRJ-G3: the LiveBus contract on Redis Streams (REDIS_IT_URL), published the
 * way `app/harness/livebus.py` publishes: XADD with MAXLEN ~ and EXPIRE, then
 * an empty message on the run's notify channel.
 */
import fs from 'node:fs';
import path from 'node:path';
import { expect } from 'chai';
import {
  liveNotifyChannel,
  liveStreamKey,
  RedisLiveBus,
} from '../../../src/modules/agent-harness/adapters/redis/redis-live-bus';
import { LiveDelta } from '../../../src/modules/agent-harness/projection/frames';
import { describeLiveBusContract } from './support/live-bus-contract';
import { RedisItRig, redisItRig, unreachableRedis } from './support/redis-it';

const VECTOR = path.join(__dirname, '..', '..', '..', '..', '..', '..', 'contracts', 'harness', 'vectors', 'livebus-frames.json');

/** The publisher's wire fields (`contracts/harness/vectors/livebus-frames.json`). */
const liveFields = (delta: LiveDelta): Record<string, string> => ({
  v: '1',
  f: String(delta.fence),
  a: String(delta.modelAttempt),
  n: String(delta.n),
  k: delta.kind,
  d: JSON.stringify(delta.data),
});

describe('LiveBus on Redis Streams (itest)', function () {
  this.timeout(30_000);
  let rig: RedisItRig;
  const broken: RedisItRig[] = [];

  before(() => {
    rig = redisItRig('livebus');
  });

  after(async () => {
    for (const down of broken.splice(0)) await down.dispose();
    await rig.dispose();
  });

  it("LB-0: the test publisher writes the publisher's pinned wire fields", () => {
    const vector = JSON.parse(fs.readFileSync(VECTOR, 'utf8')) as {
      frames: Array<{ frame: LiveDelta; fields: Record<string, string> }>;
    };
    for (const { frame, fields } of vector.frames) expect(liveFields(frame)).to.deep.equal(fields);
  });

  describeLiveBusContract('RedisLiveBus', async () => {
    const { provider } = rig;
    const client = provider.getClient();
    const serverMs = async (): Promise<number> => {
      const [seconds, micros] = await client.time();
      return Number(seconds) * 1000 + Math.floor(Number(micros) / 1000);
    };
    return {
      bus: new RedisLiveBus(provider),
      publish: async (delta) => {
        const key = liveStreamKey(delta.runId, provider.keyNamespace);
        await client.xadd(key, 'MAXLEN', '~', '8000', '*', ...Object.entries(liveFields(delta)).flat());
        await client.expire(key, 3600);
        await client.publish(liveNotifyChannel(delta.runId, provider.keyNamespace), '');
      },
      expire: async (runId) => {
        // A real lapse comes a TTL after the last XADD, so the next stream's ids follow the old cursor.
        // Deleting at once lets an XADD in the same millisecond reuse the cursor's id, and the follower skips it.
        const key = liveStreamKey(runId, provider.keyNamespace);
        const last = (await client.xrevrange(key, '+', '-', 'COUNT', 1))[0]?.[0];
        await client.del(key);
        const lastMs = Number(last?.split('-')[0] ?? 0);
        while ((await serverMs()) <= lastMs);
      },
      unreachable: async () => {
        const down = await unreachableRedis();
        broken.push(down);
        return new RedisLiveBus(down.provider);
      },
    };
  });
});
