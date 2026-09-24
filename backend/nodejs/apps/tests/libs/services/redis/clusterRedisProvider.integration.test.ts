/**
 * `ClusterRedisProvider` against a REAL Redis Cluster.
 *
 * The rest of this directory runs against `createIoredisCapture`, which can
 * prove the provider *asks* for the right things but not that the answers are
 * correct. Three behaviours here have no meaningful mock:
 *
 *  - `scanKeys` fan-out. ioredis routes `Cluster.scan()` to one arbitrary
 *    node, so a raw SCAN silently returns whatever fraction of the keyspace
 *    that shard holds. Only a real multi-slot keyspace shows the difference.
 *  - `keySlot`. ioredis does not export its CRC16 across versions, so the
 *    provider ships its own; agreeing with `CLUSTER KEYSLOT` from the server
 *    is the only check that matters, and it is what `StreamReadPlanner`'s
 *    correctness rests on.
 *  - `loadScript` on every master. A script loaded on one node and EVALSHA'd
 *    against a key on another is exactly the NOSCRIPT case R6 is about.
 *
 * Skipped unless `REDIS_CLUSTER_ENDPOINTS` is set, so `npm test` stays
 * hermetic; the `redis-cluster-integration-tests` CI job sets it. Locally:
 *
 *   docker compose -f deployment/docker-compose/docker-compose.integration.redis-cluster.yml up -d --wait
 *   cd backend/nodejs/apps && TS_NODE_PROJECT=tsconfig.test.json \
 *     REDIS_MODE=cluster \
 *     REDIS_CLUSTER_ENDPOINTS=127.0.0.1:17000,127.0.0.1:17001,127.0.0.1:17002 \
 *     npx mocha --no-parallel 'tests/libs/services/redis/**\/*.test.ts'
 */
import { expect } from 'chai';

import { realIoredis } from '../../../helpers/mock-ioredis-global';
import { RedisConnectionConfig } from '../../../../src/libs/services/redis/connectionConfig';

const endpoints = process.env.REDIS_CLUSTER_ENDPOINTS ?? '';
const describeCluster = endpoints ? describe : describe.skip;

const providerPath = require.resolve(
  '../../../../src/libs/services/redis/clusterRedisProvider',
);
const ioredisPath = require.resolve('ioredis');

function clusterConfig(): RedisConnectionConfig {
  const [first = '127.0.0.1:17000'] = endpoints.split(',');
  const [host = '127.0.0.1', port = '17000'] = first.split(':');
  return {
    host,
    port: parseInt(port, 10),
    tls: false,
    tlsRejectUnauthorized: true,
    db: 0,
    keyNamespace: '',
    connectTimeoutMs: 10000,
    clusterEndpoints: endpoints.split(',').map((e) => e.trim()),
    scaleReads: 'master',
  };
}

