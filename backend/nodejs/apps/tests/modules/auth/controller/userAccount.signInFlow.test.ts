import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import bcrypt from 'bcryptjs';
import jwt from 'jsonwebtoken';
import mongoose from 'mongoose';
import axios from 'axios';
import { LoginTicket, OAuth2Client } from 'google-auth-library';
import type { NextFunction, RequestHandler, Response } from 'express';
import { Container } from 'inversify';
import {
  UserAccountController,
  EMAIL_MISMATCH,
  OAUTH_SIGN_IN_FAILED,
  PROVIDER_SHARED_NO_EMAIL,
  SESSION_NO_LONGER_VALID,
  OTP_SEND_FAILED,
  SIGN_IN_ACCOUNT_CHANGED,
} from '../../../../src/modules/auth/controller/userAccount.controller';
import { DISABLED_ACCOUNT_SIGN_IN_MESSAGE } from '../../../../src/modules/auth/utils/generateAuthToken';
import { SessionService } from '../../../../src/modules/auth/services/session.service';
import { SamlController } from '../../../../src/modules/auth/controller/saml.controller';
import { createSamlRouter } from '../../../../src/modules/auth/routes/saml.routes';
import type { Logger } from '../../../../src/libs/services/logger.service';
import { OrgAuthConfig } from '../../../../src/modules/auth/schema/orgAuthConfiguration.schema';
import { UserCredentials } from '../../../../src/modules/auth/schema/userCredentials.schema';
import { UserActivities } from '../../../../src/modules/auth/schema/userActivities.schema';
import { Org } from '../../../../src/modules/user_management/schema/org.schema';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import * as azureAd from '../../../../src/modules/auth/utils/azureAdTokenValidation';
import {
  BadRequestError,
  ForbiddenError,
  InternalServerError,
  NotFoundError,
  UnauthorizedError,
} from '../../../../src/libs/errors/http.errors';
import { deriveUserActionSecret } from '../../../../src/libs/utils/jwtKeys';
import type { ICacheService } from '../../../../src/libs/services/cache/cacheService.interface';
import type { AuthenticatedServiceRequest } from '../../../../src/libs/middlewares/types';
import { createMockRedisService } from '../../../helpers/mock-redis';
import { createMockQuery } from '../../../helpers/mock-mongo';

// Drives the real controller and the real SessionService through whole
// sign-ins, step after step, the way the /initAuth and /authenticate routes do.
// Only the outermost I/O is faked: Mongo models, the IAM HTTP service, the mail
// service, the Redis cache behind the session, and the identity providers.

const JWT_SECRET = 'sign-in-flow-jwt-secret';
const SCOPED_SECRET = 'sign-in-flow-scoped-secret';
const PASSWORD = 'Correct-Horse-9!';

const orgId = new mongoose.Types.ObjectId().toHexString();
interface DirectoryUser {
  _id: string;
  orgId: string;
  email: string;
  fullName: string;
  role?: string;
  hasLoggedIn: boolean;
}

const alice: DirectoryUser = {
  _id: new mongoose.Types.ObjectId().toHexString(),
  orgId,
  email: 'alice@acme.test',
  fullName: 'Alice Admin',
  role: 'member',
  hasLoggedIn: true,
};
const mallory: DirectoryUser = {
  _id: new mongoose.Types.ObjectId().toHexString(),
  orgId,
  email: 'mallory@acme.test',
  fullName: 'Mallory Member',
  role: 'member',
  hasLoggedIn: true,
};
const directory: Record<string, DirectoryUser> = {
  [alice.email]: alice,
  [mallory.email]: mallory,
};

interface CredentialsDoc {
  [field: string]: unknown;
  isBlocked: boolean;
  wrongCredentialCount: number;
  blockExpiresAt: Date | null;
  hashedOTP?: string;
  save: sinon.SinonStub;
}

function credentialsDoc(fields: Record<string, unknown>): CredentialsDoc {
  const doc: CredentialsDoc = {
    isBlocked: false,
    wrongCredentialCount: 0,
    blockExpiresAt: null,
    save: sinon.stub(),
    ...fields,
  };
  doc.save.resolves(doc);
  return doc;
}

// Fake requests and responses are cast once here, to whatever the handler
// under test declares, instead of at every call.
function fakeRequest<T>(fields: Record<string, unknown>): T {
  return fields as unknown as T;
}

interface ResponseBody {
  [field: string]: unknown;
  message?: string;
  accessToken?: string;
  refreshToken?: string;
  allowedMethods?: string[];
  authProviders?: Record<string, Record<string, unknown>>;
}

interface FakeRes {
  headers: Record<string, string>;
  statusCode: number;
  body: ResponseBody | undefined;
  status: sinon.SinonStub;
  json: sinon.SinonStub;
  send: sinon.SinonStub;
  setHeader: sinon.SinonStub;
}

function fakeResponse(res: FakeRes): Response {
  return res as unknown as Response;
}

type SignInError = Error & { statusCode?: number };

type StubbedService<K extends string> = Record<K, sinon.SinonStub>;
type TokenClaims = { userId?: string };
type AzureClaims = Awaited<ReturnType<typeof azureAd.validateAzureAdUser>>;
type AzureIdentity = ReturnType<typeof azureAd.microsoftAccountIdentity>;

