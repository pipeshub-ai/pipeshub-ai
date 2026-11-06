import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import http from 'http';
import { EventEmitter } from 'events';
import type { AddressInfo } from 'net';
import jwt from 'jsonwebtoken';
import {
  NotificationService,
  NotificationSocket,
  SocketExtension,
} from '../../../../src/modules/notification/service/notification.service';
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service';
import type { Server as SocketServer } from 'socket.io';
import {
  createFakeRedisProvider,
  FakeRedisClient,
  FakeRedisProvider,
} from '../../../helpers/fake-redis-provider';
import { NoRedisProvider } from '../../../../src/libs/services/redis/noRedisProvider';
import { redisConnectionConfigFromHostPort } from '../../../../src/libs/services/redis/connectionConfig';

// Runs the real Socket.IO server that NotificationService builds, with the real
// AuthTokenService and real signed tokens. The client below speaks Socket.IO's
// HTTP long-polling transport over fetch, so no client library is needed.

const JWT_SECRET = 'notification-socket-jwt-secret';

// The private state these tests look at, so assertions stay type-checked.
interface NotificationServiceInternals {
  io: SocketServer | null;
  sessionRefusal: sinon.SinonStub;
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

  /** An event from the client, asking for an acknowledgement under `ackId`. */
  async emit(event: string, payload: unknown, ackId: number) {
    await this.post(`42${ackId}${JSON.stringify([event, payload])}`);
  }

