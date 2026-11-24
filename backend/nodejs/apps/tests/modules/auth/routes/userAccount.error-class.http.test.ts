import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import bcrypt from 'bcryptjs';
import jwt from 'jsonwebtoken';
import winston from 'winston';
import type { AddressInfo } from 'net';
import type { Server } from 'http';
import mongoose from 'mongoose';
import { Container } from 'inversify';
import { createUserAccountRouter } from '../../../../src/modules/auth/routes/userAccount.routes';
import {
  OTP_ALREADY_USED,
  UserAccountController,
  WRONG_SIGN_IN_CODE,
} from '../../../../src/modules/auth/controller/userAccount.controller';
import { SessionService } from '../../../../src/modules/auth/services/session.service';
import { JitProvisioningService } from '../../../../src/modules/auth/services/jit-provisioning.service';
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware';
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service';
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware';
import { requestContextMiddleware } from '../../../../src/libs/middlewares/request.context';
import { accessLog } from '../../../../src/libs/middlewares/access-log.middleware';
import { Logger } from '../../../../src/libs/services/logger.service';
import { MAX_WRONG_CREDENTIAL_ATTEMPTS } from '../../../../src/libs/repositories/credential/types';
import * as credentialRepo from '../../../../src/libs/repositories/credential';
import * as activityRepo from '../../../../src/libs/repositories/activity';
import * as orgRepo from '../../../../src/libs/repositories/org';
import * as orgAuthRepo from '../../../../src/libs/repositories/org-auth-config';
import * as userRepo from '../../../../src/libs/repositories/user';
import * as turnstile from '../../../../src/libs/utils/turnstile-verification';
import { generateAuthToken } from '../../../../src/modules/auth/utils/generateAuthToken';
import type { ICacheService } from '../../../../src/libs/services/cache/cacheService.interface';
import { createMockRedisService } from '../../../helpers/mock-redis';
import { useCommunitySignInLists } from '../../../helpers/sign-in-lists';

// F32: the error_class a refused credential gets on http.request.error and
// http.request.end. Serves the real sign-in router the way app.ts mounts it
// (request context, access line, JSON body, router, error middleware) with the
// real controller, auth middleware and Logger; only repositories, IAM, mail and
// the session cache are faked. A credential the request submitted (a code, a
// password, a CAPTCHA) is bad_request, even when expired or already used; the
// request's own session or token, missing or no longer valid, stays auth_expired.

const MESSAGE = Symbol.for('message');
const JWT_SECRET = 'f32-jwt-secret';
const SCOPED_SECRET = 'f32-scoped-secret';
const PASSWORD = 'Correct-Horse-9!';
const orgId = new mongoose.Types.ObjectId().toHexString();
const alice = {
  _id: new mongoose.Types.ObjectId().toHexString(),
  orgId,
  email: 'alice@acme.test',
  fullName: 'Alice Liddell',
  role: 'member',
  hasLoggedIn: true,
};

interface Cred {
  userId: string;
  orgId: string;
  hashedPassword?: string;
  hashedOTP?: string;
  otpValidity?: number;
  isBlocked: boolean;
  wrongCredentialCount: number;
  blockExpiresAt: Date | null;
}
type Record_ = Record<string, any>;

