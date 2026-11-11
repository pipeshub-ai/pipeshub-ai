/**
 * Auth audit lines over HTTP: one `audit` record per security decision, with
 * the account id, the org, the method and a reason, and no email, name,
 * password, code or token in any record.
 *
 * Served the way app.ts mounts the sign-in router (request context, access
 * line, JSON body, router, error middleware) with the real controller,
 * SessionService, JitProvisioningService, AuthMiddleware and Logger. Only the
 * outermost I/O is faked: repositories in memory, the mail / config services,
 * Google's token check and Microsoft's key endpoints; IAM is a fake or the real
 * IamService behind nock. Every winston record is captured.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import bcrypt from 'bcryptjs';
import jwt from 'jsonwebtoken';
import axios from 'axios';
import nock from 'nock';
import winston from 'winston';
import { generateKeyPairSync, KeyObject } from 'crypto';
import type { AddressInfo } from 'net';
import type { Server } from 'http';
import mongoose from 'mongoose';
import { Container } from 'inversify';
import { LoginTicket } from 'google-auth-library';
import { createUserAccountRouter } from '../../../src/modules/auth/routes/userAccount.routes';
import { createOrgAuthConfigRouter } from '../../../src/modules/auth/routes/orgAuthConfig.routes';
import * as userAdmin from '../../../src/modules/user_management/services/user-admin.service';
import { UserAccountController } from '../../../src/modules/auth/controller/userAccount.controller';
import { SessionService } from '../../../src/modules/auth/services/session.service';
import { JitProvisioningService } from '../../../src/modules/auth/services/jit-provisioning.service';
import { IamService } from '../../../src/modules/auth/services/iam.service';
import {
  GOOGLE_AUTH_CONFIG_PATH,
  MICROSOFT_AUTH_CONFIG_PATH,
} from '../../../src/modules/auth/services/cm.service';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { AuthTokenService } from '../../../src/libs/services/authtoken.service';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { requestContextMiddleware } from '../../../src/libs/middlewares/request.context';
import { accessLog } from '../../../src/libs/middlewares/access-log.middleware';
import { Logger } from '../../../src/libs/services/logger.service';
import { MAX_WRONG_CREDENTIAL_ATTEMPTS } from '../../../src/libs/repositories/credential/types';
import * as credentialRepo from '../../../src/libs/repositories/credential';
import * as activityRepo from '../../../src/libs/repositories/activity';
import * as orgRepo from '../../../src/libs/repositories/org';
import * as orgAuthRepo from '../../../src/libs/repositories/org-auth-config';
import * as userRepo from '../../../src/libs/repositories/user';
import * as groupRepo from '../../../src/libs/repositories/group';
import * as resetLinkRepo from '../../../src/libs/repositories/reset-link';
import * as turnstile from '../../../src/libs/utils/turnstile-verification';
import { generateAuthToken } from '../../../src/modules/auth/utils/generateAuthToken';
import {
  fetchConfigJwtGenerator,
  jwtGeneratorForForgotPasswordLink,
  jwtGeneratorForValidateEmailLink,
  refreshTokenJwtGenerator,
} from '../../../src/libs/utils/createJwt';
import { deriveUserActionSecret } from '../../../src/libs/utils/jwtKeys';
import { TokenScopes } from '../../../src/libs/enums/token-scopes.enum';
import type { ICacheService } from '../../../src/libs/services/cache/cacheService.interface';
import { createMockRedisService } from '../../helpers/mock-redis';
import { useCommunitySignInLists } from '../../helpers/sign-in-lists';
import { stubGoogleIdTokenCheck } from '../../helpers/google-id-token';

const JWT_SECRET = 'sign-in-audit-jwt-secret';
const SCOPED_SECRET = 'sign-in-audit-scoped-secret';
const IAM_BACKEND = 'http://iam.sign-in-audit.test';
const PASSWORD = 'Correct-Horse-9!';
const NEW_PASSWORD = 'Brand-New-Pass-7?';
const GOOGLE_TOKEN = 'google-id-token-SECRET';
const MS_CLIENT = 'ms-client-id';
const OUR_TENANT = '11111111-1111-1111-1111-111111111111';
const OTHER_TENANT = '22222222-2222-2222-2222-222222222222';
const KID = 'k1';
const orgId = new mongoose.Types.ObjectId().toHexString();
/** Every request this file makes carries a request id with this prefix; only those lines are captured. */
const OUR_REQUESTS = 'n4atest';
let requestSeq = 0;

interface Person { _id: string; orgId: string; email: string; fullName: string; role: string; hasLoggedIn: boolean }
const person = (fullName: string, email: string): Person => ({
  _id: new mongoose.Types.ObjectId().toHexString(), orgId, email, fullName, role: 'member', hasLoggedIn: true,
});
const alice = person('Alice Liddell', 'alice@acme.test');
const bob = person('Bob Builder', 'bob@acme.test');
const UNKNOWN = 'nobody@acme.test';
const NEWCOMER = 'newcomer@acme.test';
const NEW_ADDRESS = 'alice.new@acme.test';
const PRIVATE = [alice.email, bob.email, UNKNOWN, NEWCOMER, NEW_ADDRESS, alice.fullName, bob.fullName, 'Nina Newcomer', PASSWORD, NEW_PASSWORD, GOOGLE_TOKEN];

const { privateKey, publicKey } = generateKeyPairSync('rsa', { modulusLength: 2048 });
const { privateKey: otherKey } = generateKeyPairSync('rsa', { modulusLength: 2048 });
const msJwk = { ...(publicKey as KeyObject).export({ format: 'jwk' }), kid: KID };
const msIssuer = (tenant: string) => `https://login.microsoftonline.com/${tenant}/v2.0`;
const msToken = (claims: Record<string, unknown>, options: jwt.SignOptions = {}, key = privateKey) =>
  jwt.sign({ tid: OUR_TENANT, iss: msIssuer(OUR_TENANT), aud: MS_CLIENT, ...claims }, key, {
    algorithm: 'RS256', keyid: KID, expiresIn: '5m', ...options,
  });

interface Cred {
  userId: string; orgId: string; hashedPassword?: string; hashedOTP?: string; otpValidity?: number;
  isBlocked: boolean; wrongCredentialCount: number; blockExpiresAt: Date | null;
}
type Rec = Record<string, any>;

