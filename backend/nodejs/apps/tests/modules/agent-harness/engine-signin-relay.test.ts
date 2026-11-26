/**
 * SR-N1..SR-N6 (SIGNIN-2, SIGNIN-5): Node's side of the sign-in relay on Redis pub/sub,
 * against an in-process bus playing the worker as
 * `app/harness/engines/signin_relay.py` does (same channel names, same fields).
 */
import { expect } from 'chai';
import { EventEmitter } from 'events';
import {
  ATTEMPT_PREFIX,
  REVOCATION_MEMORY_S,
  REVOKED_PREFIX,
  REVOKE_CHANNEL,
  RedisEngineSignInRelay,
  signinChannel,
} from '../../../src/modules/agent-harness/adapters/redis/redis-engine-signin-relay';
import type { IRedisConnectionProvider } from '../../../src/libs/services/redis/connectionProvider.interface';

class Bus {
  readonly subscribers = new Map<string, Set<EventEmitter>>();
  readonly published: Array<[string, string]> = [];
  readonly remembered: Array<[string, string, string, number]> = [];
  worker: ((channel: string, message: Record<string, unknown>) => void) | null = null;

  publish(channel: string, message: string): Promise<number> {
    this.published.push([channel, message]);
    for (const client of this.subscribers.get(channel) ?? []) client.emit('message', channel, message);
    if (this.worker !== null) this.worker(channel, JSON.parse(message) as Record<string, unknown>);
    return Promise.resolve(1);
  }

  client(): EventEmitter & { subscribe(c: string): Promise<void>; unsubscribe(c: string): Promise<void> } {
    const emitter = new EventEmitter() as EventEmitter & {
      subscribe(c: string): Promise<void>;
      unsubscribe(c: string): Promise<void>;
    };
    emitter.subscribe = (channel) => {
      const set = this.subscribers.get(channel) ?? new Set();
      set.add(emitter);
      this.subscribers.set(channel, set);
      return Promise.resolve();
    };
    emitter.unsubscribe = (channel) => {
      this.subscribers.get(channel)?.delete(emitter);
      return Promise.resolve();
    };
    return emitter;
  }
}

const provider = (bus: Bus, keyNamespace = 'tenant-a'): IRedisConnectionProvider =>
  ({
    keyNamespace,
    getClient: () => ({
      publish: (c: string, m: string) => bus.publish(c, m),
      set: (k: string, v: string, ex: string, ttl: number) => {
        bus.remembered.push([k, v, ex, ttl]);
        return Promise.resolve('OK');
      },
    }),
    createPubSubClient: () => bus.client(),
  }) as unknown as IRedisConnectionProvider;

const CALLER = { orgId: 'org-1', userId: 'u1' };

describe('SIGNIN: the relay on Redis pub/sub (Node)', () => {
  it('SR-N1: a request goes on the attempt\'s channel with the caller and a reply channel; the answer comes back', async () => {
    const bus = new Bus();
    bus.worker = (channel, message) => {
      if (channel !== signinChannel(`${ATTEMPT_PREFIX}sin_1`, 'tenant-a')) return;
      expect(message).to.deep.include({ op: 'describe', actorId: 'u1', orgId: 'org-1' });
      expect(String(message['reply'])).to.match(/^tenant-a:harness:signin:reply:[0-9a-f-]{36}$/);
      void bus.publish(String(message['reply']), JSON.stringify({ ok: true, ownerId: 'u1', state: 'waiting' }));
    };
    const answer = await new RedisEngineSignInRelay(provider(bus)).ask('sin_1', CALLER, { op: 'describe' }, 1000);
    expect(answer).to.deep.equal({ ok: true, ownerId: 'u1', state: 'waiting' });
  });

  it('SR-N2: no worker answers: null after the wait, and the reply channel is let go', async () => {
    const bus = new Bus();
    const answer = await new RedisEngineSignInRelay(provider(bus)).ask('sin_1', CALLER, { op: 'cancel' }, 50);
    expect(answer).to.equal(null);
    expect([...bus.subscribers.values()].every((set) => set.size === 0)).to.equal(true);
  });

  it('SR-N3: a malformed answer is no answer', async () => {
    const bus = new Bus();
    bus.worker = (channel, message) => {
      if (channel.includes(ATTEMPT_PREFIX)) void bus.publish(String(message['reply']), '{"no":"ok"}');
    };
    expect(await new RedisEngineSignInRelay(provider(bus)).ask('sin_1', CALLER, { op: 'describe' }, 80)).to.equal(null);
  });

  it('SR-N4: a revocation is one message on the revoke channel', async () => {
    const bus = new Bus();
    await new RedisEngineSignInRelay(provider(bus, '')).revoke({ orgId: 'org-1', userId: 'u1', reason: 'user' });
    expect(bus.published).to.deep.equal([[REVOKE_CHANNEL, JSON.stringify({ orgId: 'org-1', userId: 'u1', reason: 'user' })]]);
  });

  it('SR-N6 (SIGNIN-5): a revocation is remembered per org, person or conversation before it is announced', async () => {
    const bus = new Bus();
    const relay = new RedisEngineSignInRelay(provider(bus));
    const before = Date.now();
    await relay.revoke({ orgId: 'org-1', userId: 'u1', reason: 'admin' });
    await relay.revoke({ orgId: 'org-1', sessionId: 'ses-1', reason: 'shared' });
    await relay.revoke({ orgId: 'org-1', reason: 'policy' });
    expect(bus.remembered.map(([key, , ex, ttl]) => [key, ex, ttl])).to.deep.equal([
      [`tenant-a:${REVOKED_PREFIX}org-1:u:u1`, 'EX', REVOCATION_MEMORY_S],
      [`tenant-a:${REVOKED_PREFIX}org-1:s:ses-1`, 'EX', REVOCATION_MEMORY_S],
      [`tenant-a:${REVOKED_PREFIX}org-1`, 'EX', REVOCATION_MEMORY_S],
    ]);
    expect(bus.remembered.every(([, at]) => Number(at) >= before)).to.equal(true);
    expect(bus.published.map(([channel]) => channel)).to.deep.equal(Array(3).fill(`tenant-a:${REVOKE_CHANNEL}`));
  });

  it('SR-N5: the channels are the worker\'s', () => {
    expect(signinChannel(`${ATTEMPT_PREFIX}sin_1`)).to.equal('harness:signin:attempt:sin_1');
    expect(signinChannel(REVOKE_CHANNEL, 'tenant-a')).to.equal('tenant-a:harness:signin:revoke');
  });
});
