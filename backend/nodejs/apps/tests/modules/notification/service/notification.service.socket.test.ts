import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import http from 'http';
import type { AddressInfo } from 'net';
import jwt from 'jsonwebtoken';
import { NotificationService } from '../../../../src/modules/notification/service/notification.service';
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service';
import type { Server as SocketServer } from 'socket.io';

// Runs the real Socket.IO server that NotificationService builds, with the real
// AuthTokenService and real signed tokens. The client below speaks Socket.IO's
// HTTP long-polling transport over fetch, so no client library is needed.

const JWT_SECRET = 'notification-socket-jwt-secret';

// The private state these tests look at, so assertions stay type-checked.
interface NotificationServiceInternals {
  io: SocketServer | null;
  connectedUsers: Set<string>;
  notificationQueue: Map<string, unknown[]>;
  queueNotification(userId: string, event: string, data: unknown): void;
}
const RECORD_SEPARATOR = '\x1e';

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

// The periodic queue pass runs every 5s; delivery on connect must not wait for it.
function within<T>(ms: number, promise: Promise<T>): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_, reject) =>
      setTimeout(() => reject(new Error(`nothing arrived within ${ms}ms`)), ms),
    ),
  ]);
}

async function waitFor(condition: () => boolean) {
  for (let i = 0; i < 100 && !condition(); i++) {
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  expect(condition()).to.be.true;
}

describe('NotificationService over a real socket', () => {
  let server: http.Server;
  let service: NotificationService;
  let base: string;
  let clients: PollingClient[];
  let intervalSpy: sinon.SinonSpy;
  const internals = (): NotificationServiceInternals =>
    service as unknown as NotificationServiceInternals;
  const socketsInRoom = (room: string): number =>
    internals().io?.sockets.adapter.rooms.get(room)?.size ?? 0;

  const alice = { userId: 'user-alice', orgId: 'org-1', role: 'member' };
  const bob = { userId: 'user-bob', orgId: 'org-1', role: 'member' };
  const carol = { userId: 'user-carol', orgId: 'org-2', role: 'admin' };

  function client() {
    const c = new PollingClient(base);
    clients.push(c);
    return c;
  }

  async function connectAs(claims: Record<string, unknown>) {
    const c = client();
    const reply = await c.connect({ token: sessionToken(claims) });
    expect(reply.startsWith('40{'), reply).to.be.true;
    return c;
  }

  beforeEach(async () => {
    clients = [];
    server = http.createServer();
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    service = new NotificationService(new AuthTokenService(JWT_SECRET, 'scoped-secret'));
    intervalSpy = sinon.spy(global, 'setInterval');
    service.initialize(server);
  });

  afterEach(async () => {
    await Promise.all(clients.map((c) => c.close()));
    service.shutdown();
    sinon.restore();
    await new Promise((resolve) => server.close(resolve));
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
      expect(service.sendToUser(alice.userId, 'notification', {})).to.be.false;
    });

    it('signs out a session whose token carries no role, then drops the connection', async () => {
      const c = client();
      const reply = await c.connect({ token: sessionToken({ userId: 'user-legacy', orgId: 'org-1' }) });

      expect(reply.startsWith('40')).to.be.true;
      expect(await c.nextEvent()).to.deep.equal(['force_logout', { reason: 'missing_role' }]);
      expect(await c.next()).to.equal('41');
      expect(service.sendToUser('user-legacy', 'notification', {})).to.be.false;
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

    it('holds notifications for a user who is offline and delivers them in order on connect', async () => {
      expect(service.sendToUser(alice.userId, 'notification', { n: 1 })).to.be.false;
      expect(service.sendToUser(alice.userId, 'notification', { n: 2 })).to.be.false;

      const aliceSocket = await connectAs(alice);

      expect(await within(1000, aliceSocket.nextEvent())).to.deep.equal(['notification', { n: 1 }]);
      expect(await within(1000, aliceSocket.nextEvent())).to.deep.equal(['notification', { n: 2 }]);
    });

    it('goes back to holding notifications once the user disconnects', async () => {
      const aliceSocket = await connectAs(alice);
      await aliceSocket.close();

      await waitFor(() => internals().connectedUsers.has(alice.userId) === false);
      expect(service.sendToUser(alice.userId, 'notification', { later: true })).to.be.false;
      expect(internals().notificationQueue.get(alice.userId)).to.have.length(1);
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

    it('delivers anything still queued for an online user on the next queue pass', async () => {
      const aliceSocket = await connectAs(alice);
      internals().queueNotification(alice.userId, 'notification', { retried: true });
      const queuePass = intervalSpy.getCalls().find((c) => c.args[1] === 5000)!.args[0];

      queuePass();

      expect(await aliceSocket.nextEvent()).to.deep.equal(['notification', { retried: true }]);
      expect(internals().notificationQueue.has(alice.userId)).to.be.false;
    });
  });

  describe('forced sign-out', () => {
    it('signs out a connected user straight away', async () => {
      const aliceSocket = await connectAs(alice);

      expect(service.emitForceLogout(alice.userId, 'role_changed')).to.be.true;
      expect(await aliceSocket.nextEvent()).to.deep.equal(['force_logout', { reason: 'role_changed' }]);
    });

    it('does not queue a sign-out for someone offline, so their next sign-in is not undone', async () => {
      expect(service.emitForceLogout(alice.userId)).to.be.false;

      const aliceSocket = await connectAs(alice);
      service.sendToUser(alice.userId, 'notification', { first: true });

      expect(await aliceSocket.nextEvent()).to.deep.equal(['notification', { first: true }]);
    });
  });
});
