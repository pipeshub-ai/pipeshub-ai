import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import type { AddressInfo } from 'net';
import type { Server } from 'http';
import { Container } from 'inversify';
import { PASSWORD_RESET_REQUESTED, SIGN_IN_CODE_REQUESTED } from '../../../../src/modules/auth/controller/userAccount.controller';
import { createUserAccountRouter } from '../../../../src/modules/auth/routes/userAccount.routes';
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware';
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service';
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware';
import type { Logger } from '../../../../src/libs/services/logger.service';

// The per-address limit applies to the requests that ask about an address from outside (a password-reset or sign-in-code request)
// and never to the sign-in itself: a client that is throttled on one address can still sign in with the right password.

describe('per-address limit on the public sign-in routes (HTTP)', () => {
  let server: Server;
  let baseUrl: string;
  let counts: Map<string, number>;
  let authenticated: sinon.SinonStub;
  let tokens: AuthTokenService;
  let forgot: sinon.SinonStub;
  let otp: sinon.SinonStub;
  const saved = process.env.ADDRESS_RATE_LIMIT_PER_MINUTE;

  const send = async (method: string, path: string, headers: Record<string, string>) => {
    const res = await fetch(`${baseUrl}${path}`, { method, headers: { 'content-type': 'application/json', ...headers }, body: '{}', signal: AbortSignal.timeout(3000) });
    return res.status;
  };

  const post = async (path: string, body: unknown, headers: Record<string, string> = {}) => {
    const res = await fetch(`${baseUrl}${path}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', ...headers },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(3000),
    });
    return { status: res.status, retryAfter: res.headers.get('retry-after'), body: (await res.json().catch(() => ({}))) as unknown };
  };

  beforeEach(async () => {
    process.env.ADDRESS_RATE_LIMIT_PER_MINUTE = '5';
    counts = new Map();
    authenticated = sinon.stub().callsFake((_req: unknown, res: express.Response) => res.status(200).json({ message: 'Fully authenticated' }));
    const logger = { debug: sinon.stub(), info: sinon.stub(), warn: sinon.stub(), error: sinon.stub() } as unknown as Logger;
    forgot = sinon.stub().callsFake((_req: unknown, res: express.Response) => res.status(200).send(PASSWORD_RESET_REQUESTED));
    otp = sinon.stub().callsFake((_req: unknown, res: express.Response) => res.status(200).send(SIGN_IN_CODE_REQUESTED));
    const controller = {
      initAuth: (_req: unknown, res: express.Response) => res.status(200).json({ message: 'Authentication initialized' }),
      authenticate: authenticated,
      forgotPasswordEmail: forgot,
      validateEmailChange: (_req: unknown, res: express.Response) => res.status(200).json({ message: 'changed' }),
      getLoginOtp: otp,
    };
    const container = new Container();
    container.bind('Logger').toConstantValue(logger);
    container.bind('AppConfig').toConstantValue({ jwtSecret: 's', scopedJwtSecret: 'c', maxAuthRequestsPerMinute: 1000 });
    container.bind('SessionService').toConstantValue({ getSession: async () => ({ token: 't', email: 'ada@acme.test' }) });
    container.bind('UserAccountController').toConstantValue(controller);
    tokens = new AuthTokenService('s', 'c');
    container.bind('AuthMiddleware').toConstantValue(new AuthMiddleware(logger, tokens));
    container.bind('RedisService').toConstantValue({
      increment: async (key: string) => {
        counts.set(key, (counts.get(key) ?? 0) + 1);
        return counts.get(key) as number;
      },
    });
    const app = express();
    app.use(express.json());
    app.use('/userAccount', createUserAccountRouter(container));
    app.use(ErrorMiddleware.handleError());
    server = await new Promise<Server>((resolve) => {
      const s = app.listen(0, '127.0.0.1', () => resolve(s));
    });
    baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });

  afterEach(async () => {
    sinon.restore();
    if (saved === undefined) delete process.env.ADDRESS_RATE_LIMIT_PER_MINUTE;
    else process.env.ADDRESS_RATE_LIMIT_PER_MINUTE = saved;
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  });

  it('answers the sixth password-reset request about one address exactly like the first, sends nothing, and still lets that person sign in', async () => {
    const answers: unknown[] = [];
    for (let i = 0; i < 6; i++) {
      const r = await post('/userAccount/password/forgot', { email: 'ada@acme.test' });
      answers.push([r.status, r.retryAfter, JSON.stringify(r.body)]);
    }
    expect(new Set(answers.map((a) => JSON.stringify(a))).size).to.equal(1);
    expect((answers[0] as [number])[0]).to.equal(200);
    expect(forgot.callCount).to.equal(5);

    // the owner signs in with the right password in the same minute
    const init = await post('/userAccount/initAuth', { email: 'ada@acme.test' });
    expect(init.status).to.equal(200);
    const signedIn = await post('/userAccount/authenticate', { method: 'password', credentials: { password: 'Passw0rd!x' }, email: 'ada@acme.test' }, { 'x-session-token': 't' });
    expect(signedIn.status).to.equal(200);
    expect(authenticated.calledOnce).to.equal(true);
  });

  it('answers a throttled sign-in-code request like an accepted one and sends no code', async () => {
    const first = await post('/userAccount/login/otp/generate', { email: 'ada@acme.test' });
    for (let i = 0; i < 4; i++) await post('/userAccount/login/otp/generate', { email: 'ada@acme.test' });
    const over = await post('/userAccount/login/otp/generate', { email: 'ada@acme.test' });
    expect([over.status, over.retryAfter]).to.deep.equal([first.status, first.retryAfter]);
    expect(otp.callCount).to.equal(5);
  });

  it('counts each address on its own, and the sign-in-code request has its own count', async () => {
    for (let i = 0; i < 5; i++) await post('/userAccount/password/forgot', { email: 'ada@acme.test' });
    await post('/userAccount/password/forgot', { email: 'ADA@acme.test' });
    expect(forgot.callCount).to.equal(5);
    await post('/userAccount/password/forgot', { email: 'bob@acme.test' });
    expect(forgot.callCount).to.equal(6);
    await post('/userAccount/login/otp/generate', { email: 'ada@acme.test' });
    expect(otp.callCount).to.equal(1);
  });

  it('limits the email-change confirmation by the new address in the verified token, not by anything the client sends', async () => {
    const token = tokens.generateScopedToken({ newEmail: 'new@acme.test', scopes: ['email:validate'] } as never);
    const statuses: number[] = [];
    // the header and body name other addresses; only the token's address counts
    for (let i = 0; i < 6; i++) statuses.push(await send('PUT', '/userAccount/validateEmailChange', { authorization: `Bearer ${token}`, 'x-forwarded-for': `10.0.0.${i}` }));
    expect(statuses).to.deep.equal([200, 200, 200, 200, 200, 429]);
    // without a valid token the limiter is never reached
    expect(await send('PUT', '/userAccount/validateEmailChange', {})).to.equal(401);
  });
});