describe('auth audit lines (HTTP)', function () {
  this.timeout(20000);
  useCommunitySignInLists();

  let server: Server;
  let baseUrl: string;
  let records: Rec[];
  let directory: Record<string, Person>;
  let creds: Record<string, Cred>;
  let steps: string[][];
  let jit: boolean;
  let removedAccount: boolean;
  let claimOtpFails: boolean;
  let resetLinkClaimed: boolean;
  let accountState: { kind: string; isDisabled: boolean };
  let lastPasswordChange: Date | null;
  let sessionEnding: { activityType: string; createdAt: Date } | null;
  let emailTaken: boolean;
  let mailFails: boolean;
  let configFails: boolean;
  let otpCodes: string[];
  let iam: Record<string, (...args: any[]) => Promise<any>>;
  let savedLevel: string;
  let maxAuthRequestsPerMinute: number;

  const root = () => (Logger.getInstance() as any).logger as winston.Logger;
  const audits = () =>
    records
      .filter((r) => r.service === 'audit')
      .map((r) => {
        const out: Rec = { event: r.message, outcome: r.outcome };
        for (const key of ['reason', 'actor', 'target', 'auth_method']) if (r[key] != null) out[key] = r[key];
        return out;
      });
  const auditRecords = () => records.filter((r) => r.service === 'audit');
  const failureLines = () => records.filter((r) => ['warning', 'error'].includes(r.level) && r.message !== 'http.request.end');
  const expectNoPrivateData = (allowed: (r: Rec) => boolean = () => false) => {
    for (const r of records) {
      if (allowed(r)) continue;
      const raw = JSON.stringify(r);
      for (const secret of [...PRIVATE, ...otpCodes]) expect(raw, `${r.message} leaks ${secret}`).to.not.include(secret);
    }
  };
  /** Background work writes after the reply: wait for the record, bounded, without sleeping a fixed time. */
  const waitForRecord = async (pred: (r: Rec) => boolean, timeoutMs = 2000): Promise<Rec> => {
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      const found = records.find(pred);
      if (found) return found;
      if (Date.now() > deadline) expect.fail(`no such record; saw ${records.map((r) => r.message).join(', ')}`);
      await new Promise((resolve) => setImmediate(resolve));
    }
  };

  async function call(method: 'POST' | 'PUT' | 'GET', path: string, body: unknown = {}, headers: Record<string, string> = {}, mount = '/api/v1/userAccount') {
    const requestId = `${OUR_REQUESTS}-${++requestSeq}`;
    const res = await fetch(`${baseUrl}${mount}${path}`, {
      method,
      headers: { 'content-type': 'application/json', 'x-request-id': requestId, ...headers },
      body: method === 'GET' ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    });
    const text = await res.text();
    let parsed: any = text;
    try { parsed = text ? JSON.parse(text) : undefined; } catch { /* plain text */ }
    // The access line is written on 'close'; wait for this request's own line (not an earlier call to the same route).
    await waitForRecord((r) => r.message === 'http.request.end' && r.request_id === requestId);
    return { status: res.status, headers: res.headers, body: parsed, text };
  }
  async function initAuth(email?: string): Promise<string> {
    const r = await call('POST', '/initAuth', email === undefined ? {} : { email });
    expect(r.status).to.equal(200);
    return String(r.headers.get('x-session-token'));
  }
  /** Start a sign-in, then keep only the records of the attempt itself. */
  async function attempt(body: Rec, email?: string, token?: string) {
    const session = token ?? (await initAuth(email));
    records.length = 0;
    return call('POST', '/authenticate', body, { 'x-session-token': session });
  }
  const password = (pw = PASSWORD, email = alice.email) => attempt({ method: 'password', credentials: { password: pw } }, email);
  const givePassword = async (p: Person) => {
    creds[p._id] = { userId: p._id, orgId, hashedPassword: await bcrypt.hash(PASSWORD, 4), isBlocked: false, wrongCredentialCount: 0, blockExpiresAt: null };
  };
  const giveOtp = async (p: Person, code: string, validFor = 60_000) => {
    creds[p._id] = { ...(creds[p._id] ?? { userId: p._id, orgId, isBlocked: false, wrongCredentialCount: 0, blockExpiresAt: null }), hashedOTP: await bcrypt.hash(code, 4), otpValidity: Date.now() + validFor };
  };
  const lock = (p: Person) => Object.assign(creds[p._id]!, { isBlocked: true, blockExpiresAt: new Date(Date.now() + 3_600_000) });
  const accessToken = (p: Person) => generateAuthToken(p, JWT_SECRET);
  const userActionToken = (claims: Rec, options: jwt.SignOptions = {}) =>
    jwt.sign(claims, deriveUserActionSecret(SCOPED_SECRET), { expiresIn: '10m', ...options });
  const serveMicrosoft = (reachable = true) =>
    sinon.stub(axios, 'get').callsFake(async (url: string) => {
      if (!reachable) throw Object.assign(new Error('connect ECONNREFUSED login.microsoftonline.com'), { code: 'ECONNREFUSED' });
      if (url.endsWith('/.well-known/openid-configuration')) return { data: { issuer: msIssuer(OUR_TENANT), jwks_uri: 'https://keys.example/keys' } } as any;
      return { data: { keys: [msJwk] } } as any;
    });
  const useRealIam = () => {
    const real = new IamService(...([{ iamBackend: IAM_BACKEND }] as unknown as ConstructorParameters<typeof IamService>));
    iam = {
      getUserByEmail: real.getUserByEmail.bind(real),
      getUserById: real.getUserById.bind(real),
      updateUser: real.updateUser.bind(real),
      checkAdminUser: real.checkAdminUser.bind(real),
    };
  };

  async function startApp() {
    const store = new Map<string, string>();
    const redis = createMockRedisService();
    redis.set.callsFake(async (k: string, v: unknown) => { store.set(k, JSON.stringify(v)); });
    redis.get.callsFake(async (k: string) => (store.has(k) ? JSON.parse(store.get(k)!) : null));
    redis.delete.callsFake(async (k: string) => { store.delete(k); });
    const sessionService = new SessionService(redis as unknown as ICacheService);
    const iamProxy = new Proxy({}, { get: (_t, name: string) => (...args: unknown[]) => iam[name]!(...args) });
    const mailService = {
      sendMail: async (m: { templateData?: { otp?: string } }) => {
        if (m.templateData?.otp) otpCodes.push(m.templateData.otp);
        if (mailFails) throw new Error('mail service down');
        return { statusCode: 200, data: 'sent' };
      },
    };
    const configService = {
      getConfig: async (_backend: string, path: string) => {
        if (configFails) throw new Error('config service down');
        return path === MICROSOFT_AUTH_CONFIG_PATH
          ? { data: { clientId: MS_CLIENT, tenantId: OUR_TENANT, enableJit: jit } }
          : path === GOOGLE_AUTH_CONFIG_PATH
            ? { data: { clientId: 'google-client', enableJit: jit } }
            : { data: {} };
      },
    };
    const events = { start: async () => undefined, stop: async () => undefined, publishEvent: async () => undefined };
    const config = { jwtSecret: JWT_SECRET, scopedJwtSecret: SCOPED_SECRET, cmBackend: 'http://cm', connectorBackend: 'http://connector.test', frontendUrl: 'http://app', iamBackend: IAM_BACKEND, maxAuthRequestsPerMinute, rsAvailable: 'false' };
    const logger = Logger.getInstance({ service: 'UserAccountController' });
    const controller = new UserAccountController(
      ...([config, iamProxy, mailService, sessionService, configService, logger, new JitProvisioningService(logger, events as any), events] as unknown as ConstructorParameters<typeof UserAccountController>),
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
    app.use('/api/v1/orgAuthConfig', createOrgAuthConfigRouter(container));
    app.use(ErrorMiddleware.handleError());
    server = await new Promise<Server>((resolve) => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
    baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  }

  beforeEach(async () => {
    records = [];
    otpCodes = [];
    steps = [['password', 'otp', 'google', 'microsoft']];
    jit = false;
    removedAccount = false;
    claimOtpFails = false;
    resetLinkClaimed = true;
    accountState = { kind: 'user', isDisabled: false };
    lastPasswordChange = null;
    sessionEnding = null;
    emailTaken = false;
    mailFails = false;
    configFails = false;
    maxAuthRequestsPerMinute = 10000;
    delete process.env.TURNSTILE_SECRET_KEY;
    savedLevel = root().level;
    root().level = 'debug';
    // Every winston root, not just the current one: modules keep the logger they got at load, and other test files
    // replace the root, so a per-instance stub misses lines depending on test order. Only this test's requests
    // count: other work in the same mocha worker (timers, sweeps, reconnects) logs meanwhile.
    sinon.stub(winston.Logger.prototype, 'log').callsFake(function (this: unknown, info: Record<string, any>) {
      if (String(info.request_id).startsWith(OUR_REQUESTS)) records.push(info);
      return this;
    } as any);

    directory = { [alice.email]: { ...alice }, [bob.email]: { ...bob } };
    creds = {};
    await givePassword(alice);
    await givePassword(bob);
    const byId = (id: string) => Object.values(directory).find((u) => u._id === id) ?? null;
    iam = {
      getUserByEmail: async (email: string) => {
        const u = directory[email.toLowerCase()];
        return u ? { statusCode: 200, data: { ...u } } : { statusCode: 404, data: { message: 'Account not found' } };
      },
      getUserById: async (id: string) => (byId(id) ? { statusCode: 200, data: { ...byId(id) } } : { statusCode: 404, data: {} }),
      updateUser: async () => ({ statusCode: 200, data: {} }),
      checkAdminUser: async () => ({ statusCode: 200, data: {} }),
    };

    sinon.stub(credentialRepo, 'getCredentialRepository').returns({
      find: async (userId: string) => (creds[userId] ? { ...creds[userId] } : null),
      clearBlock: async (userId: string) => { Object.assign(creds[userId]!, { isBlocked: false, wrongCredentialCount: 0, blockExpiresAt: null }); },
      setBlocked: async (userId: string, _o: string, until: Date) => { Object.assign(creds[userId]!, { isBlocked: true, blockExpiresAt: until }); },
      reserveAttempt: async (userId: string, _o: string, max: number) => {
        const c = creds[userId];
        if (!c || c.isBlocked || c.wrongCredentialCount >= max) return null;
        c.wrongCredentialCount += 1;
        return { ...c };
      },
      releaseAttempt: async (userId: string, _o: string, reserved: number) => {
        const c = creds[userId];
        if (c && !c.isBlocked && c.wrongCredentialCount === reserved) c.wrongCredentialCount = 0;
      },
      claimOtp: async (userId: string, _o: string, hashed: string) => {
        const c = creds[userId];
        if (claimOtpFails || !c || c.hashedOTP !== hashed) return false;
        delete c.hashedOTP;
        return true;
      },
      saveOtp: async (userId: string, o: string, f: Rec) => {
        creds[userId] = { ...(creds[userId] ?? { userId, orgId: o, isBlocked: false, wrongCredentialCount: 0, blockExpiresAt: null }), ...f };
      },
      savePassword: async (userId: string, _o: string, hashedPassword: string) => { Object.assign(creds[userId]!, { hashedPassword }); },
      touchOnRefresh: async (userId: string) => (creds[userId] ? { ...creds[userId] } : null),
    } as any);
    sinon.stub(activityRepo, 'getActivityRepository').returns({
      record: async (a: Rec) => { if (a.activityType === 'PASSWORD CHANGED') lastPasswordChange = new Date(Date.now() + 1000); },
      latestSessionEnding: async () => sessionEnding,
      latestPasswordChange: async () => (lastPasswordChange ? { createdAt: lastPasswordChange } : null),
    } as any);
    sinon.stub(orgRepo, 'getOrgRepository').returns({
      getFirstLive: async () => ({ _id: orgId, shortName: 'Acme' }),
      getLive: async () => ({ _id: orgId, shortName: 'Acme' }),
    } as any);
    sinon.stub(orgAuthRepo, 'getOrgAuthConfigRepository').returns({
      getLive: async () => ({ orgId, authSteps: steps.map((types, i) => ({ order: i + 1, allowedMethods: types.map((type) => ({ type })) })) }),
      get: async () => ({ orgId, authSteps: [] }),
      replaceSteps: async () => true,
    } as any);
    sinon.stub(userRepo, 'getUserRepository').returns({
      getAccountState: async (id: string) => (byId(id) ? accountState : null),
      getLiveById: async (id: string) => (byId(id) ? { ...byId(id), kind: 'user', isDisabled: false } : null),
      getLive: async (_o: string, id: string) => byId(id),
      hasRemovedAccount: async () => removedAccount,
      emailTakenElsewhere: async () => emailTaken,
      changeEmail: async (id: string, email: string) => ({ ...byId(id)!, email }),
      create: async (o: string, fields: Rec) => {
        const created = { ...person(String(fields.fullName), String(fields.email)), ...fields, orgId: o } as Person;
        directory[created.email] = created;
        return created;
      },
    } as any);
    sinon.stub(groupRepo, 'getGroupRepository').returns({ addToEveryone: async () => undefined } as any);
    sinon.stub(resetLinkRepo, 'getResetLinkRepository').returns({ claim: async () => resetLinkClaimed, release: async () => undefined } as any);
    // `email|second` tokens: Google verifies the token twice per sign-in (provider flow, then the method step).
    stubGoogleIdTokenCheck().callsFake((async (idToken: string) => {
      // google-auth-library quotes the token it refuses.
      if (idToken.startsWith('eyJ')) throw new Error(`Invalid token signature: ${idToken}`);
      const [, email] = idToken.split('|');
      return { getPayload: () => (email ? { email, name: 'Nina Newcomer' } : { name: 'Nina Newcomer' }) } as unknown as LoginTicket;
    }) as any);
    await startApp();
  });

  afterEach(async () => {
    sinon.restore();
    nock.cleanAll();
    root().level = savedLevel;
    delete process.env.TURNSTILE_SECRET_KEY;
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  });

  const signInFailure = (reason: string, actor: string | undefined, authMethod: string) => ({
    event: 'auth.sign_in', outcome: 'failure', reason, ...(actor && { actor }), auth_method: authMethod,
  });

  describe('sign-in', () => {
    it('T1 a password sign-in is one success line naming the account, org and method; the access line says who too', async () => {
      const r = await password();
      expect(r.status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'auth.sign_in', outcome: 'success', actor: alice._id, auth_method: 'password' }]);
      expect(auditRecords()[0]).to.include({ org_id: orgId, user_id: alice._id });
      expect(records.find((x) => x.message === 'http.request.end')).to.include({ user_id: alice._id, org_id: orgId });
      expectNoPrivateData();
      expect(JSON.stringify(records)).to.not.include(r.body.accessToken).and.not.include(r.body.refreshToken);
    });

    // Every refusal site on the sign-in path: exactly one auth.sign_in failure with its reason, and nothing private.
    const refusals: Array<[string, () => Promise<unknown>, ReturnType<typeof signInFailure>]> = [
      ['no password set', async () => { delete creds[alice._id]!.hashedPassword; return password(); }, signInFailure('no_password', alice._id, 'password')],
      ['locked (password)', async () => { lock(alice); return password(); }, signInFailure('locked', alice._id, 'password')],
      ['no attempts left (password reservation)', async () => { creds[alice._id]!.wrongCredentialCount = MAX_WRONG_CREDENTIAL_ATTEMPTS; return password(); }, signInFailure('locked', alice._id, 'password')],
      ['wrong password', () => password('Wrong-Pass-1!'), signInFailure('bad_password', alice._id, 'password')],
      ['no credentials row (otp)', async () => { delete creds[alice._id]; return attempt({ method: 'otp', credentials: { otp: '123456' } }, alice.email); }, signInFailure('no_otp', alice._id, 'otp')],
      ['no code issued (otp)', () => attempt({ method: 'otp', credentials: { otp: '123456' } }, alice.email), signInFailure('no_otp', alice._id, 'otp')],
      ['expired code', async () => { await giveOtp(alice, '482913', -1000); return attempt({ method: 'otp', credentials: { otp: '482913' } }, alice.email); }, signInFailure('otp_expired', alice._id, 'otp')],
      ['locked (otp)', async () => { await giveOtp(alice, '482913'); lock(alice); return attempt({ method: 'otp', credentials: { otp: '482913' } }, alice.email); }, signInFailure('locked', alice._id, 'otp')],
      ['no attempts left (otp reservation)', async () => { await giveOtp(alice, '482913'); creds[alice._id]!.wrongCredentialCount = MAX_WRONG_CREDENTIAL_ATTEMPTS; return attempt({ method: 'otp', credentials: { otp: '482913' } }, alice.email); }, signInFailure('locked', alice._id, 'otp')],
      ['wrong code', async () => { await giveOtp(alice, '482913'); return attempt({ method: 'otp', credentials: { otp: '000000' } }, alice.email); }, signInFailure('bad_otp', alice._id, 'otp')],
      ['code already used', async () => { await giveOtp(alice, '482913'); claimOtpFails = true; return attempt({ method: 'otp', credentials: { otp: '482913' } }, alice.email); }, signInFailure('otp_already_used', alice._id, 'otp')],
      ['unknown email (password)', () => password(PASSWORD, UNKNOWN), signInFailure('unknown_user', undefined, 'password')],
      ['unknown email (otp)', () => attempt({ method: 'otp', credentials: { otp: '123456' } }, UNKNOWN), signInFailure('unknown_user', undefined, 'otp')],
      ['method not allowed at this step', async () => { steps = [['password']]; return attempt({ method: 'otp', credentials: { otp: '1' } }, alice.email); }, signInFailure('method_not_allowed', undefined, 'otp')],
      ['CAPTCHA failed', async () => { process.env.TURNSTILE_SECRET_KEY = 'x'; sinon.stub(turnstile, 'verifyTurnstileToken').resolves(false); return password(); }, signInFailure('captcha_failed', undefined, 'password')],
      ['Google token without an email', () => attempt({ method: 'google', credentials: `${GOOGLE_TOKEN}|` }), signInFailure('sso_no_email', undefined, 'google')],
      ['Google, unknown account, JIT off', () => attempt({ method: 'google', credentials: `${GOOGLE_TOKEN}|${NEWCOMER}` }), signInFailure('no_account', undefined, 'google')],
      ['Google, JIT on, account removed by an admin', async () => { jit = true; removedAccount = true; return attempt({ method: 'google', credentials: `${GOOGLE_TOKEN}|${NEWCOMER}` }); }, signInFailure('account_removed', undefined, 'google')],
      ['Microsoft: no id token', async () => { serveMicrosoft(); return attempt({ method: 'microsoft', credentials: {} }); }, signInFailure('sso_token_invalid', undefined, 'microsoft')],
      ['Microsoft: token that cannot be decoded', async () => { serveMicrosoft(); return attempt({ method: 'microsoft', credentials: { idToken: 'garbage' } }); }, signInFailure('sso_token_invalid', undefined, 'microsoft')],
      ['Microsoft: no key for the token', async () => { serveMicrosoft(); return attempt({ method: 'microsoft', credentials: { idToken: msToken({ email: bob.email }, { keyid: 'other' }) } }); }, signInFailure('sso_token_invalid', undefined, 'microsoft')],
      ['Microsoft: bad signature', async () => { serveMicrosoft(); return attempt({ method: 'microsoft', credentials: { idToken: msToken({ email: bob.email }, {}, otherKey) } }); }, signInFailure('sso_token_invalid', undefined, 'microsoft')],
      ['Microsoft: expired token', async () => { serveMicrosoft(); return attempt({ method: 'microsoft', credentials: { idToken: msToken({ email: bob.email }, { expiresIn: -10 }) } }); }, signInFailure('sso_token_expired', undefined, 'microsoft')],
      ['Microsoft: another tenant', async () => { serveMicrosoft(); return attempt({ method: 'microsoft', credentials: { idToken: msToken({ email: bob.email, tid: OTHER_TENANT, iss: msIssuer(OTHER_TENANT) }) } }); }, signInFailure('sso_wrong_tenant', undefined, 'microsoft')],
      ['Microsoft token without an email', async () => { serveMicrosoft(); return attempt({ method: 'microsoft', credentials: { idToken: msToken({}) } }); }, signInFailure('sso_no_email', undefined, 'microsoft')],
      ['service account', async () => { accountState = { kind: 'service', isDisabled: false }; return password(); }, signInFailure('service_account', alice._id, 'password')],
      ['disabled account', async () => { accountState = { kind: 'user', isDisabled: true }; return password(); }, signInFailure('account_disabled', alice._id, 'password')],
      ['no sign-in session', async () => { records.length = 0; return call('POST', '/authenticate', { method: 'password', credentials: { password: PASSWORD } }); }, signInFailure('no_session', undefined, 'password')],
      ['unknown sign-in session', () => attempt({ method: 'password', credentials: { password: PASSWORD } }, undefined, 'not-a-session'), signInFailure('session_expired', undefined, 'password')],
    ];
    for (const [name, run, expected] of refusals) {
      it(`T2 refusal: ${name} → reason=${expected.reason}`, async () => {
        const r = (await run()) as { status: number };
        expect(r.status).to.be.at.least(400);
        expect(audits()).to.deep.equal([expected]);
        // The line points at the handler or middleware that refused, not at the audit helpers.
        expect(auditRecords()[0]!.filename).to.be.oneOf(['userAccount.controller.ts', 'userAuthentication.middleware.ts']);
        expect(failureLines()).to.have.length(1);
        expectNoPrivateData();
      });
    }

    it('T2 refusal: a second step for another account → reason=account_changed', async () => {
      steps = [['password'], ['google']];
      const token = await initAuth(alice.email);
      await call('POST', '/authenticate', { method: 'password', credentials: { password: PASSWORD } }, { 'x-session-token': token });
      records.length = 0;
      const r = await call('POST', '/authenticate', { method: 'google', credentials: `${GOOGLE_TOKEN}|${bob.email}` }, { 'x-session-token': token });
      expect(r.status).to.equal(401);
      // The session's account is the request's user; the actor is the account the second step proved.
      expect(audits()).to.deep.equal([signInFailure('account_changed', bob._id, 'google')]);
      expect(auditRecords()[0]).to.include({ user_id: alice._id });
    });

    it('T3 the wrong password that locks the account: the lock, then the refusal', async () => {
      creds[alice._id]!.wrongCredentialCount = MAX_WRONG_CREDENTIAL_ATTEMPTS - 1;
      await password('Wrong-Pass-1!');
      expect(audits()).to.deep.equal([
        { event: 'auth.account.lock', outcome: 'success', reason: 'too_many_attempts', actor: alice._id, auth_method: 'password' },
        signInFailure('bad_password', alice._id, 'password'),
      ]);
      expect(failureLines()).to.have.length(1);
      expectNoPrivateData();
    });

    it('T4 the wrong code that locks the account: the lock, then the refusal, and no email', async () => {
      await giveOtp(alice, '482913');
      creds[alice._id]!.wrongCredentialCount = MAX_WRONG_CREDENTIAL_ATTEMPTS - 1;
      await attempt({ method: 'otp', credentials: { otp: '000000' } }, alice.email);
      expect(audits()).to.deep.equal([
        { event: 'auth.account.lock', outcome: 'success', reason: 'too_many_attempts', actor: alice._id, auth_method: 'otp' },
        signInFailure('bad_otp', alice._id, 'otp'),
      ]);
      expectNoPrivateData();
    });

    it('T5 a lock email that cannot be sent is one line with the user id', async () => {
      mailFails = true;
      creds[alice._id]!.wrongCredentialCount = MAX_WRONG_CREDENTIAL_ATTEMPTS - 1;
      await password('Wrong-Pass-1!');
      const line = await waitForRecord((r) => r.message === "The account-locked email couldn't be sent");
      expect(line).to.include({ user_id: alice._id });
      expectNoPrivateData();
    });

    it('T6 a two-step sign-in: the first step is partial, the second a success', async () => {
      steps = [['password'], ['otp']];
      const token = await initAuth(alice.email);
      records.length = 0;
      await call('POST', '/authenticate', { method: 'password', credentials: { password: PASSWORD } }, { 'x-session-token': token });
      await giveOtp(alice, '482913');
      await call('POST', '/authenticate', { method: 'otp', credentials: { otp: '482913' } }, { 'x-session-token': token });
      expect(audits()).to.deep.equal([
        { event: 'auth.sign_in', outcome: 'partial', actor: alice._id, auth_method: 'password' },
        { event: 'auth.sign_in', outcome: 'success', actor: alice._id, auth_method: 'otp' },
      ]);
    });

    it('T6 a first step whose next-step lookup then fails is still one partial line', async () => {
      steps = [['password'], ['google']];
      const token = await initAuth(alice.email);
      configFails = true;
      records.length = 0;
      const r = await call('POST', '/authenticate', { method: 'password', credentials: { password: PASSWORD } }, { 'x-session-token': token });
      expect(r.status).to.equal(500);
      expect(audits()).to.deep.equal([{ event: 'auth.sign_in', outcome: 'partial', actor: alice._id, auth_method: 'password' }]);
    });

    it('T7 Google sign-in success', async () => {
      const r = await attempt({ method: 'google', credentials: `${GOOGLE_TOKEN}|${bob.email}` });
      expect(r.status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'auth.sign_in', outcome: 'success', actor: bob._id, auth_method: 'google' }]);
      expectNoPrivateData();
    });

    it('T8 a token Google rejects is sso_token_invalid, still a 500 (F31), and the token is in no record', async () => {
      const forged = jwt.sign({ email: bob.email }, 'forged-key');
      const r = await attempt({ method: 'google', credentials: forged });
      expect(r.status).to.equal(500);
      expect(audits()).to.deep.equal([signInFailure('sso_token_invalid', undefined, 'google')]);
      for (const part of forged.split('.')) expect(JSON.stringify(records)).to.not.include(part);
    });

    it('T9 JIT: the new account, then its sign-in', async () => {
      jit = true;
      const r = await attempt({ method: 'google', credentials: `${GOOGLE_TOKEN}|${NEWCOMER}` });
      expect(r.status).to.equal(200);
      const created = directory[NEWCOMER]!._id;
      expect(audits()).to.deep.equal([
        { event: 'auth.jit_provision', outcome: 'success', actor: created, auth_method: 'google' },
        { event: 'auth.sign_in', outcome: 'success', actor: created, auth_method: 'google' },
      ]);
      expectNoPrivateData();
    });

    it('T11 Microsoft success; Microsoft unreachable is no audit line and one error line with the cause', async () => {
      const stub = serveMicrosoft();
      const ok = await attempt({ method: 'microsoft', credentials: { idToken: msToken({ email: bob.email }) } });
      expect(ok.status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'auth.sign_in', outcome: 'success', actor: bob._id, auth_method: 'microsoft' }]);
      stub.restore();
      serveMicrosoft(false);
      const down = await attempt({ method: 'microsoft', credentials: { idToken: msToken({ email: bob.email }) } });
      expect(down.status).to.equal(503);
      expect(audits()).to.deep.equal([]);
      const errors = records.filter((r) => r.message === 'http.request.error');
      expect(errors).to.have.length(1);
      expect(JSON.stringify(errors[0]!.metadata)).to.include('ECONNREFUSED');
      expect(records.filter((r) => r.service === 'Azure Ad Token Validation' && r.level !== 'debug')).to.deep.equal([]);
    });

    it('T26 volume: success 2 INFO; wrong password 2 INFO + 1 WARN; lock 3 INFO + 1 WARN; OTP success 2 INFO', async () => {
      const count = () => ({ info: records.filter((r) => r.level === 'info').length, warn: records.filter((r) => r.level === 'warning').length });
      await password();
      expect(count()).to.deep.equal({ info: 2, warn: 0 });
      await password('Wrong-Pass-1!');
      expect(count()).to.deep.equal({ info: 2, warn: 1 });
      creds[alice._id]!.wrongCredentialCount = MAX_WRONG_CREDENTIAL_ATTEMPTS - 1;
      await password('Wrong-Pass-1!');
      expect(count()).to.deep.equal({ info: 3, warn: 1 });
      await giveOtp(bob, '482913');
      await attempt({ method: 'otp', credentials: { otp: '482913' } }, bob.email);
      expect(count()).to.deep.equal({ info: 2, warn: 0 });
    });

    it('T24 an expired lock: the unlock, then the sign-in', async () => {
      Object.assign(creds[alice._id]!, { isBlocked: true, blockExpiresAt: new Date(Date.now() - 1000) });
      await password();
      expect(audits()).to.deep.equal([
        { event: 'auth.account.unlock', outcome: 'success', reason: 'block_expired', target: alice._id },
        { event: 'auth.sign_in', outcome: 'success', actor: alice._id, auth_method: 'password' },
      ]);
    });

    it('T25 [guard] the refusal for a wrong password and an unknown email is the same answer', async () => {
      const wrong = await password('Wrong-Pass-1!');
      const unknown = await password(PASSWORD, UNKNOWN);
      const strip = (b: Rec) => ({ ...b, error: { ...b.error, requestId: undefined } });
      expect([wrong.status, strip(wrong.body)]).to.deep.equal([unknown.status, strip(unknown.body)]);
    });

    it('T27 [guard] a body that fails validation is not audited', async () => {
      const r = await attempt({ method: '', credentials: { password: PASSWORD } }, alice.email);
      expect(r.status).to.equal(400);
      expect(audits()).to.deep.equal([]);
      expect(records.filter((x) => x.message === 'http.request.error')).to.have.length(1);
    });

    it('T29 [guard] a rate-limited attempt is not audited', async () => {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
      maxAuthRequestsPerMinute = 1;
      await startApp();
      const token = await initAuth(alice.email);
      await call('POST', '/authenticate', { method: 'password', credentials: { password: 'Wrong-Pass-1!' } }, { 'x-session-token': token });
      records.length = 0;
      let r = { status: 0 };
      for (let i = 0; i < 5 && r.status !== 429; i++) {
        records.length = 0;
        r = await call('POST', '/authenticate', { method: 'password', credentials: { password: 'Wrong-Pass-1!' } }, { 'x-session-token': token });
      }
      expect(r.status).to.equal(429);
      expect(audits()).to.deep.equal([]);
    });

    it('T30 a method that is an email is shown as other, and the email is in no record', async () => {
      steps = [['password']];
      const r = await attempt({ method: alice.email, credentials: { password: PASSWORD } }, bob.email);
      expect(r.status).to.equal(400);
      expect(audits()).to.deep.equal([signInFailure('method_not_allowed', undefined, 'other')]);
      expectNoPrivateData();
    });
  });

  describe('sign-in codes and password reset requests', () => {
    it('T14 OTP request: sent, unknown, locked; a failed send is one line and no audit failure', async () => {
      records.length = 0;
      await call('POST', '/login/otp/generate', { email: alice.email });
      await call('POST', '/login/otp/generate', { email: UNKNOWN });
      lock(bob);
      await call('POST', '/login/otp/generate', { email: bob.email });
      expect(audits()).to.deep.equal([
        { event: 'auth.otp.send', outcome: 'success', actor: alice._id, auth_method: 'otp' },
        { event: 'auth.otp.send', outcome: 'failure', reason: 'unknown_user', auth_method: 'otp' },
        { event: 'auth.otp.send', outcome: 'failure', reason: 'locked', actor: bob._id, auth_method: 'otp' },
      ]);
      mailFails = true;
      records.length = 0;
      await call('POST', '/login/otp/generate', { email: alice.email });
      const line = await waitForRecord((r) => r.message === "The sign-in code email couldn't be sent");
      expect(line).to.include({ user_id: alice._id });
      expect(audits()).to.deep.equal([{ event: 'auth.otp.send', outcome: 'success', actor: alice._id, auth_method: 'otp' }]);
      expectNoPrivateData();
    });

    it('T15 forgot password: sent, unknown, CAPTCHA; a failed lookup is one line and no audit', async () => {
      records.length = 0;
      await call('POST', '/password/forgot', { email: alice.email });
      await waitForRecord((r) => r.message === 'auth.password_reset.request');
      await call('POST', '/password/forgot', { email: UNKNOWN });
      await waitForRecord((r) => r.message === 'auth.password_reset.request' && r.reason === 'unknown_user');
      expect(audits()).to.deep.equal([
        { event: 'auth.password_reset.request', outcome: 'success', actor: alice._id },
        { event: 'auth.password_reset.request', outcome: 'failure', reason: 'unknown_user' },
      ]);
      records.length = 0;
      process.env.TURNSTILE_SECRET_KEY = 'x';
      sinon.stub(turnstile, 'verifyTurnstileToken').resolves(false);
      await call('POST', '/password/forgot', { email: alice.email });
      expect(audits()).to.deep.equal([{ event: 'auth.password_reset.request', outcome: 'failure', reason: 'captcha_failed' }]);
      delete process.env.TURNSTILE_SECRET_KEY;
      records.length = 0;
      iam.getUserByEmail = async () => { throw new Error('IAM down'); };
      await call('POST', '/password/forgot', { email: alice.email });
      await waitForRecord((r) => r.message === "The password reset email couldn't be sent");
      expect(audits()).to.deep.equal([]);
      expectNoPrivateData();
    });
  });

  describe('password reset, change and setup', () => {
    const resetToken = (p: Person = alice) => jwtGeneratorForForgotPasswordLink(p.email, p._id, orgId, SCOPED_SECRET).passwordResetToken;
    const reset = (token?: string) =>
      call('POST', '/password/reset/token', { password: NEW_PASSWORD }, token === undefined ? {} : { authorization: `Bearer ${token}` });

    it('T16 reset via the link: success, then the same link again is link_used', async () => {
      const token = resetToken();
      records.length = 0;
      expect((await reset(token)).status).to.equal(200);
      expect((await reset(token)).status).to.equal(401);
      expect(audits()).to.deep.equal([
        { event: 'auth.password_reset.complete', outcome: 'success', actor: alice._id },
        { event: 'auth.password_reset.complete', outcome: 'failure', reason: 'link_used', actor: alice._id },
      ]);
      expect(records.filter((r) => r.message === 'http.request.error')).to.have.length(1);
      expectNoPrivateData();
    });

    it('T16 reset via the link: concurrent claim, expired, forged, missing, locked, unknown account', async () => {
      records.length = 0;
      resetLinkClaimed = false;
      await reset(resetToken());
      resetLinkClaimed = true;
      const claims = { userEmail: alice.email, userId: alice._id, orgId, scopes: [TokenScopes.PASSWORD_RESET] };
      await reset(userActionToken(claims, { expiresIn: -10 }));
      await reset(jwt.sign(claims, 'not-our-secret', { expiresIn: '10m' }));
      await reset();
      lock(alice);
      await reset(resetToken());
      const gone = person('Gone', 'gone@acme.test');
      await reset(resetToken(gone));
      expect(audits()).to.deep.equal([
        { event: 'auth.password_reset.complete', outcome: 'failure', reason: 'link_used', actor: alice._id },
        { event: 'auth.password_reset.complete', outcome: 'failure', reason: 'token_expired' },
        { event: 'auth.password_reset.complete', outcome: 'failure', reason: 'token_invalid' },
        { event: 'auth.password_reset.complete', outcome: 'failure', reason: 'no_token' },
        { event: 'auth.password_reset.complete', outcome: 'failure', reason: 'locked', actor: alice._id },
        { event: 'auth.password_reset.complete', outcome: 'failure', reason: 'unknown_user', actor: gone._id },
      ]);
    });

    it('T17 password change: success even when the new token is then refused; wrong current; CAPTCHA', async () => {
      const auth = { authorization: `Bearer ${await accessToken(alice)}` };
      records.length = 0;
      accountState = { kind: 'user', isDisabled: true };
      await call('POST', '/password/reset', { currentPassword: PASSWORD, newPassword: NEW_PASSWORD }, auth);
      accountState = { kind: 'user', isDisabled: false };
      await call('POST', '/password/reset', { currentPassword: 'Not-It-1!', newPassword: PASSWORD }, auth);
      process.env.TURNSTILE_SECRET_KEY = 'x';
      sinon.stub(turnstile, 'verifyTurnstileToken').resolves(false);
      await call('POST', '/password/reset', { currentPassword: NEW_PASSWORD, newPassword: PASSWORD }, auth);
      expect(audits()).to.deep.equal([
        { event: 'auth.password.change', outcome: 'success', actor: alice._id },
        { event: 'auth.password.change', outcome: 'failure', reason: 'bad_current_password', actor: alice._id },
        { event: 'auth.password.change', outcome: 'failure', reason: 'captcha_failed', actor: alice._id },
      ]);
      expectNoPrivateData();
    });

    it('T18 sign-out', async () => {
      const auth = { authorization: `Bearer ${await accessToken(alice)}` };
      records.length = 0;
      expect((await call('POST', '/logout/manual', {}, auth)).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'auth.sign_out', outcome: 'success', actor: alice._id }]);
    });
  });

  describe('email change and token refresh', () => {
    const emailToken = () => jwtGeneratorForValidateEmailLink(alice.email, NEW_ADDRESS, alice._id, orgId, SCOPED_SECRET).validateEmailToken;
    const validate = (token: string) => call('PUT', '/validateEmailChange', {}, { authorization: `Bearer ${token}` });

    it('T19 email change: success; taken; the same link again', async () => {
      nock('http://connector.test').patch('/api/v1/entity/user/email').reply(200, {});
      const token = emailToken();
      records.length = 0;
      expect((await validate(token)).status).to.equal(200);
      expect((await validate(token)).status).to.equal(401);
      emailTaken = true;
      lastPasswordChange = null; // a fresh link, issued after the change above
      expect((await validate(emailToken())).status).to.equal(400);
      expect(audits()).to.deep.equal([
        { event: 'auth.email.change', outcome: 'success', reason: 'link', actor: alice._id },
        { event: 'auth.email.change', outcome: 'failure', reason: 'link_used', actor: alice._id },
        { event: 'auth.email.change', outcome: 'failure', reason: 'email_taken', actor: alice._id },
      ]);
      // F31 removes the address from the email-taken message; until then only that error line has it.
      expectNoPrivateData((r) => r.message === 'http.request.error');
    });

    it('T20 refresh refusals; a successful refresh writes no audit line', async () => {
      const refresh = (token: string) => call('POST', '/refresh/token', {}, { authorization: `Bearer ${token}` });
      const token = refreshTokenJwtGenerator(alice._id, orgId, SCOPED_SECRET);
      records.length = 0;
      expect((await refresh(token)).status).to.equal(200);
      expect(audits()).to.deep.equal([]);
      sessionEnding = { activityType: 'LOGOUT', createdAt: new Date(Date.now() + 1000) };
      await refresh(token);
      sessionEnding = null;
      lock(alice);
      await refresh(token);
      delete creds[alice._id];
      await refresh(token);
      await givePassword(alice);
      iam.getUserById = async () => ({ statusCode: 200, data: { ...alice, isDisabled: true } });
      await refresh(token);
      await refresh(userActionToken({ userId: alice._id, orgId, scopes: [TokenScopes.TOKEN_REFRESH] }, { expiresIn: -10 }));
      await refresh(jwt.sign({ userId: alice._id, orgId, scopes: [TokenScopes.TOKEN_REFRESH] }, 'forged'));
      expect(audits()).to.deep.equal([
        { event: 'auth.session.refresh', outcome: 'failure', reason: 'session_ended', actor: alice._id },
        { event: 'auth.session.refresh', outcome: 'failure', reason: 'locked', actor: alice._id },
        { event: 'auth.session.refresh', outcome: 'failure', reason: 'no_credentials', actor: alice._id },
        { event: 'auth.session.refresh', outcome: 'failure', reason: 'account_inactive', actor: alice._id },
        { event: 'auth.session.refresh', outcome: 'failure', reason: 'token_expired' },
        { event: 'auth.session.refresh', outcome: 'failure', reason: 'token_invalid' },
      ]);
    });

    it('T28 [guard] a refused service-to-service token is not audited', async () => {
      records.length = 0;
      const r = await call('GET', '/internal/password/check', undefined, { authorization: 'Bearer forged.token.value' });
      expect(r.status).to.equal(401);
      expect(audits()).to.deep.equal([]);
      const ok = await call('GET', '/internal/password/check', undefined, { authorization: `Bearer ${fetchConfigJwtGenerator(alice._id, orgId, SCOPED_SECRET)}` });
      expect(ok.status).to.equal(200);
      expect(audits()).to.deep.equal([]);
    });
  });

  describe('with the real IAM client (nock)', () => {
    beforeEach(useRealIam);

    it('T22 sign-in policy: an admin updates it; IAM answering 403 (the role changed) is not_admin', async () => {
      sinon.stub(userAdmin, 'isUserOrgAdmin').resolves(true);
      const auth = { authorization: `Bearer ${await accessToken({ ...alice, role: 'admin' })}` };
      nock(IAM_BACKEND).get(`/api/v1/users/internal/${alice._id}/adminCheck`).reply(200, { isAdmin: true });
      nock(IAM_BACKEND).get(`/api/v1/users/internal/${alice._id}/adminCheck`).reply(403, { message: 'Admin access required' });
      const body = { authMethod: [{ order: 1, allowedMethods: [{ type: 'password' }] }] };
      records.length = 0;
      const admin = await call('POST', '/updateAuthMethod', body, auth, '/api/v1/orgAuthConfig');
      expect(admin.status, JSON.stringify(admin.body)).to.equal(200);
      const refused = await call('POST', '/updateAuthMethod', body, auth, '/api/v1/orgAuthConfig');
      expect(refused.status).to.be.at.least(400);
      expect(audits()).to.deep.equal([
        { event: 'auth.policy.update', outcome: 'success', actor: alice._id },
        { event: 'auth.policy.update', outcome: 'failure', reason: 'not_admin', actor: alice._id },
      ]);
      expect(nock.isDone()).to.equal(true);
    });

    it('T23 [guard] an IAM 500 on sign-in is no audit line and one error line with the cause', async () => {
      nock(IAM_BACKEND).get('/api/v1/users/email/exists').reply(500, { message: 'boom' });
      const r = await password();
      expect(r.status).to.be.at.least(500);
      expect(audits()).to.deep.equal([]);
      expect(records.filter((x) => x.message === 'http.request.error')).to.have.length(1);
    });
  });
});
