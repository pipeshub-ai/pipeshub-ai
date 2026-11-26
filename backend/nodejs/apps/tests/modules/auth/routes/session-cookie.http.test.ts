import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import type { AddressInfo } from 'net';
import type { Server } from 'http';
import { Container } from 'inversify';
import { createUserAccountRouter } from '../../../../src/modules/auth/routes/userAccount.routes';
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware';
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service';
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware';
import { refreshTokenJwtGenerator } from '../../../../src/libs/utils/createJwt';
import { UnauthorizedError } from '../../../../src/libs/errors/http.errors';
import { corsOptions } from '../../../../src/libs/middlewares/cors.options';
import {
  adoptIntoCookieRequested,
  cookieRequestOriginAllowed,
  parseCookieHeader,
  PLAIN_SESSION_COOKIE,
  SECURE_SESSION_COOKIE,
  setSessionCookie,
  signedOutSessionTokens,
} from '../../../../src/modules/auth/session-cookie';
import type { Logger } from '../../../../src/libs/services/logger.service';

// R41OPS-FU-7 (ADR-WEB-02): the browser's refresh token is an HttpOnly cookie. The refresh and sign-out routes
// read it only from a page of this server, and a bearer header keeps working for every other client.
describe('the session cookie on the refresh and sign-out routes (HTTP)', () => {
  let server: Server;
  let baseUrl: string;
  let host: string;
  let refreshed: sinon.SinonStub;
  let loggedOut: sinon.SinonStub;
  const savedEnv = { ALLOWED_ORIGINS: process.env.ALLOWED_ORIGINS, FRONTEND_PUBLIC_URL: process.env.FRONTEND_PUBLIC_URL };
  const refreshToken = () => refreshTokenJwtGenerator('u1', 'o1', 'c');

  const post = async (path: string, headers: Record<string, string> = {}) => {
    const res = await fetch(`${baseUrl}${path}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', ...headers },
      body: '{}',
      signal: AbortSignal.timeout(3000),
    });
    return {
      status: res.status,
      setCookies: res.headers.getSetCookie(),
      body: (await res.json().catch(() => ({}))) as Record<string, unknown>,
    };
  };
  const pageOrigin = () => `http://${host}`;

  beforeEach(async () => {
    process.env.ALLOWED_ORIGINS = 'http://localhost:3001';
    process.env.FRONTEND_PUBLIC_URL = 'https://pipeshub.example.com';
    const logger = { debug: sinon.stub(), info: sinon.stub(), warn: sinon.stub(), error: sinon.stub() } as unknown as Logger;
    refreshed = sinon.stub().callsFake((req: express.Request & { verifiedToken?: string; tokenPayload?: Record<string, unknown> }, res: express.Response) => {
      if (adoptIntoCookieRequested(res) && req.verifiedToken) setSessionCookie(req, res, req.verifiedToken);
      res.status(200).json({ accessToken: 'access', userId: req.tokenPayload?.userId });
    });
    loggedOut = sinon.stub().callsFake((_req: unknown, res: express.Response) => res.status(200).end());
    const container = new Container();
    container.bind('Logger').toConstantValue(logger);
    container.bind('AppConfig').toConstantValue({ jwtSecret: 's', scopedJwtSecret: 'c', maxAuthRequestsPerMinute: 1000 });
    container.bind('SessionService').toConstantValue({ getSession: async () => null });
    container.bind('UserAccountController').toConstantValue({
      getAccessTokenFromRefreshToken: refreshed,
      logoutSession: loggedOut,
    });
    const middleware = new AuthMiddleware(logger, new AuthTokenService('s', 'c'));
    // An access token is checked as it always was; here only its presence matters.
    middleware.authenticate = (async (req: express.Request & { user?: unknown }, _res: express.Response, next: express.NextFunction) => {
      if (req.headers.authorization === 'Bearer access') {
        req.user = { userId: 'u1', orgId: 'o1' };
        next();
        return;
      }
      next(new UnauthorizedError('No token provided'));
    }) as unknown as AuthMiddleware['authenticate'];
    container.bind('AuthMiddleware').toConstantValue(middleware);
    container.bind('RedisService').toConstantValue({ increment: async () => 1 });
    const app = express();
    app.use(express.json());
    app.use('/api/v1/userAccount', createUserAccountRouter(container));
    app.use(ErrorMiddleware.handleError());
    server = await new Promise<Server>((resolve) => {
      const s = app.listen(0, '127.0.0.1', () => resolve(s));
    });
    host = `127.0.0.1:${(server.address() as AddressInfo).port}`;
    baseUrl = `http://${host}`;
  });

  afterEach(async () => {
    sinon.restore();
    for (const [key, value] of Object.entries(savedEnv)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  });

  describe('refresh', () => {
    it('refreshes from the cookie alone when the page is this server', async () => {
      const r = await post('/api/v1/userAccount/refresh/token', {
        origin: pageOrigin(),
        cookie: `${PLAIN_SESSION_COOKIE}=${refreshToken()}`,
      });
      expect(r.status).to.equal(200);
      expect(r.body).to.include({ accessToken: 'access', userId: 'u1' });
      expect(refreshed.calledOnce).to.equal(true);
    });

    it('accepts the configured UI origin (the from-source split on :3001) and FRONTEND_PUBLIC_URL', async () => {
      for (const origin of ['http://localhost:3001']) {
        const r = await post('/api/v1/userAccount/refresh/token', { origin, cookie: `${PLAIN_SESSION_COOKIE}=${refreshToken()}` });
        expect(r.status, origin).to.equal(200);
      }
      const https = await post('/api/v1/userAccount/refresh/token', {
        origin: 'https://pipeshub.example.com',
        cookie: `${SECURE_SESSION_COOKIE}=${refreshToken()}`,
      });
      expect(https.status).to.equal(200);
    });

    for (const [label, headers] of [
      ['another site', { origin: 'https://attacker.test' }],
      ['a sibling subdomain (same site, other origin)', { origin: 'https://evil.example.com' }],
      ['no Origin at all', {}],
      ['an opaque origin', { origin: 'null' }],
      ['a cross-site fetch, whatever its Origin says', { origin: 'http://localhost:3001', 'sec-fetch-site': 'cross-site' }],
    ] as const) {
      it(`refuses the cookie from ${label} (CSRF) and never refreshes`, async () => {
        const r = await post('/api/v1/userAccount/refresh/token', {
          ...headers,
          cookie: `${PLAIN_SESSION_COOKIE}=${refreshToken()}; ${SECURE_SESSION_COOKIE}=${refreshToken()}`,
        });
        expect(r.status).to.equal(403);
        expect(refreshed.called).to.equal(false);
      });
    }

    it('an https page never reads the plain cookie (a cookie planted over http)', async () => {
      const r = await post('/api/v1/userAccount/refresh/token', {
        origin: 'https://pipeshub.example.com',
        cookie: `${PLAIN_SESSION_COOKIE}=${refreshToken()}`,
      });
      expect(r.status).to.equal(401);
      expect(refreshed.called).to.equal(false);
    });

    it('clears a refused cookie so the browser stops sending it', async () => {
      const r = await post('/api/v1/userAccount/refresh/token', {
        origin: pageOrigin(),
        cookie: `${PLAIN_SESSION_COOKIE}=not-a-jwt`,
      });
      expect(r.status).to.equal(401);
      expect(r.setCookies.join('\n')).to.match(new RegExp(`${PLAIN_SESSION_COOKIE}=;`));
    });

    it('an access token in the cookie is not a refresh token', async () => {
      const r = await post('/api/v1/userAccount/refresh/token', {
        origin: pageOrigin(),
        cookie: `${PLAIN_SESSION_COOKIE}=access`,
      });
      expect(r.status).to.equal(401);
    });

    it('a bearer refresh token works as before and sets no cookie (desktop, scripts, the SDK)', async () => {
      const r = await post('/api/v1/userAccount/refresh/token', { authorization: `Bearer ${refreshToken()}` });
      expect(r.status).to.equal(200);
      expect(r.setCookies).to.deep.equal([]);
    });

    it('moves a bearer refresh token into the cookie when the page asks (the localStorage migration)', async () => {
      const token = refreshToken();
      const r = await post('/api/v1/userAccount/refresh/token', {
        authorization: `Bearer ${token}`,
        'x-pipeshub-session': 'cookie',
        origin: 'https://pipeshub.example.com',
      });
      expect(r.status).to.equal(200);
      const cookie = r.setCookies.find((c) => c.startsWith(`${SECURE_SESSION_COOKIE}=`));
      expect(cookie).to.be.a('string');
      expect(parseCookieHeader(cookie!.split(';')[0]).get(SECURE_SESSION_COOKIE)).to.equal(token);
      expect(cookie).to.match(/; Path=\/;/);
      expect(cookie).to.match(/HttpOnly/);
      expect(cookie).to.match(/Secure/);
      expect(cookie).to.match(/SameSite=Strict/);
      expect(cookie).to.match(/Max-Age=\d+/);
      expect(cookie).to.not.match(/Domain=/i);
    });
  });

  describe('sign-out', () => {
    it('clears both cookies on a bearer sign-out', async () => {
      const r = await post('/api/v1/userAccount/logout/manual', { authorization: 'Bearer access', origin: pageOrigin() });
      expect(r.status).to.equal(200);
      expect(loggedOut.calledOnce).to.equal(true);
      const cleared = r.setCookies.join('\n');
      expect(cleared).to.match(new RegExp(`${SECURE_SESSION_COOKIE}=;.*Path=/;.*Expires=Thu, 01 Jan 1970`));
      expect(cleared).to.match(new RegExp(`${PLAIN_SESSION_COOKIE}=;.*Path=/api/v1/userAccount/;`));
    });

    it('signs out with the cookie alone, recording the sign-out for its user', async () => {
      const r = await post('/api/v1/userAccount/logout/manual', {
        origin: pageOrigin(),
        cookie: `${PLAIN_SESSION_COOKIE}=${refreshToken()}`,
      });
      expect(r.status).to.equal(200);
      expect(loggedOut.firstCall.args[0].user).to.deep.include({ userId: 'u1', orgId: 'o1' });
      expect(r.setCookies.join('\n')).to.include(`${PLAIN_SESSION_COOKIE}=;`);
    });

    it('hands the controller every session cookie the sign-out carried, so it can end their families (R48MIGFIX-3)', async () => {
      const own = refreshToken();
      const orgs = refreshTokenJwtGenerator('u1', 'o2', 'c');
      const r = await post('/api/v1/userAccount/logout/manual', {
        authorization: 'Bearer access',
        origin: pageOrigin(),
        cookie: `${PLAIN_SESSION_COOKIE}=${own}; ${PLAIN_SESSION_COOKIE}.o2=${orgs}; other=x`,
      });
      expect(r.status).to.equal(200);
      expect(signedOutSessionTokens(loggedOut.firstCall.args[1])).to.have.members([own, orgs]);
      const bare = await post('/api/v1/userAccount/logout/manual', { authorization: 'Bearer access', origin: pageOrigin() });
      expect(bare.status).to.equal(200);
      expect(signedOutSessionTokens(loggedOut.secondCall.args[1])).to.deep.equal([]);
    });

    it('refuses a cookie sign-out from another origin and keeps the cookie', async () => {
      const r = await post('/api/v1/userAccount/logout/manual', {
        origin: 'http://attacker.test',
        cookie: `${PLAIN_SESSION_COOKIE}=${refreshToken()}`,
      });
      expect(r.status).to.equal(403);
      expect(loggedOut.called).to.equal(false);
      expect(r.setCookies).to.deep.equal([]);
    });

    it('clears the cookie even when the server refuses the stale access token', async () => {
      const r = await post('/api/v1/userAccount/logout/manual', { authorization: 'Bearer expired', origin: pageOrigin() });
      expect(r.status).to.equal(401);
      expect(r.setCookies.join('\n')).to.include(`${SECURE_SESSION_COOKIE}=;`);
    });
  });

  describe('the rules on their own', () => {
    const req = (headers: Record<string, string>) => ({ headers }) as unknown as express.Request;

    it('matches the Origin to the Host when nothing is configured', () => {
      expect(cookieRequestOriginAllowed(req({ origin: 'http://app.local:3000', host: 'app.local:3000' }), {})).to.equal(true);
      expect(cookieRequestOriginAllowed(req({ origin: 'http://app.local:3001', host: 'app.local:3000' }), { ALLOWED_ORIGINS: '' })).to.equal(false);
    });

    it('parses quoted, encoded and repeated cookies, keeping the first of a name', () => {
      const parsed = parseCookieHeader('a=1; b="two"; c=%20x; a=again; =bad; d');
      expect([...parsed.entries()]).to.deep.equal([['a', '1'], ['b', 'two'], ['c', ' x']]);
    });

    it('lets the web app send the delivery header across the from-source split', () => {
      expect(corsOptions({}).allowedHeaders).to.include('x-pipeshub-session');
    });
  });
});
