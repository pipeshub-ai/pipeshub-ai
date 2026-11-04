import { expect } from 'chai';
import sinon from 'sinon';
import { realIoredis } from '../../helpers/mock-ioredis-global';
import {
  ACQUIRE_OR_ENQUEUE_LUA,
  MemoryThreadQueueStore,
  QueuedSlackMessage,
  RENEW_LUA,
  RedisThreadQueueStore,
  TAKE_PENDING_OR_RELEASE_LUA,
  createThreadQueueStore,
  threadQueueKey,
} from '../../../src/integrations/slack-bot/src/utils/thread-queue';
import type { IRedisConnectionProvider } from '../../../src/libs/services/redis/connectionProvider.interface';

const msg = (ts: string): QueuedSlackMessage => ({
  kind: 'mention',
  ts,
  threadTs: '1.0',
  channel: 'C1',
  user: 'U1',
  text: `text ${ts}`,
  files: [],
  receivedAt: 0,
});
const SHAS = { acquire: 'sha-a', take: 'sha-t', renew: 'sha-r' };
const KEY = threadQueueKey({ botId: 'B', channel: 'C1', threadTs: '1.0', user: 'U1' });

describe('slack-bot/utils/thread-queue redis store', () => {
  afterEach(() => sinon.restore());

  it('sends each operation as one EVALSHA on both hash-tagged keys', async () => {
    const calls: unknown[][] = [];
    const replies: unknown[] = [1, 0, -1, [JSON.stringify(msg('1')), JSON.stringify(msg('2'))], 1, 0];
    const client = {
      evalsha: async (...args: unknown[]) => {
        calls.push(args);
        return replies.shift();
      },
    };
    const store = new RedisThreadQueueStore(client as any, SHAS);
    expect(await store.acquireOrEnqueue(KEY, 'o1', msg('1'), 10)).to.equal('acquired');
    expect(await store.acquireOrEnqueue(KEY, 'o2', msg('2'), 10)).to.equal('queued');
    expect(await store.acquireOrEnqueue(KEY, 'o2', msg('3'), 10)).to.equal('full');
    const batch = await store.takePendingOrRelease(KEY, 'o1');
    expect(batch.map((m) => m.ts)).to.deep.equal(['1', '2']);
    expect(await store.renew(KEY, 'o1')).to.equal(true);
    expect(await store.renew(KEY, 'o2')).to.equal(false);

    const shas = [SHAS.acquire, SHAS.acquire, SHAS.acquire, SHAS.take, SHAS.renew, SHAS.renew];
    calls.forEach((c, i) => {
      expect(c.slice(0, 4)).to.deep.equal([shas[i], 2, `${KEY}:lock`, `${KEY}:pending`]);
    });
    expect(calls[0]!.slice(4)).to.deep.equal(['o1', JSON.stringify(msg('1')), 10, 120000, 1800000]);
    expect(calls[3]!.slice(4)).to.deep.equal(['o1', 120000]);
  });

  it('reloads a flushed script once and retries', async () => {
    let first = true;
    const client = {
      evalsha: sinon.stub().callsFake(async (sha: string) => {
        if (first) {
          first = false;
          throw new Error('NOSCRIPT No matching script');
        }
        return sha === 'sha-new' ? 1 : -1;
      }),
    };
    const loadScript = sinon.stub().resolves('sha-new');
    const store = new RedisThreadQueueStore(client as any, SHAS, { loadScript });
    expect(await store.acquireOrEnqueue(KEY, 'o1', msg('1'), 10)).to.equal('acquired');
    expect(loadScript.calledOnce).to.equal(true);

    client.evalsha.rejects(new Error('ECONNRESET'));
    let err: unknown;
    try {
      await store.renew(KEY, 'o1');
    } catch (e) {
      err = e;
    }
    expect((err as Error).message).to.equal('ECONNRESET');
  });

  it('skips a corrupt queued item instead of losing its batch', async () => {
    const warn = sinon.stub(console, 'warn');
    const replies: unknown[] = [['{bad', JSON.stringify(msg('1'))], ['null', '{"x":1}'], []];
    const evalsha = sinon.stub().callsFake(async () => replies.shift());
    const store = new RedisThreadQueueStore({ evalsha } as any, SHAS);
    expect(await store.takePendingOrRelease(KEY, 'o1')).to.deep.equal([msg('1')]);
    expect(warn.callCount).to.equal(1);
    // All corrupt: take again so the lock is released rather than held for a TTL.
    expect(await store.takePendingOrRelease(KEY, 'o1')).to.deep.equal([]);
    expect(evalsha.callCount).to.equal(3);
    expect(warn.callCount).to.equal(3);
  });

  it('falls back to memory when Redis is not configured or does not answer', async () => {
    const warn = sinon.stub(console, 'warn');
    const provider = (ping: () => Promise<boolean>, load: () => Promise<string>) =>
      ({ ping, loadScript: load, keyNamespace: 'ns:', getClient: () => ({}) }) as unknown as IRedisConnectionProvider;

    for (const p of [
      null,
      provider(async () => false, async () => 'x'),
      provider(async () => { throw new Error('down'); }, async () => 'x'),
      provider(async () => true, async () => { throw new Error('denied'); }),
    ]) {
      const r = await createThreadQueueStore(p);
      expect(r.shared).to.equal(false);
      expect(r.namespace).to.equal('');
      expect(r.store).to.be.instanceOf(MemoryThreadQueueStore);
    }
    expect(warn.callCount).to.equal(4);
    expect(warn.alwaysCalledWithExactly('Slack thread queue: Redis unavailable, queueing is per process')).to.equal(true);

    const ok = await createThreadQueueStore(provider(async () => true, async () => 'sha'));
    expect(ok.shared).to.equal(true);
    expect(ok.namespace).to.equal('ns:');
    expect(ok.store).to.be.instanceOf(RedisThreadQueueStore);
  });

  const url = process.env.SLACK_QUEUE_TEST_REDIS_URL;
  if (!url) {
    it.skip('real Redis: scripts keep the contract (set SLACK_QUEUE_TEST_REDIS_URL to run)');
    return;
  }

  describe('real Redis: scripts keep the contract', () => {
    let client: any;
    let shas: typeof SHAS;
    const key = threadQueueKey({ botId: 'T', channel: 'C', threadTs: `${process.pid}`, user: 'U' });
    const store = (opts: { lockTtlMs?: number } = {}) => new RedisThreadQueueStore(client, shas, opts);

    before(async () => {
      // The global ioredis mock would answer in-process; this block needs the real driver.
      client = new realIoredis.Redis(url);
      shas = {
        acquire: await client.script('LOAD', ACQUIRE_OR_ENQUEUE_LUA),
        take: await client.script('LOAD', TAKE_PENDING_OR_RELEASE_LUA),
        renew: await client.script('LOAD', RENEW_LUA),
      };
    });
    afterEach(async () => {
      await client.del(`${key}:lock`, `${key}:pending`);
    });
    after(async () => {
      await client.quit();
    });

    it('acquires, queues, refuses at 11, takes one batch, then releases', async () => {
      const s = store();
      expect(await s.acquireOrEnqueue(key, 'a', msg('0'), 10)).to.equal('acquired');
      expect((await s.takePendingOrRelease(key, 'a')).map((m) => m.ts)).to.deep.equal(['0']);
      for (let i = 1; i <= 10; i++) {
        expect(await s.acquireOrEnqueue(key, 'b', msg(`${i}`), 10)).to.equal('queued');
      }
      expect(await s.acquireOrEnqueue(key, 'b', msg('11'), 10)).to.equal('full');
      const batch = await s.takePendingOrRelease(key, 'a');
      expect(batch.map((m) => m.ts)).to.deep.equal(['1', '2', '3', '4', '5', '6', '7', '8', '9', '10']);
      expect(batch[0]).to.deep.equal(msg('1'));
      expect(await s.takePendingOrRelease(key, 'a')).to.deep.equal([]);
      expect(await client.exists(`${key}:lock`, `${key}:pending`)).to.equal(0);
      expect(await s.acquireOrEnqueue(key, 'b', msg('12'), 10)).to.equal('acquired');
    });

    it('leaves a non-owner unable to renew or take', async () => {
      const s = store();
      await s.acquireOrEnqueue(key, 'a', msg('1'), 10);
      await s.acquireOrEnqueue(key, 'b', msg('2'), 10);
      expect(await s.renew(key, 'b')).to.equal(false);
      expect(await s.renew(key, 'a')).to.equal(true);
      expect(await s.takePendingOrRelease(key, 'b')).to.deep.equal([]);
      expect(await client.get(`${key}:lock`)).to.equal('a');
      expect(await client.llen(`${key}:pending`)).to.equal(2);
    });

    it('hands stranded items to the next owner after the lock expires', async () => {
      const s = store({ lockTtlMs: 200 });
      await s.acquireOrEnqueue(key, 'dead', msg('1'), 10);
      await s.acquireOrEnqueue(key, 'b', msg('2'), 10);
      await new Promise((r) => setTimeout(r, 300));
      expect(await s.renew(key, 'dead')).to.equal(false);
      expect(await s.acquireOrEnqueue(key, 'fresh', msg('3'), 10)).to.equal('acquired');
      expect((await s.takePendingOrRelease(key, 'fresh')).map((m) => m.ts)).to.deep.equal(['1', '2', '3']);
    });

    it('keeps working after the script cache is flushed', async () => {
      const s = store();
      await client.script('FLUSH');
      expect(await s.acquireOrEnqueue(key, 'a', msg('1'), 10)).to.equal('acquired');
      expect(await s.renew(key, 'a')).to.equal(true);
      expect((await s.takePendingOrRelease(key, 'a')).map((m) => m.ts)).to.deep.equal(['1']);
    });

    it('grants the lock to exactly one of 20 concurrent callers', async () => {
      const s = store();
      const outcomes = await Promise.all(
        Array.from({ length: 20 }, (_, i) => s.acquireOrEnqueue(key, `o${i}`, msg(`${i}`), 100)),
      );
      expect(outcomes.filter((o) => o === 'acquired')).to.have.length(1);
      expect(outcomes.filter((o) => o === 'queued')).to.have.length(19);
    });
  });
});
