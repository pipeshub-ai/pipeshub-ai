import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import http from 'http';
import { EventEmitter } from 'events';
import type { AddressInfo } from 'net';
import jwt from 'jsonwebtoken';
import { NotificationService } from '../../../../src/modules/notification/service/notification.service';
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service';
import type { Server as SocketServer } from 'socket.io';
import {
  createFakeRedisProvider,
  FakeRedisClient,
  FakeRedisProvider,
} from '../../../helpers/fake-redis-provider';

// Runs the real Socket.IO server that NotificationService builds, with the real
// AuthTokenService and real signed tokens. The client below speaks Socket.IO's
// HTTP long-polling transport over fetch, so no client library is needed.

const JWT_SECRET = 'notification-socket-jwt-secret';

// The private state these tests look at, so assertions stay type-checked.
interface NotificationServiceInternals {
  io: SocketServer | null;
}
const RECORD_SEPARATOR = '\x1e';

/**
 * The Redis pub/sub that the replicas of one test share: what one replica's
 * adapter publishes reaches every subscribed client, as on a real Redis. The
 * `s`-commands are Redis 7's sharded pub/sub, used on Redis Cluster.
 */
class FakePubSub extends EventEmitter {
  readonly patterns = new Set<string>();
  readonly channels = new Set<string>();
  duplicatedWith: unknown;

  constructor(private readonly bus: Set<FakePubSub>) {
    super();
    bus.add(this);
  }

  psubscribe(pattern: string) {
    this.patterns.add(pattern);
    return Promise.resolve();
  }
  punsubscribe(pattern: string) {
    this.patterns.delete(pattern);
    return Promise.resolve();
  }
  subscribe(channels: string[]) {
    channels.forEach((channel) => this.channels.add(channel));
    return Promise.resolve();
  }
  unsubscribe(channels: string[]) {
    channels.forEach((channel) => this.channels.delete(channel));
    return Promise.resolve();
  }
  ssubscribe(channel: string) {
    return this.subscribe([channel]);
  }
  sunsubscribe(channels: string | string[]) {
    return this.unsubscribe([channels].flat());
  }

  publish(channel: string, message: string | Buffer) {
    for (const client of this.bus) {
      for (const pattern of client.patterns) {
        if (channel.startsWith(pattern.replace(/\*$/, ''))) {
          setImmediate(() =>
            client.emit('pmessageBuffer', Buffer.from(pattern), Buffer.from(channel), Buffer.from(message)),
          );
        }
      }
      if (client.channels.has(channel)) {
        setImmediate(() => client.emit('messageBuffer', Buffer.from(channel), Buffer.from(message)));
      }
    }
    return Promise.resolve(1);
  }
  spublish(channel: string, message: string | Buffer) {
    for (const client of this.bus) {
      if (client.channels.has(channel)) {
        setImmediate(() => client.emit('smessageBuffer', Buffer.from(channel), Buffer.from(message)));
      }
    }
    return Promise.resolve(1);
  }

  duplicate(_startupNodes: unknown, options: unknown) {
    const copy = new FakePubSub(this.bus);
    copy.duplicatedWith = options;
    return copy;
  }
  quit() {
    this.bus.delete(this);
    return Promise.resolve('OK');
  }
}

function redisOn(bus: Set<FakePubSub>, isCluster = false): FakeRedisProvider {
  return createFakeRedisProvider(
    () => new FakePubSub(bus) as unknown as FakeRedisClient,
    '',
    { isCluster },
  );
}

class PollingClient {
  private sid = '';
  private buffer: string[] = [];

  constructor(private base: string) {}

  private url() {
    return `${this.base}/socket.io/?EIO=4&transport=polling${this.sid ? `&sid=${this.sid}` : ''}`;
  }

  private async poll(): Promise<void> {
    const response = await fetch(this.url());
    const body = await response.text();
    if (!response.ok) {
      this.buffer.push(`closed:${response.status}`);
      return;
    }
    this.buffer.push(...body.split(RECORD_SEPARATOR));
  }

  private async post(packet: string) {
    const response = await fetch(this.url(), { method: 'POST', body: packet });
    expect(response.status, await response.text()).to.equal(200);
  }

  async next(): Promise<string> {
    while (this.buffer.length === 0) {
      await this.poll();
    }
    return this.buffer.shift()!;
  }

  // Engine.IO handshake, then the Socket.IO CONNECT packet carrying the token.
  async connect(auth: Record<string, unknown>): Promise<string> {
    const open = await this.next();
    expect(open.startsWith('0')).to.be.true;
    this.sid = JSON.parse(open.slice(1)).sid;
    await this.post(`40${JSON.stringify(auth)}`);
    return this.next();
  }

  async nextEvent(): Promise<[string, unknown]> {
    for (;;) {
      const packet = await this.next();
      if (packet === '2') {
        await this.post('3');
        continue;
      }
      if (packet.startsWith('42')) {
        return JSON.parse(packet.slice(2));
      }
      throw new Error(`expected an event, got ${packet}`);
    }
  }

