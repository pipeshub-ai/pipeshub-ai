import 'reflect-metadata';
import { expect } from 'chai';
import fs from 'fs';
import http from 'http';
import path from 'path';
import type { AddressInfo } from 'net';
import jwt from 'jsonwebtoken';
import sinon from 'sinon';
import { NotificationService } from '../../src/modules/notification/service/notification.service';
import { DesktopProxySocketGateway } from '../../src/modules/desktop_proxy/socket/desktop-proxy.gateway';
import { AuthTokenService } from '../../src/libs/services/authtoken.service';
import { HANDSHAKE_REFUSAL } from '../../src/libs/utils/socket-handshake-refusal';

/**
 * SOCKET HANDSHAKE REFUSALS
 * -------------------------
 * The web notification socket retries a refused handshake only when the
 * refusal's `data.retryable` is true, and never retries an auth refusal
 * (frontend/lib/socket/notification-socket.ts). Its tests read the refusals
 * from socket-handshake-refusals.fixture.json, so this test pins that file to
 * what the real gateways send. For the notification socket that is the
 * CONNECT_ERROR packet off a real Socket.IO server; the desktop gateway's
 * refusals are the errors its handshake middleware passes to `next()`, which
 * Socket.IO sends as `{ message, data }`.
 *
 * Re-record when a refusal changes ON PURPOSE:
 *   UPDATE_SOCKET_REFUSALS_FIXTURE=1 TS_NODE_PROJECT=tsconfig.test.json \
 *     npx mocha --no-parallel --spec tests/contract/socket-handshake-refusals.test.ts
 * and check the frontend socket tests still pass.
 */
const FIXTURE = path.join(__dirname, 'socket-handshake-refusals.fixture.json');
const JWT_SECRET = 'handshake-contract-secret';
const member = { userId: 'user-1', orgId: 'org-1', role: 'member' };

type Refusal = { message: string; data?: unknown };
type Recorded = {
  codes: string[];
  notification: Record<string, Refusal>;
  desktopProxy: Record<string, Refusal>;
};

const bearer = (claims: object, secret = JWT_SECRET) =>
  `Bearer ${jwt.sign(claims, secret, { expiresIn: '1h' })}`;

// One Engine.IO long-polling handshake, then the Socket.IO CONNECT packet; returns the server's answer.
async function connectAnswer(base: string, auth: object): Promise<string> {
  const url = (sid?: string) =>
    `${base}/socket.io/?EIO=4&transport=polling${sid ? `&sid=${sid}` : ''}`;
  const open = await (await fetch(url())).text();
  const sid = JSON.parse(open.slice(1)).sid as string;
  const posted = await fetch(url(sid), {
    method: 'POST',
    body: `40${JSON.stringify(auth)}`,
  });
  expect(posted.status).to.equal(200);
  return (await (await fetch(url(sid))).text()).split('\x1e')[0];
}

async function notificationRefusals(): Promise<Record<string, Refusal>> {
  const server = http.createServer();
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const service = new NotificationService(
    new AuthTokenService(JWT_SECRET, 'scoped-secret'),
  );
  const internals = service as unknown as { sessionRefusal: sinon.SinonStub };
  internals.sessionRefusal = sinon
    .stub()
    .rejects(new Error('database unreachable'));
  service.initialize(server);
  const refusal = async (auth: object): Promise<Refusal> => {
    const packet = await connectAnswer(base, auth);
    expect(packet.startsWith('44'), packet).to.equal(true);
    return JSON.parse(packet.slice(2));
  };
  try {
    return {
      tokenMissing: await refusal({}),
      tokenInvalid: await refusal({ token: bearer(member, 'another-key') }),
      sessionCheckUnavailable: await refusal({ token: bearer(member) }),
    };
  } finally {
    service.shutdown();
    await new Promise((resolve) => server.close(resolve));
  }
}

async function desktopProxyRefusals(): Promise<Record<string, Refusal>> {
  const verifyToken = sinon.stub();
  const findSessionRefusal = sinon.stub();
  const gateway = new DesktopProxySocketGateway(
    { verifyToken } as never,
    { findSessionRefusal, findSessionRefusals: sinon.stub() } as never,
  ) as unknown as {
    authenticateHandshake(socket: object): Promise<Error | undefined>;
  };
  const refusal = async (token: string): Promise<Refusal> => {
    const error = (await gateway.authenticateHandshake({
      handshake: { auth: { token } },
      data: {},
    })) as (Error & { data?: unknown }) | undefined;
    expect(error, 'handshake was accepted').to.be.instanceOf(Error);
    return JSON.parse(
      JSON.stringify({ message: error!.message, data: error!.data }),
    );
  };

  const tokenMissing = await refusal('');
  verifyToken.rejects(new Error('jwt expired'));
  const tokenInvalid = await refusal('Bearer t');
  verifyToken.resolves({ role: 'member' });
  const tokenWithoutIds = await refusal('Bearer t');
  verifyToken.resolves(member);
  findSessionRefusal.resolves('User not found, please login again');
  const sessionRefused = await refusal('Bearer t');
  findSessionRefusal.rejects(new Error('database unreachable'));
  const sessionCheckUnavailable = await refusal('Bearer t');
  return {
    tokenMissing,
    tokenInvalid,
    tokenWithoutIds,
    sessionRefused,
    sessionCheckUnavailable,
  };
}

describe('Socket handshake refusals (contract with the web and desktop socket clients)', function () {
  this.timeout(20000);
  let actual: Recorded;

  before(async () => {
    // `codes` lets the client tests cover every code, including ones no recorded refusal uses.
    actual = {
      codes: Object.values(HANDSHAKE_REFUSAL),
      notification: await notificationRefusals(),
      desktopProxy: await desktopProxyRefusals(),
    };
    if (process.env.UPDATE_SOCKET_REFUSALS_FIXTURE === '1') {
      fs.writeFileSync(FIXTURE, `${JSON.stringify(actual, null, 2)}\n`);
    }
  });

  after(() => sinon.restore());

  it('sends exactly the recorded refusals', () => {
    const recorded = JSON.parse(fs.readFileSync(FIXTURE, 'utf8')) as Recorded;
    expect(actual).to.deep.equal(recorded);
  });

  it('marks a session that cannot be checked as retryable, with its code, on both gateways', () => {
    for (const gateway of [actual.notification, actual.desktopProxy]) {
      expect(gateway.sessionCheckUnavailable.data).to.deep.equal({
        code: 'SESSION_CHECK_UNAVAILABLE',
        retryable: true,
      });
    }
  });

  it('marks every auth refusal as not retryable, with a code', () => {
    for (const [gateway, refusals] of Object.entries({
      notification: actual.notification,
      desktopProxy: actual.desktopProxy,
    })) {
      for (const [name, refusal] of Object.entries(refusals)) {
        if (name === 'sessionCheckUnavailable') continue;
        const data = refusal.data as
          { code?: unknown; retryable?: unknown } | undefined;
        expect(data?.retryable, `${gateway}.${name}`).to.equal(false);
        expect(data?.code, `${gateway}.${name}`)
          .to.be.a('string')
          .and.match(/^AUTH_/);
      }
    }
  });
});