describeCluster('ClusterRedisProvider (live 3-master cluster)', function () {
  // Cluster connect + slot-map load is slower than the default 10s budget on
  // a cold container.
  this.timeout(30000);

  let ClusterRedisProvider: typeof import('../../../../src/libs/services/redis/clusterRedisProvider').ClusterRedisProvider;
  let provider: InstanceType<typeof ClusterRedisProvider>;
  let savedIoredis: NodeModule | undefined;
  const prefix = `it:${process.pid}:${Date.now()}`;

  before(async () => {
    // Swap the global fake back out for the real driver, for this spec only.
    savedIoredis = require.cache[ioredisPath];
    if (savedIoredis && realIoredis) {
      require.cache[ioredisPath] = {
        ...savedIoredis,
        exports: realIoredis,
      } as NodeModule;
    }
    delete require.cache[providerPath];
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    ClusterRedisProvider = require(providerPath).ClusterRedisProvider;
    provider = new ClusterRedisProvider(clusterConfig());
    await provider.ping();
  });

  after(async () => {
    const client = provider.getClient();
    for await (const key of provider.scanKeys(`${prefix}*`)) {
      await client.del(key);
    }
    await provider.close();
    delete require.cache[providerPath];
    if (savedIoredis) {
      require.cache[ioredisPath] = savedIoredis;
    }
  });

  it('scanKeys returns keys from every shard, not just one node', async () => {
    const client = provider.getClient();
    // 24 distinct keys spread over the slot space; with 3 masters the odds of
    // them all landing on one shard are negligible, and the assertion below
    // checks that they genuinely did not.
    const keys = Array.from({ length: 24 }, (_, i) => `${prefix}:k${i}`);
    await Promise.all(keys.map((key) => client.set(key, '1')));

    const slots = new Set(keys.map((key) => provider.keySlot(key)));
    expect(
      slots.size,
      'test is meaningless unless the keys really do span slots',
    ).to.be.greaterThan(1);

    const found: string[] = [];
    for await (const key of provider.scanKeys(`${prefix}:k*`)) {
      found.push(key);
    }

    expect(found.sort()).to.deep.equal(keys.sort());
  });

  it('keySlot agrees with the server\'s own CLUSTER KEYSLOT', async () => {
    const client = provider.getClient();
    const samples = [
      'records',
      'record-events.0',
      'record-events.7',
      '{crawling}:meta',
      'pipeshub:kv:/services/redis',
      'a',
      '',
    ];

    for (const key of samples) {
      const serverSlot = await client.cluster('KEYSLOT', key);
      expect(provider.keySlot(key), `slot mismatch for '${key}'`).to.equal(
        Number(serverSlot),
      );
    }
  });

  it('loadScript makes EVALSHA work against keys on any shard', async () => {
    const sha = await provider.loadScript(
      "return redis.call('SET', KEYS[1], ARGV[1])",
    );
    const client = provider.getClient();

    const keys = Array.from({ length: 12 }, (_, i) => `${prefix}:lua${i}`);
    expect(new Set(keys.map((k) => provider.keySlot(k))).size).to.be.greaterThan(1);

    for (const key of keys) {
      await client.evalsha(sha, 1, key, 'ok');
      expect(await client.get(key)).to.equal('ok');
    }
  });

  it('multi-slot XREADGROUP fails, one call per slot group succeeds', async () => {
    const client = provider.getClient();
    const group = 'it-group';
    // Lane streams: exactly the shape `StreamReadPlanner` exists for.
    const streams = [0, 1, 2, 3].map((n) => `${prefix}:record-events.${n}`);
    for (const stream of streams) {
      await client.xadd(stream, '*', 'value', '1');
      await client.xgroup('CREATE', stream, group, '0', 'MKSTREAM');
    }

    const slots = new Set(streams.map((s) => provider.keySlot(s)));
    expect(slots.size, 'lane streams must span slots for this to mean anything')
      .to.be.greaterThan(1);

    // The pre-refactor call shape: every stream in one XREADGROUP.
    let crossSlotError: Error | null = null;
    try {
      await client.xreadgroup(
        'GROUP',
        group,
        'c1',
        'COUNT',
        '10',
        'STREAMS',
        ...streams,
        ...streams.map(() => '>'),
      );
    } catch (error) {
      crossSlotError = error as Error;
    }
    expect(crossSlotError, 'expected CROSSSLOT from a multi-slot XREADGROUP')
      .to.not.equal(null);
    expect(crossSlotError!.message).to.include('CROSSSLOT');

    // Grouped by slot, every stream is readable.
    const { StreamReadPlanner } = require('../../../../src/libs/services/redis/streamReadPlanner');
    const planner = new StreamReadPlanner(provider);
    const read: string[] = [];
    for (const slotGroup of planner.group(streams)) {
      const result = (await client.xreadgroup(
        'GROUP',
        group,
        'c1',
        'COUNT',
        '10',
        'STREAMS',
        ...slotGroup,
        ...slotGroup.map(() => '>'),
      )) as Array<[string, unknown[]]> | null;
      for (const [streamName] of result ?? []) {
        read.push(streamName);
      }
    }

    expect(read.sort()).to.deep.equal(streams.sort());
  });
});