  async close() {
    if (this.sid) {
      await fetch(this.url(), { method: 'POST', body: '1' }).catch(() => undefined);
    }
  }
}

function sessionToken(claims: Record<string, unknown>, secret = JWT_SECRET) {
  return `Bearer ${jwt.sign(claims, secret, { expiresIn: '1h' })}`;
}

async function waitFor(condition: () => boolean) {
  for (let i = 0; i < 100 && !condition(); i++) {
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  expect(condition()).to.be.true;
}

interface Replica {
  server: http.Server;
  service: NotificationService;
  redis: FakeRedisProvider;
  base: string;
}

async function startReplica(redis: FakeRedisProvider): Promise<Replica> {
  const server = http.createServer();
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const service = new NotificationService(
    new AuthTokenService(JWT_SECRET, 'scoped-secret'),
    redis,
  );
  service.initialize(server);
  return {
    server,
    service,
    redis,
    base: `http://127.0.0.1:${(server.address() as AddressInfo).port}`,
  };
}

describe('NotificationService over a real socket', () => {
  let bus: Set<FakePubSub>;
  let replicas: Replica[];
  let service: NotificationService;
  let base: string;
  let clients: PollingClient[];
  const internals = (): NotificationServiceInternals =>
    service as unknown as NotificationServiceInternals;
  const socketsInRoom = (room: string): number =>
    internals().io?.sockets.adapter.rooms.get(room)?.size ?? 0;

  const alice = { userId: 'user-alice', orgId: 'org-1', role: 'member' };
  const bob = { userId: 'user-bob', orgId: 'org-1', role: 'member' };
  const carol = { userId: 'user-carol', orgId: 'org-2', role: 'admin' };

  function client(on = base) {
    const c = new PollingClient(on);
    clients.push(c);
    return c;
  }

  async function connectAs(claims: Record<string, unknown>, on = base) {
    const c = client(on);
    const reply = await c.connect({ token: sessionToken(claims) });
    expect(reply.startsWith('40{'), reply).to.be.true;
    return c;
  }

  async function addReplica(isCluster = false) {
    const replica = await startReplica(redisOn(bus, isCluster));
    replicas.push(replica);
    return replica;
  }

  beforeEach(async () => {
    clients = [];
    bus = new Set();
    replicas = [];
    ({ service, base } = await addReplica());
  });

  afterEach(async () => {
    await Promise.all(clients.map((c) => c.close()));
    for (const replica of replicas) {
      replica.service.shutdown();
      await new Promise((resolve) => replica.server.close(resolve));
    }
    sinon.restore();
  });

  describe('who may connect', () => {
    it('turns away a connection with no token', async () => {
      const reply = await client().connect({});

      expect(reply.startsWith('44')).to.be.true;
      expect(JSON.parse(reply.slice(2)).message).to.equal('Authentication token missing');
    });

    it('turns away a token that is not a Bearer token', async () => {
      const reply = await client().connect({ token: jwt.sign(alice, JWT_SECRET) });

      expect(reply.startsWith('44')).to.be.true;
    });

    it('turns away a token signed with another key', async () => {
      const reply = await client().connect({ token: sessionToken(alice, 'someone-elses-key') });

      expect(reply.startsWith('44')).to.be.true;
      expect(socketsInRoom(alice.userId)).to.equal(0);
    });

    it('signs out a session whose token carries no role, then drops the connection', async () => {
      const c = client();
      const reply = await c.connect({ token: sessionToken({ userId: 'user-legacy', orgId: 'org-1' }) });

      expect(reply.startsWith('40')).to.be.true;
      expect(await c.nextEvent()).to.deep.equal(['force_logout', { reason: 'missing_role' }]);
      expect(await c.next()).to.equal('41');
      expect(socketsInRoom('user-legacy')).to.equal(0);
    });
  });

  describe('delivery', () => {
    it("delivers to the addressed user only, never to a colleague's socket", async () => {
      const aliceSocket = await connectAs(alice);
      const bobSocket = await connectAs(bob);

      expect(service.sendToUser(bob.userId, 'notification', { for: 'bob' })).to.be.true;
      expect(service.sendToUser(alice.userId, 'notification', { for: 'alice' })).to.be.true;

      expect(await aliceSocket.nextEvent()).to.deep.equal(['notification', { for: 'alice' }]);
      expect(await bobSocket.nextEvent()).to.deep.equal(['notification', { for: 'bob' }]);
    });

    it('keeps org-wide messages inside the org', async () => {
      const aliceSocket = await connectAs(alice);
      const carolSocket = await connectAs(carol);

      service.sendToOrg('org-2', 'org_notice', { org: 2 });
      service.sendToOrg('org-1', 'org_notice', { org: 1 });
      service.sendToUser(carol.userId, 'notification', { last: true });

      expect(await aliceSocket.nextEvent()).to.deep.equal(['org_notice', { org: 1 }]);
      expect(await carolSocket.nextEvent()).to.deep.equal(['org_notice', { org: 2 }]);
      expect(await carolSocket.nextEvent()).to.deep.equal(['notification', { last: true }]);
    });

    it('holds nothing for a user who is offline: the stored notification is fetched on connect', async () => {
      expect(service.sendToUser(alice.userId, 'notification', { offline: true })).to.be.true;

      const aliceSocket = await connectAs(alice);
      service.sendToUser(alice.userId, 'notification', { online: true });

      expect(await aliceSocket.nextEvent()).to.deep.equal(['notification', { online: true }]);
    });

    it('keeps a user marked online while another of their tabs is still open', async () => {
      const firstTab = await connectAs(alice);
      const secondTab = await connectAs(alice);
      expect(socketsInRoom(alice.userId)).to.equal(2);
      await firstTab.close();
      await waitFor(() => socketsInRoom(alice.userId) === 1);

      expect(service.sendToUser(alice.userId, 'notification', { tab: 2 })).to.be.true;
      expect(await secondTab.nextEvent()).to.deep.equal(['notification', { tab: 2 }]);
    });

  });

  describe('forced sign-out', () => {
    it('signs out a connected user straight away', async () => {
      const aliceSocket = await connectAs(alice);

      expect(service.emitForceLogout(alice.userId, 'role_changed')).to.be.true;
      expect(await aliceSocket.nextEvent()).to.deep.equal(['force_logout', { reason: 'role_changed' }]);
    });

    it('does not queue a sign-out for someone offline, so their next sign-in is not undone', async () => {
      service.emitForceLogout(alice.userId);

      const aliceSocket = await connectAs(alice);
      service.sendToUser(alice.userId, 'notification', { first: true });

      expect(await aliceSocket.nextEvent()).to.deep.equal(['notification', { first: true }]);
    });
  });

  describe('across replicas', () => {
    it("delivers to a user's socket on another replica, and to nobody else there", async () => {
      const other = await addReplica();
      const bobSocket = await connectAs(bob, other.base);
      const aliceSocket = await connectAs(alice, other.base);

      expect(service.sendToUser(alice.userId, 'notification', { from: 'replica A' })).to.be.true;
      service.sendToUser(bob.userId, 'notification', { for: 'bob' });

      expect(await aliceSocket.nextEvent()).to.deep.equal(['notification', { from: 'replica A' }]);
      expect(await bobSocket.nextEvent()).to.deep.equal(['notification', { for: 'bob' }]);
    });

    it('reaches every tab of a user, whichever replica holds each', async () => {
      const other = await addReplica();
      const here = await connectAs(alice);
      const there = await connectAs(alice, other.base);

      other.service.sendToUser(alice.userId, 'notification', { n: 1 });

      expect(await here.nextEvent()).to.deep.equal(['notification', { n: 1 }]);
      expect(await there.nextEvent()).to.deep.equal(['notification', { n: 1 }]);
    });

    it('signs out a session held by another replica', async () => {
      const other = await addReplica();
      const aliceSocket = await connectAs(alice, other.base);

      expect(service.emitForceLogout(alice.userId, 'role_changed')).to.be.true;

      expect(await aliceSocket.nextEvent()).to.deep.equal(['force_logout', { reason: 'role_changed' }]);
    });

    it('uses sharded pub/sub on Redis Cluster', async () => {
      const [first] = replicas;
      first!.service.shutdown();
      ({ service, base } = await addReplica(true));
      const other = await addReplica(true);
      const aliceSocket = await connectAs(alice, other.base);

      service.sendToUser(alice.userId, 'notification', { sharded: true });

      expect(await aliceSocket.nextEvent()).to.deep.equal(['notification', { sharded: true }]);
      const subscriber = [...bus].find((client) => client.duplicatedWith !== undefined);
      expect(subscriber?.duplicatedWith).to.deep.equal({ shardedSubscribers: true });
      expect(other.redis.createPubSubClient.called).to.be.false;
    });

    it("shutting one replica down leaves the other replica's sockets connected", async () => {
      const other = await addReplica();
      const aliceSocket = await connectAs(alice, other.base);

      service.shutdown();
      // Let anything the closing replica published reach the other one.
      await new Promise((resolve) => setTimeout(resolve, 50));
      other.service.sendToUser(alice.userId, 'notification', { still: 'connected' });

      expect(await aliceSocket.nextEvent()).to.deep.equal(['notification', { still: 'connected' }]);
    });

    it('closes its Redis subscriber on shutdown', async () => {
      const [replica] = replicas;
      const subscriber = replica!.redis.createPubSubClient.firstCall.returnValue as FakePubSub;
      expect(bus.has(subscriber)).to.be.true;

      replica!.service.shutdown();

      expect(replica!.redis.release.calledOnceWith(subscriber)).to.be.true;
      await waitFor(() => !bus.has(subscriber));
    });
  });
});