describe('userAccount routes: error_class of a 401 (F32)', function () {
  this.timeout(20000);
  useCommunitySignInLists();

  let server: Server;
  let baseUrl: string;
  let captured: Record_[];
  let cred: Cred;
  let sentOtps: string[];
  let savedLevel: string;
  let savedTurnstile: string | undefined;
  let savedMiddlewareLogger: unknown;

  const root = () => (Logger.getInstance() as any).logger as winston.Logger;

  async function call(path: string, body: unknown, headers: Record<string, string> = {}) {
    const res = await fetch(`${baseUrl}/api/v1/userAccount${path}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', ...headers },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    });
    const text = await res.text();
    // The OTP request answers plain text; everything else answers JSON.
    const json = res.headers.get('content-type')?.includes('application/json');
    return { status: res.status, headers: res.headers, body: json ? JSON.parse(text) : text };
  }

  /** The access line is written on 'close', after the client has the answer. */
  async function linesFor(): Promise<{ error: Record_; end: Record_ }> {
    for (let i = 0; i < 100 && !captured.some((r) => r.message === 'http.request.end'); i += 1) {
      await new Promise((r) => setImmediate(r));
    }
    const error = captured.filter((r) => r.message === 'http.request.error');
    const end = captured.filter((r) => r.message === 'http.request.end');
    expect(error, 'one http.request.error line').to.have.length(1);
    expect(end, 'one http.request.end line').to.have.length(1);
    return { error: error[0]!, end: end[0]! };
  }

  /** A record as the console transport prints it. */
  function render(r: Record_): string {
    const consoleFormat = root().transports.find((t) => t instanceof winston.transports.Console)!.format!;
    const shaped = root().format.transform({ ...r } as winston.Logform.TransformableInfo, {});
    return String((consoleFormat.transform(shaped as winston.Logform.TransformableInfo, {}) as Record<symbol, unknown>)[MESSAGE]);
  }

  async function signInSession(): Promise<string> {
    const r = await call('/initAuth', { email: alice.email });
    expect(r.status, JSON.stringify(r.body)).to.equal(200);
    return String(r.headers.get('x-session-token'));
  }


  async function changePassword(headers: Record<string, string>, body: Record_ = {}) {
    captured.length = 0;
    return call('/password/reset', { currentPassword: 'Not-It-1!', newPassword: 'Brand-New-Pass-7?', ...body }, headers);
  }

  const bearer = async () => ({ authorization: `Bearer ${await generateAuthToken(alice, JWT_SECRET)}` });

  beforeEach(async () => {
    captured = [];
    sentOtps = [];
    savedLevel = root().level;
    savedTurnstile = process.env.TURNSTILE_SECRET_KEY;
    delete process.env.TURNSTILE_SECRET_KEY;
    root().level = 'debug';
    sinon.stub(root(), 'log').callsFake(((info: Record_) => {
      captured.push(info);
      return root();
    }) as any);
    // ErrorMiddleware keeps the logger it got at load; an earlier file may have reset Logger since.
    savedMiddlewareLogger = (ErrorMiddleware as any).logger;
    (ErrorMiddleware as any).logger = Logger.getInstance({ service: 'ErrorMiddleware' });

    cred = {
      userId: alice._id,
      orgId,
      hashedPassword: await bcrypt.hash(PASSWORD, 4),
      isBlocked: false,
      wrongCredentialCount: 0,
      blockExpiresAt: null,
    };
    const mine = (userId: string) => (userId === alice._id ? cred : null);
    sinon.stub(credentialRepo, 'getCredentialRepository').returns({
      find: async (userId: string) => (mine(userId) ? { ...cred } : null),
      clearBlock: async () => Object.assign(cred, { isBlocked: false, wrongCredentialCount: 0, blockExpiresAt: null }),
      setBlocked: async (_u: string, _o: string, until: Date) => Object.assign(cred, { isBlocked: true, blockExpiresAt: until }),
      reserveAttempt: async (userId: string, _o: string, max: number) => {
        if (!mine(userId) || cred.isBlocked || cred.wrongCredentialCount >= max) return null;
        cred.wrongCredentialCount += 1;
        return { ...cred };
      },
      releaseAttempt: async () => undefined,
      claimOtp: async () => false,
      saveOtp: async (_u: string, _o: string, f: { hashedOTP: string; otpValidity: number }) => Object.assign(cred, f),
      savePassword: async () => undefined,
    } as any);
    sinon.stub(activityRepo, 'getActivityRepository').returns({
      record: async () => undefined,
      latestSessionEnding: async () => null,
      latestPasswordChange: async () => null,
    } as any);
    sinon.stub(orgRepo, 'getOrgRepository').returns({
      getFirstLive: async () => ({ _id: orgId, shortName: 'Acme' }),
      getLive: async () => ({ _id: orgId, shortName: 'Acme' }),
    } as any);
    sinon.stub(orgAuthRepo, 'getOrgAuthConfigRepository').returns({
      getLive: async () => ({ orgId, authSteps: [{ order: 1, allowedMethods: [{ type: 'password' }, { type: 'otp' }] }] }),
    } as any);
    sinon.stub(userRepo, 'getUserRepository').returns({
      getAccountState: async (id: string) => (id === alice._id ? { kind: 'user', isDisabled: false } : null),
      getLiveById: async (id: string) => (id === alice._id ? { ...alice, kind: 'user', isDisabled: false } : null),
      getLive: async (_o: string, id: string) => (id === alice._id ? { ...alice } : null),
      hasRemovedAccount: async () => false,
    } as any);

    const store = new Map<string, string>();
    const redis = createMockRedisService();
    redis.set.callsFake(async (k: string, v: unknown) => { store.set(k, JSON.stringify(v)); });
    redis.get.callsFake(async (k: string) => (store.has(k) ? JSON.parse(store.get(k)!) : null));
    redis.delete.callsFake(async (k: string) => { store.delete(k); });
    const sessionService = new SessionService(redis as unknown as ICacheService);

    const iamService = {
      getUserByEmail: async (email: string) =>
        email.toLowerCase() === alice.email ? { statusCode: 200, data: { ...alice } } : { statusCode: 404, data: {} },
      getUserById: async (id: string) => (id === alice._id ? { statusCode: 200, data: { ...alice } } : { statusCode: 404, data: {} }),
    };
    const mailService = {
      sendMail: async (m: { templateData?: { otp?: string } }) => {
        if (m.templateData?.otp) sentOtps.push(m.templateData.otp);
        return { statusCode: 200, data: 'sent' };
      },
    };
    const eventProducer = { start: async () => undefined, stop: async () => undefined, publishEvent: async () => undefined };
    const config = {
      jwtSecret: JWT_SECRET,
      scopedJwtSecret: SCOPED_SECRET,
      cmBackend: 'http://cm',
      frontendUrl: 'http://app',
      maxAuthRequestsPerMinute: 10000,
      rsAvailable: 'false',
    };
    const logger = Logger.getInstance({ service: 'UserAccountController' });
    const controller = new UserAccountController(
      ...([config, iamService, mailService, sessionService, { getConfig: async () => ({ data: {} }) }, logger,
        new JitProvisioningService(logger, eventProducer as any), eventProducer] as unknown as ConstructorParameters<
        typeof UserAccountController
      >),
    );

    const container = new Container();
    container.bind('Logger').toConstantValue(logger);
    container.bind('AppConfig').toConstantValue(config);
    container.bind('SessionService').toConstantValue(sessionService);
    container.bind('UserAccountController').toConstantValue(controller);
    container.bind('AuthMiddleware').toConstantValue(new AuthMiddleware(logger, new AuthTokenService(JWT_SECRET, SCOPED_SECRET)));
    container.bind('RedisService').toConstantValue({ increment: async () => 1 });

    const app = express();
    app.use(requestContextMiddleware);
    app.use(accessLog());
    app.use(express.json());
    app.use('/api/v1/userAccount', createUserAccountRouter(container));
    app.use(ErrorMiddleware.handleError());
    server = await new Promise<Server>((resolve) => {
      const s = app.listen(0, '127.0.0.1', () => resolve(s));
    });
    baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });

  afterEach(async () => {
    sinon.restore();
    (ErrorMiddleware as any).logger = savedMiddlewareLogger;
    root().level = savedLevel;
    if (savedTurnstile === undefined) delete process.env.TURNSTILE_SECRET_KEY;
    else process.env.TURNSTILE_SECRET_KEY = savedTurnstile;
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  });

  /** The two lines of a refused credential: bad_request, typed WrongCredentialError, at WARN. */
  async function expectWrongCredential(): Promise<void> {
    const { error, end } = await linesFor();
    expect(error).to.include({
      level: 'warning',
      status: 401,
      error_class: 'bad_request',
      error_type: 'WrongCredentialError',
    });
    expect(end).to.include({ status: 401, error_class: 'bad_request', outcome: 'failure' });
  }

  /** A sign-in code sent to alice, and a sign-in session to submit it in. */
  async function otpSession(): Promise<string> {
    expect((await call('/login/otp/generate', { email: alice.email })).status).to.equal(200);
    return signInSession();
  }

  async function submitOtp(token: string, otp: string) {
    captured.length = 0;
    return call('/authenticate', { method: 'otp', credentials: { otp } }, { 'x-session-token': token });
  }

  const wrongOtp = async () => submitOtp(await otpSession(), '000000');

  function failCaptcha(): void {
    process.env.TURNSTILE_SECRET_KEY = 'f32-turnstile';
    sinon.stub(turnstile, 'verifyTurnstileToken').resolves(false);
  }

  describe('a credential the request submitted is bad_request', () => {
    it('wrong sign-in code', async () => {
      const r = await wrongOtp();
      expect(r.status).to.equal(401);
      await expectWrongCredential();
    });

    it('the wrong sign-in code that locks the account', async () => {
      cred.wrongCredentialCount = MAX_WRONG_CREDENTIAL_ATTEMPTS - 1;
      const r = await wrongOtp();
      expect(r.status).to.equal(401);
      expect(cred.isBlocked, 'the account is locked').to.equal(true);
      await expectWrongCredential();
    });

    it('a sign-in code for a locked account (answered as a wrong code)', async () => {
      Object.assign(cred, { isBlocked: true, blockExpiresAt: new Date(Date.now() + 3600_000) });
      const r = await submitOtp(await signInSession(), '000000');
      expect(r.status).to.equal(401);
      await expectWrongCredential();
    });

    it('an expired sign-in code', async () => {
      const token = await otpSession();
      cred.otpValidity = Date.now() - 1000;
      const r = await submitOtp(token, sentOtps[0]!);
      expect(r.status).to.equal(401);
      expect(r.body.error.message).to.equal(WRONG_SIGN_IN_CODE);
      await expectWrongCredential();
    });

    it('a sign-in code already used', async () => {
      const token = await otpSession();
      const r = await submitOtp(token, sentOtps[0]!);
      expect(r.status).to.equal(401);
      expect(r.body.error.message).to.equal(OTP_ALREADY_USED);
      await expectWrongCredential();
    });

    it('a sign-in code for an unknown email logs the same as a wrong code', async () => {
      const init = await call('/initAuth', { email: 'nobody@acme.test' });
      expect(init.status, JSON.stringify(init.body)).to.equal(200);
      const r = await submitOtp(String(init.headers.get('x-session-token')), '000000');
      expect(r.status, JSON.stringify(r.body)).to.equal(401);
      expect(r.body.error.message).to.equal(WRONG_SIGN_IN_CODE);
      await expectWrongCredential();
    });

    it('wrong current password on a password change', async () => {
      const r = await changePassword(await bearer());
      expect(r.status, JSON.stringify(r.body)).to.equal(401);
      await expectWrongCredential();
    });

    it('a failed CAPTCHA on a password change', async () => {
      failCaptcha();
      const r = await changePassword(await bearer(), { turnstileToken: 'nope' });
      expect(r.status, JSON.stringify(r.body)).to.equal(401);
      await expectWrongCredential();
    });

    it('a failed CAPTCHA on a forgotten-password request', async () => {
      failCaptcha();
      captured.length = 0;
      const r = await call('/password/forgot', { email: alice.email, 'cf-turnstile-response': 'nope' });
      expect(r.status, JSON.stringify(r.body)).to.equal(401);
      await expectWrongCredential();
    });

    it('a failed CAPTCHA on a password sign-in', async () => {
      const token = await signInSession();
      failCaptcha();
      captured.length = 0;
      const r = await call(
        '/authenticate',
        { method: 'password', credentials: { password: PASSWORD }, 'cf-turnstile-response': 'nope' },
        { 'x-session-token': token },
      );
      expect(r.status, JSON.stringify(r.body)).to.equal(401);
      await expectWrongCredential();
    });
  });

  describe('[guard] what must not change', () => {
    it('the client still gets the same 401 body for a wrong code', async () => {
      const r = await wrongOtp();
      expect(r.status).to.equal(401);
      expect(r.body).to.have.all.keys('error');
      expect(r.body.error).to.have.all.keys('code', 'message', 'requestId');
      expect(r.body.error).to.include({ code: 'HTTP_UNAUTHORIZED', message: WRONG_SIGN_IN_CODE });
      expect(JSON.stringify(r.body)).to.not.include('error_class');
    });

    it('an expired session token stays auth_expired', async () => {
      const { authorization } = await bearer();
      const claims = jwt.decode(authorization.slice('Bearer '.length)) as Record_;
      const expired = jwt.sign({ ...claims, iat: claims.iat - 7200, exp: Math.floor(Date.now() / 1000) - 60 }, JWT_SECRET);
      const r = await changePassword({ authorization: `Bearer ${expired}` });
      expect(r.status).to.equal(401);
      const { error, end } = await linesFor();
      expect(error).to.include({ level: 'warning', error_class: 'auth_expired', error_type: 'UnauthorizedError' });
      expect(render(error)).to.include('"name": "TokenExpiredError"');
      expect(end).to.include({ error_class: 'auth_expired' });
    });

    it('a request with no token stays auth_expired', async () => {
      const r = await changePassword({});
      expect(r.status).to.equal(401);
      const { error, end } = await linesFor();
      expect(error).to.include({ error_class: 'auth_expired', error_type: 'UnauthorizedError' });
      expect(end).to.include({ error_class: 'auth_expired' });
    });

    it('a wrong password at sign-in stays a 400 bad_request', async () => {
      const token = await signInSession();
      captured.length = 0;
      const r = await call('/authenticate', { method: 'password', credentials: { password: 'Wrong-Pass-1!' } }, { 'x-session-token': token });
      expect(r.status).to.equal(400);
      const { error, end } = await linesFor();
      expect(error).to.include({ level: 'warning', error_class: 'bad_request', error_type: 'BadRequestError' });
      expect(end).to.include({ error_class: 'bad_request' });
    });
  });
});
