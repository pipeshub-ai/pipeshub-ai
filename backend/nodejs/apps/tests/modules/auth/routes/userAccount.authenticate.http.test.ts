import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import type { AddressInfo } from 'net';
import type { Server } from 'http';
import mongoose from 'mongoose';
import { Container } from 'inversify';
import { createUserAccountRouter } from '../../../../src/modules/auth/routes/userAccount.routes';
import {
  UserAccountController,
} from '../../../../src/modules/auth/controller/userAccount.controller';
import { SessionService } from '../../../../src/modules/auth/services/session.service';
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware';
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service';
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware';
import type { Logger } from '../../../../src/libs/services/logger.service';
import type { ICacheService } from '../../../../src/libs/services/cache/cacheService.interface';
import { OrgAuthConfig } from '../../../../src/modules/auth/schema/orgAuthConfiguration.schema';
import { SIGN_IN_METHOD_NOT_ALLOWED } from '../../../../src/modules/auth/utils/authMethodGuard';
import { Org } from '../../../../src/modules/user_management/schema/org.schema';
import { createMockRedisService } from '../../../helpers/mock-redis';
import { useCommunitySignInLists } from '../../../helpers/sign-in-lists';

// Serves the real sign-in router over HTTP with the real controller and the
// real session service, so a request that never gets an answer shows up as a
// timeout. Only Mongo, the session cache, and other services are faked.

const JWT_SECRET = 'authenticate-http-jwt-secret';
const SCOPED_SECRET = 'authenticate-http-scoped-secret';
const ANSWER_WITHIN_MS = 3000;

describe('POST /userAccount/authenticate over HTTP', () => {
  useCommunitySignInLists();

  const orgId = new mongoose.Types.ObjectId().toHexString();
  let server: Server;
  let baseUrl: string;

  async function post(
    path: string,
    body: unknown,
    headers: Record<string, string> = {},
  ): Promise<{ status: number; headers: Headers; body: unknown }> {
    const response = await fetch(`${baseUrl}${path}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', ...headers },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(ANSWER_WITHIN_MS),
    });
    return { status: response.status, headers: response.headers, body: await response.json() };
  }

  beforeEach(async () => {
    const store = new Map<string, string>();
    const redis = createMockRedisService();
    redis.set.callsFake(async (key: string, value: unknown) => {
      store.set(key, JSON.stringify(value));
    });
    redis.get.callsFake(async (key: string) => {
      const raw = store.get(key);
      return raw === undefined ? null : JSON.parse(raw);
    });
    const sessionService = new SessionService(redis as unknown as ICacheService);

    sinon.stub(Org, 'findOne').callsFake((() =>
      Promise.resolve({ _id: orgId, shortName: 'Acme' })) as unknown as typeof Org.findOne);
    sinon.stub(OrgAuthConfig, 'findOne').callsFake((() =>
      Promise.resolve({
        orgId,
        authSteps: [{ order: 1, allowedMethods: [{ type: 'samlSso' }] }],
      })) as unknown as typeof OrgAuthConfig.findOne);

    const logger = {
      debug: sinon.stub(),
      info: sinon.stub(),
      warn: sinon.stub(),
      error: sinon.stub(),
    } as unknown as Logger;
    const config = {
      jwtSecret: JWT_SECRET,
      scopedJwtSecret: SCOPED_SECRET,
      cmBackend: 'http://cm',
      frontendUrl: 'http://app',
    };
    const iamService = { getUserByEmail: sinon.stub().resolves({ statusCode: 404, data: null }) };
    const configService = {
      getConfig: sinon.stub().resolves({ data: { entryPoint: 'https://idp.test/sso' } }),
    };
    const controller = new UserAccountController(
      ...([config, iamService, {}, sessionService, configService, logger, {}] as unknown as ConstructorParameters<
        typeof UserAccountController
      >),
    );

    const container = new Container();
    container.bind('Logger').toConstantValue(logger);
    container.bind('AppConfig').toConstantValue(config);
    container.bind('SessionService').toConstantValue(sessionService);
    container.bind('UserAccountController').toConstantValue(controller);
    container
      .bind('AuthMiddleware')
      .toConstantValue(new AuthMiddleware(logger, new AuthTokenService(JWT_SECRET, SCOPED_SECRET)));

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
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  });

  it('offers password for a policy saved with only SAML, and refuses a SAML attempt with a 400', async () => {
    const started = await post('/userAccount/initAuth', { email: 'alice@acme.test' });
    const sessionToken = started.headers.get('x-session-token');
    expect(started.status).to.equal(200);
    expect(sessionToken).to.be.a('string');
    expect((started.body as { allowedMethods?: string[] }).allowedMethods).to.deep.equal(['password']);

    const res = await post(
      '/userAccount/authenticate',
      { method: 'samlSso', credentials: {}, email: 'alice@acme.test' },
      { 'x-session-token': String(sessionToken) },
    );

    expect(res.status).to.equal(400);
    expect((res.body as { error?: { message?: string } }).error?.message).to.equal(
      SIGN_IN_METHOD_NOT_ALLOWED,
    );
  });

  it('lets the sign-in page load many times without spending the sign-in attempts', async () => {
    // The default limit is 10 a minute; a page load starts a sign-in each time.
    for (let i = 0; i < 12; i += 1) {
      expect((await post('/userAccount/initAuth', {})).status, `page load ${i + 1}`).to.equal(200);
    }
    const started = await post('/userAccount/initAuth', { email: 'alice@acme.test' });

    const res = await post(
      '/userAccount/authenticate',
      { method: 'samlSso', credentials: {}, email: 'alice@acme.test' },
      { 'x-session-token': String(started.headers.get('x-session-token')) },
    );

    expect(res.status).to.equal(400);
  });

  it('still refuses an 11th sign-in attempt in a minute', async () => {
    const started = await post('/userAccount/initAuth', { email: 'alice@acme.test' });
    const attempt = () =>
      post(
        '/userAccount/authenticate',
        { method: 'samlSso', credentials: {}, email: 'alice@acme.test' },
        { 'x-session-token': String(started.headers.get('x-session-token')) },
      );
    for (let i = 0; i < 10; i += 1) {
      expect((await attempt()).status, `attempt ${i + 1}`).to.equal(400);
    }

    expect((await attempt()).status).to.equal(429);
  });

  it('still limits how fast sign-ins can be started', async () => {
    const statuses: number[] = [];
    for (let i = 0; i < 61; i += 1) {
      statuses.push((await post('/userAccount/initAuth', {})).status);
    }

    expect(statuses.slice(0, 60).every((s) => s === 200)).to.equal(true);
    expect(statuses[60]).to.equal(429);
  });
});