describe('UserAccountController sign-in flow', () => {
  let controller: UserAccountController;
  let sessionService: SessionService;
  let redisStore: Map<string, string>;
  let iamService: StubbedService<
    'getUserByEmail' | 'getUserById' | 'updateUser' | 'checkAdminUser' | 'createOrg'
  >;
  let mailService: StubbedService<'sendMail'>;
  let configService: StubbedService<'getConfig'>;
  let jitService: StubbedService<
    | 'provisionUser'
    | 'extractGoogleUserDetails'
    | 'extractMicrosoftUserDetails'
    | 'extractOAuthUserDetails'
    | 'extractSamlUserDetails'
  >;
  let logger: StubbedService<'info' | 'debug' | 'warn' | 'error'>;
  let credentialsByUser: Record<string, CredentialsDoc | undefined>;
  let activities: Array<Record<string, unknown>>;
  let configuredSteps: string[][];
  let disabledUserIds: Set<string>;

  function orgAuthConfig(steps: string[][]) {
    return {
      orgId,
      authSteps: steps.map((types, i) => ({
        order: i + 1,
        allowedMethods: types.map((type) => ({ type })),
      })),
    };
  }

  function makeRes(): FakeRes {
    const json = sinon.stub();
    const res: FakeRes = {
      headers: {},
      statusCode: 200,
      body: undefined,
      status: sinon.stub(),
      json,
      send: json,
      setHeader: sinon.stub(),
    };
    res.status.callsFake((code: number) => {
      res.statusCode = code;
      return res;
    });
    json.callsFake((body: ResponseBody) => {
      res.body = body;
      return res;
    });
    res.setHeader.callsFake((k: string, v: string) => {
      res.headers[k] = v;
      return res;
    });
    return res;
  }

  async function initAuth(steps: string[][], email = alice.email) {
    configuredSteps = steps;
    const res = makeRes();
    const next = sinon.stub();
    await controller.initAuth(fakeRequest({ body: { email } }), fakeResponse(res), next);
    expect(next.called, 'initAuth must not fail').to.be.false;
    return res.headers['x-session-token'] as string;
  }

  // Mirrors authSessionMiddleware: the session is read back from the cache on
  // every request, so whatever a step saved is what the next step sees.
  async function authenticate(token: string, body: Record<string, unknown>) {
    const sessionInfo = await sessionService.getSession(token);
    const res = makeRes();
    const next = sinon.stub();
    await controller.authenticate(
      fakeRequest({ body, sessionInfo: sessionInfo ?? undefined, ip: '10.0.0.1' }),
      fakeResponse(res),
      next,
    );
    // Success cases check that `error` is undefined before reading it.
    return { res, next, error: next.firstCall?.args[0] as SignInError };
  }

  beforeEach(() => {
    redisStore = new Map();
    const redis = createMockRedisService();
    redis.set.callsFake(async (key: string, value: unknown) => {
      redisStore.set(key, JSON.stringify(value));
    });
    redis.get.callsFake(async (key: string) => {
      const raw = redisStore.get(key);
      return raw === undefined ? null : JSON.parse(raw);
    });
    redis.delete.callsFake(async (key: string) => {
      redisStore.delete(key);
    });
    sessionService = new SessionService(redis as unknown as ICacheService);

    iamService = {
      getUserByEmail: sinon.stub().callsFake(async (email: string) => {
        const user = directory[email.toLowerCase()];
        return user
          ? { statusCode: 200, data: { ...user } }
          : { statusCode: 404, data: null };
      }),
      getUserById: sinon.stub(),
      updateUser: sinon.stub().resolves({ statusCode: 200, data: {} }),
      checkAdminUser: sinon.stub(),
      createOrg: sinon.stub(),
    };
    mailService = { sendMail: sinon.stub().resolves({ statusCode: 200 }) };
    configService = { getConfig: sinon.stub() };
    jitService = {
      provisionUser: sinon.stub(),
      extractGoogleUserDetails: sinon.stub().returns({ fullName: 'New Person' }),
      extractMicrosoftUserDetails: sinon.stub().returns({ fullName: 'New Person' }),
      extractOAuthUserDetails: sinon.stub().returns({ fullName: 'New Person' }),
      extractSamlUserDetails: sinon.stub().returns({ fullName: 'New Person' }),
    };
    logger = {
      info: sinon.stub(),
      debug: sinon.stub(),
      warn: sinon.stub(),
      error: sinon.stub(),
    };

    const appConfig = {
      cmBackend: 'http://cm',
      frontendUrl: 'http://app',
      jwtSecret: JWT_SECRET,
      scopedJwtSecret: SCOPED_SECRET,
      rsAvailable: 'false',
    };
    controller = new UserAccountController(
      ...([
        appConfig,
        iamService,
        mailService,
        sessionService,
        configService,
        logger,
        jitService,
      ] as unknown as ConstructorParameters<typeof UserAccountController>),
    );

    credentialsByUser = {};
    disabledUserIds = new Set();
    sinon.stub(UserCredentials, 'findOne').callsFake(((filter: { userId: string }) =>
      Promise.resolve(credentialsByUser[String(filter.userId)] ?? null)) as unknown as typeof UserCredentials.findOne);
    sinon.stub(UserCredentials, 'findOneAndUpdate').callsFake(((
      filter: { userId: string; hashedOTP?: string },
      update: {
        $inc?: { wrongCredentialCount?: number };
        $set?: Record<string, unknown>;
        $unset?: Record<string, unknown>;
      },
    ) => {
      const doc = credentialsByUser[String(filter.userId)];
      if (!doc || ('hashedOTP' in filter && doc.hashedOTP !== filter.hashedOTP)) {
        return Promise.resolve(null);
      }
      if (update.$inc?.wrongCredentialCount) {
        doc.wrongCredentialCount += update.$inc.wrongCredentialCount;
      }
      Object.assign(doc, update.$set ?? {});
      for (const field of Object.keys(update.$unset ?? {})) {
        delete doc[field];
      }
      return Promise.resolve(doc);
    }) as unknown as typeof UserCredentials.findOneAndUpdate);
    configuredSteps = [['password']];
    sinon.stub(Org, 'findOne').callsFake((() =>
      Promise.resolve({ _id: orgId, shortName: 'Acme' })) as unknown as typeof Org.findOne);
    sinon
      .stub(OrgAuthConfig, 'findOne')
      .callsFake((() => Promise.resolve(orgAuthConfig(configuredSteps))) as unknown as typeof OrgAuthConfig.findOne);
    activities = [];
    sinon.stub(UserActivities, 'create').callsFake((async (doc: Record<string, unknown>) => {
      activities.push(doc);
      return doc;
    }) as unknown as typeof UserActivities.create);
    sinon.stub(Users, 'findOne').callsFake(((filter: { _id: string }) => {
      const found = Object.values(directory).find(
        (u) => u._id === String(filter._id),
      );
      return createMockQuery(
        found ? { kind: 'user', isDisabled: disabledUserIds.has(found._id) } : null,
      );
    }) as unknown as typeof Users.findOne);
  });

  afterEach(() => {
    sinon.restore();
  });

  async function givePassword(user: DirectoryUser, password = PASSWORD) {
    credentialsByUser[user._id] = credentialsDoc({
      userId: user._id,
      orgId,
      hashedPassword: await bcrypt.hash(password, 4),
    });
  }

  async function giveOtp(user: DirectoryUser, otp: string) {
    const existing = credentialsByUser[user._id];
    const fields = {
      userId: user._id,
      orgId,
      hashedOTP: await bcrypt.hash(otp, 4),
      otpValidity: Date.now() + 5 * 60 * 1000,
    };
    if (existing) {
      Object.assign(existing, fields);
    } else {
      credentialsByUser[user._id] = credentialsDoc(fields);
    }
  }

  function googleSignsInAs(email: string | undefined) {
    return sinon.stub(OAuth2Client.prototype, 'verifyIdToken').resolves({
      getPayload: () => (email ? { email, name: 'Someone' } : { name: 'Someone' }),
    } as unknown as LoginTicket);
  }

  describe('the sign-in method has to be one the org allows at this step', () => {
    it('refuses password sign-in when the org only allows Google, even with the right password', async () => {
      await givePassword(alice);
      const token = await initAuth([['google']]);

      const { res, error } = await authenticate(token, {
        method: 'password',
        credentials: { password: PASSWORD },
      });

      expect(error).to.be.instanceOf(BadRequestError);
      expect(error.message).to.match(/isn't turned on/);
      expect(res.body).to.be.undefined;
      expect(await sessionService.getSession(token)).to.not.equal(null);
    });

    it('refuses to let the password from step one stand in for the code step two asks for', async () => {
      await givePassword(alice);
      const token = await initAuth([['password'], ['otp']]);

      const first = await authenticate(token, {
        method: 'password',
        credentials: { password: PASSWORD },
      });
      expect(first.error).to.be.undefined;
      expect(first.res.body).to.include({ status: 'success', nextStep: 1 });
      expect(first.res.body?.allowedMethods).to.deep.equal(['otp']);

      const second = await authenticate(token, {
        method: 'password',
        credentials: { password: PASSWORD },
      });

      expect(second.error).to.be.instanceOf(BadRequestError);
      expect(second.error.message).to.match(/isn't turned on/);
      expect(second.res.body).to.be.undefined;
    });

    it('completes a password-then-code sign-in and ends the sign-in session', async () => {
      await givePassword(alice);
      const token = await initAuth([['password'], ['otp']]);
      await authenticate(token, {
        method: 'password',
        credentials: { password: PASSWORD },
      });
      await giveOtp(alice, '482913');

      const { res, error } = await authenticate(token, {
        method: 'otp',
        credentials: { otp: '482913' },
      });

      expect(error).to.be.undefined;
      expect(res.statusCode).to.equal(200);
      expect(res.body?.message).to.equal('Fully authenticated');
      const access = jwt.verify(String(res.body?.accessToken), JWT_SECRET) as TokenClaims;
      expect(access.userId).to.equal(alice._id);
      const refresh = jwt.verify(
        String(res.body?.refreshToken),
        deriveUserActionSecret(SCOPED_SECRET),
      ) as TokenClaims;
      expect(refresh.userId).to.equal(alice._id);
      expect(await sessionService.getSession(token)).to.equal(null);
    });
  });

  describe('every step has to prove the same account', () => {
    it("refuses a Google step two for another member after step one checked Alice's password", async () => {
      await givePassword(alice);
      configService.getConfig.resolves({ data: { clientId: 'google-client' } });
      const token = await initAuth([['password'], ['google']]);
      await authenticate(token, {
        method: 'password',
        credentials: { password: PASSWORD },
      });
      googleSignsInAs(mallory.email);

      const { res, error } = await authenticate(token, {
        method: 'google',
        credentials: 'mallory-google-id-token',
      });

      expect(error).to.be.instanceOf(UnauthorizedError);
      expect(error.message).to.match(/same account/);
      expect(res.body).to.be.undefined;
    });

    it('refuses an unknown Google identity at step two before any account could be created', async () => {
      await givePassword(alice);
      configService.getConfig.resolves({
        data: { clientId: 'google-client', enableJit: true },
      });
      const token = await initAuth([['password'], ['google']]);
      await authenticate(token, {
        method: 'password',
        credentials: { password: PASSWORD },
      });
      // initAuth takes JIT settings from step one's methods only, so turn it on
      // for Google here: the same-account check alone must stop the create.
      const session = await sessionService.getSession(token);
      await sessionService.updateSession({ ...session!, jitConfig: { google: true } });
      googleSignsInAs('stranger@elsewhere.test');

      const { error } = await authenticate(token, {
        method: 'google',
        credentials: 'stranger-google-id-token',
      });

      expect(error).to.be.instanceOf(UnauthorizedError);
      expect(error.message).to.match(/same account/);
      expect(jitService.provisionUser.called).to.be.false;
    });

    it("signs Alice in when step two is Alice's own Google account", async () => {
      await givePassword(alice);
      configService.getConfig.resolves({ data: { clientId: 'google-client' } });
      const token = await initAuth([['password'], ['google']]);
      await authenticate(token, {
        method: 'password',
        credentials: { password: PASSWORD },
      });
      const verify = googleSignsInAs(alice.email);

      const { res, error } = await authenticate(token, {
        method: 'google',
        credentials: 'alice-google-id-token',
      });

      expect(error).to.be.undefined;
      expect(res.body?.message).to.equal('Fully authenticated');
      expect(verify.firstCall.args[0]).to.deep.include({
        idToken: 'alice-google-id-token',
        audience: 'google-client',
      });
      expect(
        (jwt.verify(String(res.body?.accessToken), JWT_SECRET) as TokenClaims).userId,
      ).to.equal(alice._id);
    });
  });

  describe('a sign-in code works only once', () => {
    it('refuses the same code a second time after it signed the user in', async () => {
      const first = await initAuth([['otp']]);
      await giveOtp(alice, '731640');
      const ok = await authenticate(first, {
        method: 'otp',
        credentials: { otp: '731640' },
      });
      expect(ok.error).to.be.undefined;
      expect(ok.res.body?.message).to.equal('Fully authenticated');

      const replayToken = await initAuth([['otp']]);

      const replay = await authenticate(replayToken, {
        method: 'otp',
        credentials: { otp: '731640' },
      });

      expect(replay.error).to.be.instanceOf(UnauthorizedError);
      expect(replay.res.body).to.be.undefined;
    });
  });

  describe('the next step tells the browser how to show each provider, without secrets', () => {
    it('lists every provider for step two and strips the OAuth client secret and server endpoints', async () => {
      await givePassword(alice);
      configService.getConfig.callsFake(async (_b: string, path: string) => {
        if (path.includes('google')) return { data: { clientId: 'g-id' } };
        if (path.includes('microsoft')) return { data: { clientId: 'm-id', tenantId: 't' } };
        if (path.includes('azureAd')) return { data: { clientId: 'a-id', tenantId: 't' } };
        return {
          data: {
            clientId: 'o-id',
            providerName: 'Okta',
            authorizationUrl: 'https://idp/authorize',
            clientSecret: 'must-not-leak',
            tokenEndpoint: 'https://idp/token',
            userInfoEndpoint: 'https://idp/userinfo',
          },
        };
      });
      const token = await initAuth([
        ['password'],
        ['google', 'microsoft', 'azureAd', 'oauth'],
      ]);

      const { res, error } = await authenticate(token, {
        method: 'password',
        credentials: { password: PASSWORD },
      });

      expect(error).to.be.undefined;
      const providers = res.body?.authProviders ?? {};
      expect(providers.google).to.deep.equal({ clientId: 'g-id' });
      expect(providers.microsoft).to.include({ clientId: 'm-id' });
      expect(providers.azureAd).to.include({ clientId: 'a-id' });
      expect(providers.oauth).to.deep.equal({
        clientId: 'o-id',
        providerName: 'Okta',
        authorizationUrl: 'https://idp/authorize',
      });
      expect(JSON.stringify(res.body)).to.not.include('must-not-leak');
    });
  });

  describe('first sign-in', () => {
    it('marks the account as having signed in once the last step passes', async () => {
      const newcomer = { ...alice, hasLoggedIn: false };
      directory[alice.email] = newcomer;
      try {
        await givePassword(alice);
        const token = await initAuth([['password']]);

        const { res, error } = await authenticate(token, {
          method: 'password',
          credentials: { password: PASSWORD },
        });

        expect(error).to.be.undefined;
        expect(res.body?.message).to.equal('Fully authenticated');
        expect(iamService.updateUser.calledOnce).to.be.true;
        expect(iamService.updateUser.firstCall.args[0]).to.equal(alice._id);
        expect(iamService.updateUser.firstCall.args[1]).to.deep.equal({
          hasLoggedIn: true,
          email: alice.email,
        });
      } finally {
        directory[alice.email] = alice;
      }
    });
  });

  describe('external providers', () => {
    it('refuses Google when the token carries no email', async () => {
      configService.getConfig.resolves({ data: { clientId: 'google-client' } });
      const token = await initAuth([['google']]);
      googleSignsInAs(undefined);

      const { error } = await authenticate(token, {
        method: 'google',
        credentials: { credential: 'id-token' },
      });

      expect(error).to.be.instanceOf(UnauthorizedError);
      expect(error.message).to.equal(PROVIDER_SHARED_NO_EMAIL);
    });

    it('refuses an unknown Google identity when JIT is off for Google', async () => {
      configService.getConfig.resolves({ data: { clientId: 'google-client' } });
      const token = await initAuth([['google']]);
      googleSignsInAs('stranger@elsewhere.test');

      const { error } = await authenticate(token, {
        method: 'google',
        credentials: 'id-token',
      });

      expect(error).to.be.instanceOf(BadRequestError);
      expect(error.message).to.match(/Account not found/);
      expect(jitService.provisionUser.called).to.be.false;
    });

    it('creates the account on first Google sign-in when the admin turned JIT on', async () => {
      configService.getConfig.resolves({
        data: { clientId: 'google-client', enableJit: true },
      });
      const token = await initAuth([['google']]);
      const newcomer: DirectoryUser = {
        _id: new mongoose.Types.ObjectId().toHexString(),
        orgId,
        email: 'newcomer@acme.test',
        fullName: 'New Person',
        hasLoggedIn: false,
      };
      jitService.provisionUser.callsFake(async () => {
        directory[newcomer.email] = newcomer;
        return newcomer;
      });
      googleSignsInAs(newcomer.email);

      try {
        const { res, error } = await authenticate(token, {
          method: 'google',
          credentials: 'id-token',
        });

        expect(error).to.be.undefined;
        expect(jitService.provisionUser.firstCall.args.slice(0, 4)).to.deep.equal([
          newcomer.email,
          { fullName: 'New Person' },
          orgId,
          'google',
        ]);
        expect(res.body?.message).to.equal('Fully authenticated');
        expect(activities.some((a) => a.loginMode === 'GOOGLE OAUTH')).to.be.true;
      } finally {
        delete directory[newcomer.email];
      }
    });

    it('signs in with Microsoft when the verified identity matches the account', async () => {
      configService.getConfig.resolves({
        data: { clientId: 'ms-client', tenantId: 'tenant-1' },
      });
      const decoded = { tid: 'tenant-1', email: alice.email };
      sinon.stub(azureAd, 'validateAzureAdUser').resolves(decoded as unknown as AzureClaims);
      sinon
        .stub(azureAd, 'microsoftAccountIdentity')
        .returns({ email: alice.email, emailClaimTrusted: false } as unknown as AzureIdentity);
      const token = await initAuth([['microsoft']]);

      const { res, error } = await authenticate(token, {
        method: 'microsoft',
        credentials: { idToken: 'ms-id-token', accessToken: 'ms-access' },
      });

      expect(error).to.be.undefined;
      expect(res.body?.message).to.equal('Fully authenticated');
      expect(activities.some((a) => a.loginMode === 'MICROSOFT OAUTH')).to.be.true;
    });

    it('refuses Azure AD when the token shares no usable identity', async () => {
      configService.getConfig.resolves({
        data: { clientId: 'az-client', tenantId: 'tenant-1' },
      });
      sinon.stub(azureAd, 'validateAzureAdUser').resolves({} as unknown as AzureClaims);
      sinon
        .stub(azureAd, 'microsoftAccountIdentity')
        .returns({ email: '', emailClaimTrusted: false } as unknown as AzureIdentity);
      const token = await initAuth([['azureAd']]);

      const { error } = await authenticate(token, {
        method: 'azureAd',
        credentials: { idToken: 'az-id-token' },
      });

      expect(error).to.be.instanceOf(UnauthorizedError);
      expect(error.message).to.equal(PROVIDER_SHARED_NO_EMAIL);
    });

    describe('generic OAuth', () => {
      let fetchStub: sinon.SinonStub;
      beforeEach(() => {
        configService.getConfig.resolves({
          data: { userInfoEndpoint: 'https://idp/userinfo', providerName: 'Okta' },
        });
        fetchStub = sinon.stub(globalThis, 'fetch');
      });

      it('signs in when the provider vouches for the same email', async () => {
        fetchStub.callsFake(async () =>
          new Response(JSON.stringify({ email: alice.email }), { status: 200 }),
        );
        const token = await initAuth([['oauth']]);

        const { res, error } = await authenticate(token, {
          method: 'oauth',
          credentials: { accessToken: 'oauth-access' },
        });

        expect(error).to.be.undefined;
        expect(res.body?.message).to.equal('Fully authenticated');
        expect(fetchStub.firstCall.args[1].headers.Authorization).to.equal(
          'Bearer oauth-access',
        );
      });

      it('refuses when the call is missing its access token', async () => {
        const token = await initAuth([['oauth']]);

        const { error } = await authenticate(token, {
          method: 'oauth',
          credentials: { idToken: 'no-access-token' },
        });

        expect(error).to.be.instanceOf(BadRequestError);
        expect(error.message).to.equal(OAUTH_SIGN_IN_FAILED);
        expect(fetchStub.called).to.be.false;
      });

      it('refuses when the provider rejects the access token', async () => {
        fetchStub.resolves(new Response('nope', { status: 401 }));
        const token = await initAuth([['oauth']]);

        const { error } = await authenticate(token, {
          method: 'oauth',
          credentials: { accessToken: 'revoked' },
        });

        expect(error).to.be.instanceOf(UnauthorizedError);
        expect(error.message).to.equal(OAUTH_SIGN_IN_FAILED);
      });

      it('refuses when the provider shares no email, username or subject', async () => {
        fetchStub.resolves(new Response(JSON.stringify({}), { status: 200 }));
        const token = await initAuth([['oauth']]);

        const { error } = await authenticate(token, {
          method: 'oauth',
          credentials: { accessToken: 'oauth-access' },
        });

        expect(error).to.be.instanceOf(BadRequestError);
        expect(error.message).to.equal(PROVIDER_SHARED_NO_EMAIL);
      });
    });
  });

  describe('refreshing an access token', () => {
    const issuedAt = Math.floor(Date.now() / 1000) - 3600;

    function refreshReq(): AuthenticatedServiceRequest {
      return fakeRequest({
        tokenPayload: { userId: alice._id, orgId, iat: issuedAt },
        ip: '10.0.0.1',
      });
    }

    function stubLatestInvalidation(result: unknown) {
      const query = {
        sort: sinon.stub().returnsThis(),
        lean: sinon.stub().returnsThis(),
        exec:
          typeof result === 'function'
            ? sinon.stub().callsFake(result as () => Promise<unknown>)
            : sinon.stub().resolves(result),
      };
      return sinon
        .stub(UserActivities, 'findOne')
        .returns(query as unknown as ReturnType<typeof UserActivities.findOne>);
    }

    it('refuses a refresh token issued before the user signed out', async () => {
      stubLatestInvalidation({ createdAt: new Date(), activityType: 'LOGOUT' });
      const res = makeRes();
      const next = sinon.stub();

      await controller.getAccessTokenFromRefreshToken(refreshReq(), fakeResponse(res), next);

      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError);
      expect(iamService.getUserById.called).to.be.false;
      expect(res.body).to.be.undefined;
    });

    it('still refreshes when the sign-out lookup fails, and logs the failure', async () => {
      stubLatestInvalidation(async () => {
        throw new Error('mongo timeout');
      });
      iamService.getUserById.resolves({ statusCode: 200, data: { ...alice } });
      credentialsByUser[alice._id] = credentialsDoc({ userId: alice._id, orgId });
      const res = makeRes();
      const next = sinon.stub();

      await controller.getAccessTokenFromRefreshToken(refreshReq(), fakeResponse(res), next);

      expect(next.called).to.be.false;
      expect(res.body?.accessToken).to.be.a('string');
      expect(logger.error.calledWithMatch('Failed to fetch session-invalidating activity on refresh')).to.be.true;
    });

    it('refuses to refresh for a user who has since been deleted', async () => {
      stubLatestInvalidation(null);
      iamService.getUserById.resolves({ statusCode: 404, data: null });
      const res = makeRes();
      const next = sinon.stub();

      await controller.getAccessTokenFromRefreshToken(refreshReq(), fakeResponse(res), next);

      expect(next.firstCall.args[0]).to.be.instanceOf(NotFoundError);
      expect(next.firstCall.args[0].message).to.equal(SESSION_NO_LONGER_VALID);
    });
  });

  describe('repeated wrong passwords lock the account', () => {
    async function tryPassword(password: string) {
      const token = await initAuth([['password']]);
      return authenticate(token, { method: 'password', credentials: { password } });
    }

    it('locks after the fifth wrong password, warns the owner, and then refuses even the right password', async () => {
      await givePassword(alice);

      for (let attempt = 1; attempt <= 4; attempt++) {
        const { error } = await tryPassword('wrong-guess');
        expect(error, `attempt ${attempt}`).to.be.instanceOf(BadRequestError);
        expect(error.message).to.equal('Incorrect password, please try again.');
      }
      expect(mailService.sendMail.called).to.be.false;

      const fifth = await tryPassword('wrong-guess');
      expect(fifth.error.message).to.equal('Incorrect password, please try again.');
      expect(credentialsByUser[alice._id]?.isBlocked).to.be.true;
      expect(mailService.sendMail.calledOnce).to.be.true;
      expect(mailService.sendMail.firstCall.args[0]).to.deep.include({
        emailTemplateType: 'suspiciousLoginAttempt',
        usersMails: [alice.email],
      });

      const rightPassword = await tryPassword(PASSWORD);
      expect(rightPassword.error).to.be.instanceOf(BadRequestError);
      expect(rightPassword.error.message).to.match(/disabled as you have entered incorrect/);
      expect(rightPassword.error.message).to.include('[blockedUntil:');
      expect(rightPassword.res.body).to.be.undefined;
    });

    it('lets the owner back in once the lock has run out, and starts the count again', async () => {
      await givePassword(alice);
      Object.assign(credentialsByUser[alice._id] ?? {}, {
        isBlocked: true,
        wrongCredentialCount: 5,
        blockExpiresAt: new Date(Date.now() - 1000),
      });

      const { res, error } = await tryPassword(PASSWORD);

      expect(error).to.be.undefined;
      expect(res.body?.message).to.equal('Fully authenticated');
      expect(credentialsByUser[alice._id]).to.include({
        isBlocked: false,
        wrongCredentialCount: 0,
      });
    });
  });

  describe('accounts that must not get a session', () => {
    it('refuses a disabled account even with the right password', async () => {
      await givePassword(alice);
      disabledUserIds.add(alice._id);
      const token = await initAuth([['password']]);

      const { res, error } = await authenticate(token, {
        method: 'password',
        credentials: { password: PASSWORD },
      });

      expect(error).to.be.instanceOf(ForbiddenError);
      expect(error.message).to.equal(DISABLED_ACCOUNT_SIGN_IN_MESSAGE);
      expect(res.body).to.be.undefined;
    });

    it('answers an account with no password exactly like a wrong password', async () => {
      await givePassword(alice);
      const wrongToken = await initAuth([['password']]);
      const wrong = await authenticate(wrongToken, {
        method: 'password',
        credentials: { password: 'wrong-guess' },
      });
      const noPasswordToken = await initAuth([['password']], mallory.email);
      const noPassword = await authenticate(noPasswordToken, {
        method: 'password',
        credentials: { password: 'wrong-guess' },
      });

      expect(noPassword.error).to.be.instanceOf(BadRequestError);
      expect(noPassword.error.message).to.equal(wrong.error.message);
      expect(noPassword.error.statusCode).to.equal(wrong.error.statusCode);
    });
  });

  describe('CAPTCHA', () => {
    let cloudflare: sinon.SinonStub;
    beforeEach(() => {
      process.env.TURNSTILE_SECRET_KEY = 'turnstile-secret';
      cloudflare = sinon.stub(axios, 'post').resolves({ data: { success: false } });
    });
    afterEach(() => {
      delete process.env.TURNSTILE_SECRET_KEY;
    });

    it('refuses password sign-in before checking the password when Cloudflare rejects the challenge', async () => {
      await givePassword(alice);
      const token = await initAuth([['password']]);
      const credentialLookups = (UserCredentials.findOne as sinon.SinonStub).callCount;

      const { error } = await authenticate(token, {
        method: 'password',
        credentials: { password: PASSWORD },
        'cf-turnstile-response': 'bot-answer',
      });

      expect(error).to.be.instanceOf(UnauthorizedError);
      expect(error.message).to.match(/CAPTCHA/);
      expect(cloudflare.firstCall.args[1]).to.include({
        secret: 'turnstile-secret',
        response: 'bot-answer',
      });
      expect((UserCredentials.findOne as sinon.SinonStub).callCount).to.equal(credentialLookups);
    });

    it('sends no reset email when Cloudflare rejects the forgot-password challenge', async () => {
      const res = makeRes();
      const next = sinon.stub();

      await controller.forgotPasswordEmail(
        fakeRequest({ body: { email: alice.email, 'cf-turnstile-response': 'bot' }, ip: '1.1.1.1' }),
        fakeResponse(res),
        next,
      );

      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError);
      expect(iamService.getUserByEmail.called).to.be.false;
      expect(mailService.sendMail.called).to.be.false;
    });

    it('does not change the password when Cloudflare rejects the reset challenge', async () => {
      await givePassword(alice);
      const before = credentialsByUser[alice._id]?.hashedPassword;
      const res = makeRes();
      const next = sinon.stub();

      await controller.resetPassword(
        fakeRequest({
          body: { currentPassword: PASSWORD, newPassword: 'Brand-New-Pass-7!' },
          user: { userId: alice._id, orgId },
          ip: '1.1.1.1',
        }),
        fakeResponse(res),
        next,
      );

      expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError);
      expect(credentialsByUser[alice._id]?.hashedPassword).to.equal(before);
    });
  });

  describe('forgot password never reveals whether an account exists', () => {
    async function forgot(email: string) {
      const res = makeRes();
      const next = sinon.stub();
      await controller.forgotPasswordEmail(
        fakeRequest({ body: { email }, ip: '1.1.1.1' }),
        fakeResponse(res),
        next,
      );
      return { res, next };
    }

    it('gives a known and an unknown email the same answer, and mails only the known one', async () => {
      const known = await forgot(alice.email);
      const unknown = await forgot('nobody@acme.test');

      expect(known.next.called || unknown.next.called).to.be.false;
      expect(known.res.statusCode).to.equal(200);
      expect(unknown.res.statusCode).to.equal(200);
      expect(unknown.res.body).to.deep.equal(known.res.body);
      expect(mailService.sendMail.calledOnce).to.be.true;
      expect(mailService.sendMail.firstCall.args[0].usersMails).to.deep.equal([alice.email]);
    });

    it('gives the same answer when looking the account up fails', async () => {
      const known = await forgot(alice.email);
      iamService.getUserByEmail.rejects(new Error('iam down'));

      const failed = await forgot(alice.email);

      expect(failed.res.statusCode).to.equal(200);
      expect(failed.res.body).to.deep.equal(known.res.body);
    });
  });

  describe('resetting a password while signed in', () => {
    it('refuses to issue a new token when the account is gone after the change', async () => {
      await givePassword(alice);
      iamService.getUserById.resolves({ statusCode: 404, data: null });
      const res = makeRes();
      const next = sinon.stub();

      await controller.resetPassword(
        fakeRequest({
          body: { currentPassword: PASSWORD, newPassword: 'Brand-New-Pass-7!' },
          user: { userId: alice._id, orgId },
          ip: '1.1.1.1',
        }),
        fakeResponse(res),
        next,
      );

      expect(next.firstCall.args[0]).to.be.instanceOf(NotFoundError);
      expect(next.firstCall.args[0].message).to.equal(SESSION_NO_LONGER_VALID);
      expect(res.body).to.be.undefined;
    });
  });

  describe('asking for a sign-in code', () => {
    it('answers with a plain retry message and stores no code when the account lookup fails', async () => {
      iamService.getUserByEmail.resolves({ statusCode: 503, data: 'unavailable' });
      const create = sinon.stub(UserCredentials, 'create');

      let caught: unknown;
      try {
        await controller.getLoginOtp(
          fakeRequest({ body: { email: alice.email }, ip: '1.1.1.1' }),
          fakeResponse(makeRes()),
        );
      } catch (e) {
        caught = e;
      }

      expect(caught).to.be.instanceOf(InternalServerError);
      expect((caught as Error).message).to.equal(OTP_SEND_FAILED);
      expect(create.called).to.be.false;
      expect(mailService.sendMail.called).to.be.false;
    });

    it('tells the user the code was not sent when the mail service fails', async () => {
      mailService.sendMail.resolves({ statusCode: 500, data: 'smtp down' });
      sinon.stub(UserCredentials, 'create').callsFake((() =>
        Promise.resolve({})) as unknown as typeof UserCredentials.create);

      let caught: unknown;
      try {
        await controller.getLoginOtp(
          fakeRequest({ body: { email: alice.email }, ip: '1.1.1.1' }),
          fakeResponse(makeRes()),
        );
      } catch (e) {
        caught = e;
      }

      expect(caught).to.be.instanceOf(InternalServerError);
      expect((caught as Error).message).to.equal(OTP_SEND_FAILED);
    });
  });

  describe('exchanging an OAuth authorization code', () => {
    let fetchStub: sinon.SinonStub;
    const oauthConfig = {
      clientId: 'o-id',
      clientSecret: 'oauth-client-secret',
      tokenEndpoint: 'https://idp/token',
      userInfoEndpoint: 'https://idp/userinfo',
    };

    function providerAnswers(userInfo: Record<string, unknown> | null) {
      fetchStub.callsFake(async (url: string) => {
        if (url === oauthConfig.tokenEndpoint) {
          return new Response(
            JSON.stringify({ access_token: 'idp-access', id_token: 'idp-id', token_type: 'Bearer', expires_in: 3600 }),
            { status: 200 },
          );
        }
        return userInfo
          ? new Response(JSON.stringify(userInfo), { status: 200 })
          : new Response('denied', { status: 401 });
      });
    }

    async function exchange() {
      (Org.findOne as unknown as sinon.SinonStub).returns(createMockQuery({ _id: orgId }));
      const res = makeRes();
      const next = sinon.stub();
      await controller.exchangeOAuthToken(
        fakeRequest({ body: { code: 'auth-code', provider: 'oauth', redirectUri: 'http://app/cb' }, ip: '1.1.1.1' }),
        fakeResponse(res),
        next,
      );
      return { res, error: next.firstCall?.args[0] as SignInError };
    }

    beforeEach(() => {
      fetchStub = sinon.stub(globalThis, 'fetch');
    });

    it('refuses an unknown person when JIT is off, without creating an account', async () => {
      configService.getConfig.resolves({ data: { ...oauthConfig, enableJit: false } });
      providerAnswers({ email: 'stranger@elsewhere.test' });

      const { res, error } = await exchange();

      expect(error).to.be.instanceOf(NotFoundError);
      expect(jitService.provisionUser.called).to.be.false;
      expect(res.body).to.be.undefined;
    });

    it('creates the account when JIT is on, and never sends the client secret back', async () => {
      configService.getConfig.resolves({ data: { ...oauthConfig, enableJit: true } });
      providerAnswers({ email: 'newcomer@acme.test', name: 'New Person' });
      jitService.provisionUser.resolves({ _id: 'new-id' });

      const { res, error } = await exchange();

      expect(error).to.be.undefined;
      expect(jitService.provisionUser.firstCall.args[0]).to.equal('newcomer@acme.test');
      expect(jitService.provisionUser.firstCall.args[2]).to.equal(orgId);
      expect(res.body).to.deep.equal({
        access_token: 'idp-access',
        id_token: 'idp-id',
        token_type: 'Bearer',
        expires_in: 3600,
      });
      const tokenCall = fetchStub.getCalls().find((c) => c.args[0] === oauthConfig.tokenEndpoint)!;
      expect(String(tokenCall.args[1].body)).to.include('client_secret=oauth-client-secret');
      expect(JSON.stringify(res.body)).to.not.include('oauth-client-secret');
    });

    it('refuses when the provider will not share the user profile', async () => {
      configService.getConfig.resolves({ data: oauthConfig });
      providerAnswers(null);

      const { error } = await exchange();

      expect(error).to.be.instanceOf(UnauthorizedError);
      expect(error.message).to.equal(OAUTH_SIGN_IN_FAILED);
    });

    it('refuses when the profile has no email, username or subject', async () => {
      configService.getConfig.resolves({ data: oauthConfig });
      providerAnswers({ name: 'No Email' });

      const { error } = await exchange();

      expect(error).to.be.instanceOf(BadRequestError);
      expect(error.message).to.equal(PROVIDER_SHARED_NO_EMAIL);
    });
  });

  describe('SAML as a later sign-in step', () => {
    const appConfig = {
      cmBackend: 'http://cm',
      frontendUrl: 'http://app',
      jwtSecret: JWT_SECRET,
      scopedJwtSecret: SCOPED_SECRET,
      cookieSecret: 'saml-cookie-secret',
    };

    // The real callback that runs after passport has checked the IdP's
    // assertion; passport's output (req.user) is the only thing faked.
    function samlCallback(): RequestHandler {
      const container = new Container();
      const bind = (id: string, value: unknown): void => {
        container.bind(id).toConstantValue(value);
      };
      bind('AppConfig', appConfig);
      bind('AuthMiddleware', { scopedTokenValidator: () => sinon.stub() });
      bind('SessionService', sessionService);
      bind('IamService', iamService);
      bind('JitProvisioningService', jitService);
      bind('ConfigurationManagerService', configService);
      bind('Logger', logger);
      bind(
        'SamlController',
        new SamlController(appConfig as never, logger as unknown as Logger),
      );
      const router = createSamlRouter(container);
      const layer = (
        router.stack as unknown as Array<{
          route?: { path: string; stack: Array<{ handle: RequestHandler }> };
        }>
      ).find((l) => l.route?.path === '/signIn/callback');
      const handlers = layer?.route?.stack ?? [];
      return handlers[handlers.length - 1]!.handle;
    }

    async function samlSignsInAs(email: string, sessionToken: string) {
      const res = {
        redirect: sinon.stub(),
        cookie: sinon.stub(),
      };
      const relayState = Buffer.from(JSON.stringify({ orgId, sessionToken })).toString('base64');
      await samlCallback()(
        fakeRequest({ user: { email, orgId }, body: { RelayState: relayState } }),
        res as unknown as Response,
        sinon.stub() as unknown as NextFunction,
      );
      return { redirect: String(res.redirect.firstCall?.args[0]), cookies: res.cookie };
    }

    async function passStepOneAsAlice(steps: string[][] = [['password'], ['samlSso']]) {
      await givePassword(alice);
      configService.getConfig.resolves({ data: { enableJit: true, entryPoint: 'https://idp' } });
      const token = await initAuth(steps);
      const first = await authenticate(token, {
        method: 'password',
        credentials: { password: PASSWORD },
      });
      expect(first.res.body).to.include({ status: 'success', nextStep: 1 });
      return token;
    }

    it("refuses a SAML login for another member after step one checked Alice's password", async () => {
      const token = await passStepOneAsAlice();

      const { redirect, cookies } = await samlSignsInAs(mallory.email, token);

      expect(redirect).to.equal(
        `http://app/login?saml_error=${encodeURIComponent(SIGN_IN_ACCOUNT_CHANGED)}`,
      );
      expect(cookies.called).to.be.false;
      expect(await sessionService.getSession(token)).to.not.equal(null);
    });

    it('refuses an unknown SAML identity after step one before any account could be created', async () => {
      const token = await passStepOneAsAlice();
      const session = await sessionService.getSession(token);
      await sessionService.updateSession({ ...session!, jitConfig: { saml: true } });

      const { redirect, cookies } = await samlSignsInAs('stranger@elsewhere.test', token);

      expect(redirect).to.include(encodeURIComponent(SIGN_IN_ACCOUNT_CHANGED));
      expect(jitService.provisionUser.called).to.be.false;
      expect(cookies.called).to.be.false;
    });

    it('with password or SAML at step one and a code at step two, never issues tokens for another account', async () => {
      const token = await passStepOneAsAlice([['password', 'samlSso'], ['otp']]);

      const { redirect, cookies } = await samlSignsInAs(mallory.email, token);

      expect(redirect).to.include(encodeURIComponent(SIGN_IN_ACCOUNT_CHANGED));
      expect(cookies.called).to.be.false;
      expect(await sessionService.getSession(token)).to.not.equal(null);
    });

    it('refuses, without creating an account, a session past step one that names no account', async () => {
      // A session advanced before step accounts were recorded still says NOT_FOUND.
      const session = await sessionService.createSession({
        userId: 'NOT_FOUND',
        email: alice.email,
        orgId,
        authConfig: orgAuthConfig([['password'], ['samlSso']]).authSteps,
        currentStep: 1,
        jitConfig: { saml: true },
      });
      configuredSteps = [['password'], ['samlSso']];
      configService.getConfig.resolves({ data: { enableJit: true } });

      const { redirect, cookies } = await samlSignsInAs('stranger@elsewhere.test', String(session.token));

      expect(redirect).to.include(encodeURIComponent(SIGN_IN_ACCOUNT_CHANGED));
      expect(jitService.provisionUser.called).to.be.false;
      expect(cookies.called).to.be.false;
    });

    it('still creates the account on a first-step SAML sign-in when the admin turned JIT on', async () => {
      configService.getConfig.resolves({ data: { enableJit: true, entryPoint: 'https://idp' } });
      const token = await initAuth([['samlSso']], '');
      const newcomer: DirectoryUser = {
        _id: new mongoose.Types.ObjectId().toHexString(),
        orgId,
        email: 'newcomer@acme.test',
        fullName: 'New Person',
        hasLoggedIn: false,
      };
      jitService.provisionUser.callsFake(async () => {
        directory[newcomer.email] = newcomer;
        return newcomer;
      });

      try {
        const { redirect, cookies } = await samlSignsInAs(newcomer.email, token);

        expect(jitService.provisionUser.firstCall.args.slice(0, 4)).to.deep.equal([
          newcomer.email,
          { fullName: 'New Person' },
          orgId,
          'saml',
        ]);
        expect(redirect).to.equal('http://app/auth/sign-in/samlSso/success');
        const access = cookies.getCalls().find((c) => c.args[0] === 'accessToken');
        expect(
          (jwt.verify(String(access?.args[1]), JWT_SECRET) as TokenClaims).userId,
        ).to.equal(newcomer._id);
      } finally {
        delete directory[newcomer.email];
      }
    });

    it("signs Alice in when the SAML step is Alice's own account", async () => {
      const token = await passStepOneAsAlice();

      const { redirect, cookies } = await samlSignsInAs(alice.email, token);

      expect(redirect).to.equal('http://app/auth/sign-in/samlSso/success');
      const access = cookies.getCalls().find((c) => c.args[0] === 'accessToken');
      expect(
        (jwt.verify(String(access?.args[1]), JWT_SECRET) as TokenClaims).userId,
      ).to.equal(alice._id);
    });
  });

  describe('email mismatch on Google second check', () => {
    it("refuses a Google token for someone else when checking Alice's account directly", async () => {
      configService.getConfig.resolves({ data: { clientId: 'google-client' } });
      googleSignsInAs(mallory.email);

      let caught: unknown;
      try {
        await controller.authenticateWithGoogle({ ...alice }, 'token', '10.0.0.1');
      } catch (e) {
        caught = e;
      }

      expect(caught).to.be.instanceOf(BadRequestError);
      expect((caught as Error).message).to.equal(EMAIL_MISMATCH);
    });
  });
});
