/**
 * The RunReadyNotifier contract on Redis pub/sub (REDIS_IT_URL): the claimer
 * subscribes as `app/harness/run_ready.py` does, on the deployment's namespaced channel.
 */
import { Redis } from 'ioredis';
import {
  RedisRunReadyNotifier,
  runReadyChannel,
} from '../../../src/modules/agent-harness/adapters/redis/redis-run-ready';
import { describeRunReadyNotifierContract } from './support/run-ready-notifier-contract';
import { collected } from './support/collected';
import { RedisItRig, redisItRig, unreachableRedis } from './support/redis-it';

describe('RunReadyNotifier on Redis pub/sub (itest)', function () {
  this.timeout(30_000);
  let rig: RedisItRig;
  const subscribers: Redis[] = [];
  const broken: RedisItRig[] = [];

  before(() => {
    rig = redisItRig('run-ready');
  });

  afterEach(() => {
    for (const subscriber of subscribers.splice(0)) subscriber.disconnect();
  });

  after(async () => {
    for (const down of broken.splice(0)) await down.dispose();
    await rig.dispose();
  });

  describeRunReadyNotifierContract(
    'RedisRunReadyNotifier',
    async () => {
      const subscriber: Redis = rig.provider.createPubSubClient();
      subscribers.push(subscriber);
      const sink = collected<unknown>();
      subscriber.on('message', (_channel: string, message: string) => sink.push(JSON.parse(message)));
      await subscriber.subscribe(runReadyChannel(rig.provider.keyNamespace));
      return { notifier: new RedisRunReadyNotifier(rig.provider), received: sink.first };
    },
    {
      unreachable: async () => {
        const down = await unreachableRedis();
        broken.push(down);
        return new RedisRunReadyNotifier(down.provider);
      },
    },
  );
});
