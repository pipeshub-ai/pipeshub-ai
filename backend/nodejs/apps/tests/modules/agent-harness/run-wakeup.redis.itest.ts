/**
 * The RunWakeup contract on Redis pub/sub (REDIS_IT_URL): holders subscribe
 * the way `app/harness/interaction_wakeup.py` does, one listener connection
 * per worker process, on the run's channel in the deployment's namespace.
 */
import { expect } from 'chai';
import { Redis } from 'ioredis';
import { RedisRunWakeup, wakeChannel } from '../../../src/modules/agent-harness/adapters/redis/redis-run-wakeup';
import { IRedisConnectionProvider } from '../../../src/libs/services/redis/connectionProvider.interface';
import { describeRunWakeupContract, wakeCounter } from './support/run-wakeup-contract';
import { RedisItRig, redisItRig, unreachableRedis } from './support/redis-it';

/** One worker process's listener: a subscriber connection shared by the runs it holds. */
function workerListener(provider: IRedisConnectionProvider) {
  const subscriber: Redis = provider.createPubSubClient();
  const holders = new Map<string, Set<ReturnType<typeof wakeCounter>>>();
  subscriber.on('message', (channel: string) => {
    for (const holder of holders.get(channel) ?? []) holder.wake();
  });
  return {
    hold: async (runId: string) => {
      const channel = wakeChannel(runId, provider.keyNamespace);
      const set = holders.get(channel) ?? new Set();
      holders.set(channel, set);
      const holder = wakeCounter(async () => {
        set.delete(holder);
        if (set.size === 0) {
          holders.delete(channel);
          await subscriber.unsubscribe(channel);
        }
      });
      set.add(holder);
      await subscriber.subscribe(channel);
      return holder;
    },
    close: () => subscriber.disconnect(),
  };
}

describe('RunWakeup on Redis pub/sub (itest)', function () {
  this.timeout(30_000);
  let rig: RedisItRig;
  const listeners: Array<{ close(): void }> = [];
  const broken: RedisItRig[] = [];

  before(() => {
    rig = redisItRig('wakeup');
  });

  afterEach(() => {
    for (const listener of listeners.splice(0)) listener.close();
  });

  after(async () => {
    for (const down of broken.splice(0)) await down.dispose();
    await rig.dispose();
  });

  describeRunWakeupContract(
    'RedisRunWakeup',
    async () => {
      const listener = workerListener(rig.provider);
      listeners.push(listener);
      return { wakeup: new RedisRunWakeup(rig.provider), hold: listener.hold };
    },
    {
      unreachable: async () => {
        const down = await unreachableRedis();
        broken.push(down);
        return new RedisRunWakeup(down.provider);
      },
    },
  );

  it("RW-7: a wake in one deployment's namespace does not reach a holder in another's", async () => {
    const other = redisItRig('wakeup-other');
    const theirs = workerListener(other.provider);
    listeners.push(theirs);
    try {
      const shared = await theirs.hold('run-same-id');
      const witness = await theirs.hold('run-witness');
      await new RedisRunWakeup(rig.provider).wake('run-same-id');
      await new RedisRunWakeup(other.provider).wake('run-witness');
      await witness.woken(1);
      expect(shared.count).to.equal(0);
    } finally {
      await other.dispose();
    }
  });
});
