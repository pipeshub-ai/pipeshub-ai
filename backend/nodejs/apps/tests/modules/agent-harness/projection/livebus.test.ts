import { EventEmitter } from 'events';
import fs from 'fs';
import path from 'path';
import { expect } from 'chai';
import type { IRedisConnectionProvider } from '../../../../src/libs/services/redis/connectionProvider.interface';
import {
  decodeLiveFields,
  liveNotifyChannel,
  liveStreamKey,
  RedisLiveBus,
} from '../../../../src/modules/agent-harness/adapters/redis/redis-live-bus';
import { LiveDelta } from '../../../../src/modules/agent-harness/projection/frames';

const VECTOR = path.join(__dirname, '..', '..', '..', '..', '..', '..', '..', 'contracts', 'harness', 'vectors', 'livebus-frames.json');

interface Vector {
  keys: Array<{ runId: string; namespace: string; key: string; notifyChannel: string }>;
  frames: Array<{ frame: LiveDelta; frameId: string; fields: Record<string, string> }>;
  rejected: Array<{ fields: Record<string, string> }>;
}

const vector = (): Vector => JSON.parse(fs.readFileSync(VECTOR, 'utf8')) as Vector;
const flat = (fields: Record<string, string>): string[] => Object.entries(fields).flat();

/** A Redis stream in memory, speaking the two commands the reader uses. */
class FakeStreams {
  readonly streams = new Map<string, Array<[string, string[]]>>();
  failXread: Error | null = null;
  add(key: string, fields: Record<string, string>): void {
    const list = this.streams.get(key) ?? [];
    list.push([`${list.length + 1}-0`, flat(fields)]);
    this.streams.set(key, list);
  }
  client() {
    return {
      xrange: (key: string) => Promise.resolve(this.streams.get(key) ?? []),
      xread: (...args: unknown[]) => {
        if (this.failXread) return Promise.reject(this.failXread);
        const key = args[3] as string;
        const after = Number(String(args[4]).split('-')[0]);
        const entries = (this.streams.get(key) ?? []).filter(([id]) => Number(id.split('-')[0]) > after);
        return Promise.resolve(entries.length ? [[key, entries]] : null);
      },
    };
  }
  provider(namespace = '', subscriber: FakeSubscriber | null = null): IRedisConnectionProvider {
    const client = this.client();
    return {
      getClient: () => client,
      keyNamespace: namespace,
      createPubSubClient: () => {
        if (subscriber === null) throw new Error('no pub/sub here');
        return subscriber;
      },
    } as unknown as IRedisConnectionProvider;
  }
}

/** The `SUBSCRIBE` connection: records channels, delivers what the test publishes. */
class FakeSubscriber extends EventEmitter {
  readonly channels = new Set<string>();
  subscribe(channel: string): Promise<number> {
    this.channels.add(channel);
    return Promise.resolve(this.channels.size);
  }
  unsubscribe(channel: string): Promise<number> {
    this.channels.delete(channel);
    return Promise.resolve(this.channels.size);
  }
  publish(channel: string): void {
    if (this.channels.has(channel)) this.emit('message', channel, '');
  }
}

const tick = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

describe('agent-harness LiveBus (Node reader of the Python publisher)', () => {
  it('reads the wire shape pinned by the shared vector', () => {
    const doc = vector();
    for (const key of doc.keys) {
      expect(liveStreamKey(key.runId, key.namespace)).to.equal(key.key);
      expect(liveNotifyChannel(key.runId, key.namespace)).to.equal(key.notifyChannel);
    }
    for (const case_ of doc.frames) {
      expect(decodeLiveFields(case_.frame.runId, flat(case_.fields))).to.deep.equal(case_.frame);
      expect(decodeLiveFields(case_.frame.runId, case_.fields)).to.deep.equal(case_.frame);
    }
    for (const rejected of doc.rejected) expect(decodeLiveFields('run_1', rejected.fields)).to.equal(null);
    expect(decodeLiveFields('run_1', { v: '1', f: '1', a: '1', n: '1', k: 'text_delta', d: 'not json' })).to.equal(null);
    expect(decodeLiveFields('run_1', { v: '1', f: '-1', a: '1', n: '1', k: 'text_delta', d: '{}' })).to.equal(null);
  });

  it('reads the retained frames and follows new ones from where the read ended', async () => {
    const redis = new FakeStreams();
    const [first, second] = vector().frames;
    redis.add('ns:harness:live:7a1c0e52-5b1d-4c3e-9f0a-2d4e6b8c0f11', first!.fields);
    const bus = new RedisLiveBus(redis.provider('ns'), 5);
    const retained = await bus.read(first!.frame.runId);
    expect(retained.deltas).to.deep.equal([first!.frame]);
    const got: LiveDelta[] = [];
    const sub = bus.follow(first!.frame.runId, retained.cursor, (d) => got.push(...d), () => undefined);
    redis.add('ns:harness:live:7a1c0e52-5b1d-4c3e-9f0a-2d4e6b8c0f11', second!.fields);
    await tick(30);
    sub.close();
    expect(got).to.deep.equal([second!.frame]);
  });

  it('reads a run as soon as its publisher notifies, not at the next poll', async () => {
    const redis = new FakeStreams();
    const subscriber = new FakeSubscriber();
    const [first] = vector().frames;
    const runId = first!.frame.runId;
    const bus = new RedisLiveBus(redis.provider('ns', subscriber), 60_000);
    const got: LiveDelta[] = [];
    const sub = bus.follow(runId, '0-0', (d) => got.push(...d), () => undefined);
    await tick(1);
    expect([...subscriber.channels]).to.deep.equal([`ns:harness:live-notify:${runId}`]);
    redis.add(`ns:harness:live:${runId}`, first!.fields);
    subscriber.publish(`ns:harness:live-notify:${runId}`);
    await tick(5);
    expect(got).to.deep.equal([first!.frame]);
    sub.close();
    expect(subscriber.channels.size, 'no follower left: not listening').to.equal(0);
  });

  it('keeps polling a run when no notify connection can be opened', async () => {
    const redis = new FakeStreams();
    const [first] = vector().frames;
    const bus = new RedisLiveBus(redis.provider('ns'), 5);
    const got: LiveDelta[] = [];
    const sub = bus.follow(first!.frame.runId, '0-0', (d) => got.push(...d), () => undefined);
    redis.add(`ns:harness:live:${first!.frame.runId}`, first!.fields);
    await tick(30);
    sub.close();
    expect(got).to.deep.equal([first!.frame]);
  });

  it('reports a Redis failure to the follower once and stops polling it', async () => {
    const redis = new FakeStreams();
    redis.failXread = new Error('ECONNREFUSED');
    const bus = new RedisLiveBus(redis.provider(), 5);
    const errors: unknown[] = [];
    bus.follow('run-x', '0-0', () => undefined, (e) => errors.push(e));
    await tick(40);
    expect(errors).to.have.length(1);
  });
});