  async nextAck(ackId: number): Promise<unknown[]> {
    for (;;) {
      const packet = await this.next();
      if (packet === '2') {
        await this.post('3');
        continue;
      }
      if (packet.startsWith(`43${ackId}[`)) {
        return JSON.parse(packet.slice(`43${ackId}`.length));
      }
      throw new Error(`expected acknowledgement ${ackId}, got ${packet}`);
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
  // Every session is live unless a test says otherwise; the rules themselves are tested in user-session.utils.
  (service as unknown as NotificationServiceInternals).sessionRefusal = sinon.stub().resolves(null);
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

  // These replicas share a Redis (the fake bus): pin Redis mode, whatever the runner's environment says.
  const savedMode = process.env.REDIS_MODE;

  beforeEach(async () => {
    process.env.REDIS_MODE = 'standalone';
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
    if (savedMode === undefined) delete process.env.REDIS_MODE;
    else process.env.REDIS_MODE = savedMode;
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

    it('signs out a session that has since ended, then drops the connection', async () => {
      internals().sessionRefusal.resolves('Session expired, please login again');
      const c = client();
      const reply = await c.connect({ token: sessionToken(alice) });

      expect(reply.startsWith('40')).to.be.true;
      expect(await c.nextEvent()).to.deep.equal(['force_logout', { reason: 'session_ended' }]);
      expect(await c.next()).to.equal('41');
      expect(internals().sessionRefusal.firstCall.args[0]).to.include(alice);
      expect(socketsInRoom(alice.userId)).to.equal(0);
    });

    it('turns away, without signing out, a session that cannot be checked right now', async () => {
      internals().sessionRefusal.rejects(new Error('mongo down'));
      const reply = await client().connect({ token: sessionToken(alice) });

      expect(reply.startsWith('44')).to.be.true;
      expect(JSON.parse(reply.slice(2)).message).to.equal('Unable to check the session right now');
      expect(socketsInRoom(alice.userId)).to.equal(0);
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

    it("logs why a user's last socket went away, so a proxy timeout can be told from a silent client", async () => {
      const info = sinon.spy(
        (internals() as unknown as { logger: { info: (msg: string) => void } }).logger,
        'info',
      );
      const tab = await connectAs(alice);
      await tab.close();
      await waitFor(() => socketsInRoom(alice.userId) === 0);
      await waitFor(() => info.getCalls().some((c) => c.args[0].startsWith('User disconnected')));
      const line = info.getCalls().find((c) => c.args[0].startsWith('User disconnected'))!.args[0];
      expect(line).to.match(/reason: \w[\w ]*$/);
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
  describe('socket extensions (use)', () => {
    /** An extension that records what it was given and answers `echo` with its socket's user. */
    function recording(over: Partial<SocketExtension> = {}) {
      const started: NotificationService[] = [];
      const sockets: NotificationSocket[] = [];
      const stop = sinon.stub().resolves();
      const extension: SocketExtension = {
        start: (s) => {
          started.push(s);
        },
        connected: (socket) => {
          sockets.push(socket);
          socket.on('echo', (payload: unknown, ack: (reply: unknown) => void) =>
            ack({ userId: socket.data.userId, payload }),
          );
        },
        stop,
        ...over,
      };
      return { extension, started, sockets, stop };
    }
    const errors = () =>
      sinon.spy((internals() as unknown as { logger: { error: (msg: string) => void } }).logger, 'error');

    it('an extension added before the server is up starts with it, and hears each signed-in socket', async () => {
      const server = http.createServer();
      await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
      const late = new NotificationService(new AuthTokenService(JWT_SECRET, 'scoped-secret'), redisOn(bus));
      (late as unknown as NotificationServiceInternals).sessionRefusal = sinon.stub().resolves(null);
      const { extension, started, sockets } = recording();
      late.use(extension);
      expect(started).to.have.length(0);
      late.initialize(server);
      replicas.push({ server, service: late, redis: redisOn(bus), base: '' });
      expect(started).to.deep.equal([late]);

      const on = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
      const aliceSocket = await connectAs(alice, on);
      await aliceSocket.emit('echo', { hi: 1 }, 1);
      expect(await aliceSocket.nextAck(1)).to.deep.equal([{ userId: alice.userId, payload: { hi: 1 } }]);
      expect(sockets.map((s) => s.data.orgId)).to.deep.equal([alice.orgId]);
    });

    it('an extension added once the server is up starts at once', () => {
      const { extension, started } = recording();
      service.use(extension);
      expect(started).to.deep.equal([service]);
    });

    it('never hears a socket that was turned away or signed out on connect', async () => {
      const { extension, sockets } = recording();
      service.use(extension);
      await client().connect({});
      const legacy = client();
      await legacy.connect({ token: sessionToken({ userId: 'user-legacy', orgId: 'org-1' }) });
      expect(await legacy.nextEvent()).to.deep.equal(['force_logout', { reason: 'missing_role' }]);
      expect(sockets).to.have.length(0);
    });

    it("an extension that throws breaks neither the server nor another extension", async () => {
      const error = errors();
      const broken = recording({
        start: () => {
          throw new Error('start failed');
        },
        connected: () => {
          throw new Error('connect failed');
        },
      });
      const working = recording();
      service.use(broken.extension);
      service.use(working.extension);
      expect(working.started).to.deep.equal([service]);

      const aliceSocket = await connectAs(alice);
      await aliceSocket.emit('echo', 'still here', 7);
      expect(await aliceSocket.nextAck(7)).to.deep.equal([{ userId: alice.userId, payload: 'still here' }]);
      service.sendToUser(alice.userId, 'notification', { delivered: true });
      expect(await aliceSocket.nextEvent()).to.deep.equal(['notification', { delivered: true }]);
      expect(error.getCalls().map((c) => c.args[0])).to.include.members([
        'Socket extension failed to start',
        'Socket extension failed on connect',
      ]);
    });

    it('shutdown stops every extension, one that fails to stop included', async () => {
      const failing = recording({ stop: sinon.stub().rejects(new Error('stuck')) });
      const fine = recording();
      service.use(failing.extension);
      service.use(fine.extension);
      service.shutdown();
      await new Promise((resolve) => setImmediate(resolve));
      expect((failing.extension.stop as sinon.SinonStub).calledOnce).to.be.true;
      expect(fine.stop.calledOnce).to.be.true;
    });

    it('emitToRoom reaches the room on every replica, and says false before the server is up', async () => {
      const idle = new NotificationService(new AuthTokenService(JWT_SECRET, 'scoped-secret'), redisOn(bus));
      (idle as unknown as NotificationServiceInternals).sessionRefusal = sinon.stub().resolves(null);
      expect(idle.emitToRoom('room-1', 'evt', {})).to.be.false;

      const other = await addReplica();
      const join = recording({
        connected: (socket) => {
          if (socket.data.userId !== carol.userId) void socket.join('room-1');
        },
      });
      service.use(join.extension);
      other.service.use(join.extension);
      const here = await connectAs(alice);
      const there = await connectAs(bob, other.base);
      const outside = await connectAs(carol);
      await waitFor(() => socketsInRoom('room-1') === 1);

      expect(service.emitToRoom('room-1', 'evt', { n: 1 })).to.be.true;
      service.sendToUser(carol.userId, 'notification', { last: true });

      expect(await here.nextEvent()).to.deep.equal(['evt', { n: 1 }]);
      expect(await there.nextEvent()).to.deep.equal(['evt', { n: 1 }]);
      expect(await outside.nextEvent(), 'not in the room').to.deep.equal(['notification', { last: true }]);
    });

    it("emitToLocalRoom, emitToAllLocal and leaveLocalRoom act on this replica's sockets only", async () => {
      const other = await addReplica();
      const join = recording({ connected: (socket) => void socket.join('room-1') });
      service.use(join.extension);
      other.service.use(join.extension);
      const here = await connectAs(alice);
      const there = await connectAs(bob, other.base);
      await waitFor(() => socketsInRoom('room-1') === 1);

      service.emitToLocalRoom('room-1', 'local', { n: 1 });
      service.emitToAllLocal('everyone-here', { n: 2 });
      other.service.sendToUser(bob.userId, 'notification', { marker: true });
      expect(await here.nextEvent()).to.deep.equal(['local', { n: 1 }]);
      expect(await here.nextEvent()).to.deep.equal(['everyone-here', { n: 2 }]);
      expect(await there.nextEvent(), 'the other replica sent nothing of its own').to.deep.equal([
        'notification',
        { marker: true },
      ]);

      service.leaveLocalRoom('room-1');
      expect(socketsInRoom('room-1')).to.equal(0);
      expect(other.service.emitToRoom('room-1', 'after', {})).to.be.true;
      expect(await there.nextEvent(), "the other replica's socket is still in the room").to.deep.equal(['after', {}]);
    });

    it("localSocketsIn lists this replica's sockets in a room, each with its identity and its own join, leave and emit", async () => {
      const idle = new NotificationService(new AuthTokenService(JWT_SECRET, 'scoped-secret'), redisOn(bus));
      (idle as unknown as NotificationServiceInternals).sessionRefusal = sinon.stub().resolves(null);
      expect(await idle.localSocketsIn('room-1')).to.deep.equal([]);

      const other = await addReplica();
      const join = recording({ connected: (socket) => void socket.join('room-1') });
      service.use(join.extension);
      other.service.use(join.extension);
      const here = await connectAs(alice);
      await connectAs(bob, other.base);
      await waitFor(() => socketsInRoom('room-1') === 1);

      const listed = await service.localSocketsIn('room-1');
      expect(listed.map((s) => s.data.userId)).to.deep.equal([alice.userId]);
      listed[0].emit('just-you', { n: 3 });
      expect(await here.nextEvent()).to.deep.equal(['just-you', { n: 3 }]);
      listed[0].join('room-2');
      listed[0].leave('room-1');
      await waitFor(() => socketsInRoom('room-1') === 0);
      expect((await service.localSocketsIn('room-2')).map((s) => s.data.userId)).to.deep.equal([alice.userId]);
    });
  });
});

// PostgreSQL mode (PIPESHUB_STORE=postgres sets REDIS_MODE=postgres) runs one API process with no Redis server.
describe('NotificationService without a Redis server (REDIS_MODE=postgres)', () => {
  const saved = process.env.REDIS_MODE;
  let server: http.Server;
  let service: NotificationService;
  let provider: NoRedisProvider;
  let clients: PollingClient[];

  beforeEach(async () => {
    process.env.REDIS_MODE = 'postgres';
    clients = [];
    provider = new NoRedisProvider(redisConnectionConfigFromHostPort({ host: 'localhost', port: 6379 }), 'postgres');
    server = http.createServer();
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    service = new NotificationService(new AuthTokenService(JWT_SECRET, 'scoped-secret'), provider);
    (service as unknown as NotificationServiceInternals).sessionRefusal = sinon.stub().resolves(null);
  });

  afterEach(async () => {
    await Promise.all(clients.map((c) => c.close()));
    service.shutdown();
    await new Promise((resolve) => server.close(resolve));
    if (saved === undefined) delete process.env.REDIS_MODE;
    else process.env.REDIS_MODE = saved;
    sinon.restore();
  });

  it('starts on the in-process adapter and asks the provider for no Redis connection', () => {
    const getClient = sinon.spy(provider, 'getClient');
    const createPubSubClient = sinon.spy(provider, 'createPubSubClient');

    expect(() => service.initialize(server)).to.not.throw();

    expect(getClient.called).to.equal(false);
    expect(createPubSubClient.called).to.equal(false);
    const io = (service as unknown as NotificationServiceInternals).io;
    expect(io?.of('/').adapter.constructor.name).to.equal('Adapter');
  });

  it('still delivers to a signed-in user on this process', async () => {
    service.initialize(server);
    const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    const alice = new PollingClient(base);
    clients.push(alice);
    const reply = await alice.connect({ token: sessionToken({ userId: 'user-alice', orgId: 'org-1', role: 'member' }) });
    expect(reply.startsWith('40{'), reply).to.be.true;

    expect(service.sendToUser('user-alice', 'notification', { pg: true })).to.be.true;

    expect(await alice.nextEvent()).to.deep.equal(['notification', { pg: true }]);
  });
});
