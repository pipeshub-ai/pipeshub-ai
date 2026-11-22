/**
 * Org-settings, team and service-account audit lines over HTTP: one `audit`
 * record per change (actor, target, org, outcome, reason; `team_id`, `key_id`
 * and `scopes` where they apply), and no email, name or key in any record.
 *
 * Served the way app.ts mounts the routers (request context, access line, JSON
 * body, router, error middleware) with the real org / teams / service-accounts
 * / service-tokens routers, controllers and services, AuthMiddleware +
 * AuthTokenService (real JWTs) and Logger. Faked: repositories (in memory),
 * the event producer, the OAuth token store, and the Python team service (a
 * real HTTP server, FakeBackend). Only records of this file's requests count.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import winston from 'winston';
import sharp from 'sharp';
import type { AddressInfo } from 'net';
import type { Server } from 'http';
import mongoose from 'mongoose';
import { Container } from 'inversify';
import { createOrgRouter } from '../../../src/modules/user_management/routes/org.routes';
import { createTeamsRouter } from '../../../src/modules/user_management/routes/teams.routes';
import { createServiceAccountsRouter } from '../../../src/modules/user_management/routes/service-accounts.routes';
import { createServiceTokenRouter } from '../../../src/modules/oauth_provider/routes/service-token.routes';
import { OrgController } from '../../../src/modules/user_management/controller/org.controller';
import { TeamsController } from '../../../src/modules/user_management/controller/teams.controller';
import { ServiceAccountsController } from '../../../src/modules/user_management/controller/service-accounts.controller';
import { ServiceAccountsService } from '../../../src/modules/user_management/services/service-accounts.service';
import { ServiceTokenController } from '../../../src/modules/oauth_provider/controller/service-token.controller';
import { ServiceTokenService } from '../../../src/modules/oauth_provider/services/service-token.service';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { AuthTokenService } from '../../../src/libs/services/authtoken.service';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { requestContextMiddleware } from '../../../src/libs/middlewares/request.context';
import { accessLog } from '../../../src/libs/middlewares/access-log.middleware';
import { Logger } from '../../../src/libs/services/logger.service';
import { authJwtGenerator } from '../../../src/libs/utils/createJwt';
import { UniqueConstraintError } from '../../../src/libs/repositories/errors';
import * as userRepo from '../../../src/libs/repositories/user';
import * as groupRepo from '../../../src/libs/repositories/group';
import * as orgRepo from '../../../src/libs/repositories/org';
import * as activityRepo from '../../../src/libs/repositories/activity';
import * as pictureRepo from '../../../src/libs/repositories/picture';
import * as oauthAppRepo from '../../../src/libs/repositories/oauth-app';
import * as seam from '../../../src/libs/services/structured-logging';
import { FakeBackend, Reply } from '../tokens_manager/routes/connectors-http-harness';
import { registerUpstreams } from '../../../src/libs/http/upstream-call';
import { registerAppUpstreams } from '../../../src/modules/tokens_manager/config/config';

const JWT_SECRET = 'n4c-audit-jwt-secret';
const SCOPED_SECRET = 'n4c-audit-scoped-secret';
const oid = () => new mongoose.Types.ObjectId().toHexString();
const orgId = oid();
const otherOrg = oid();
/** Every request this file makes carries a request id with this prefix; only those lines are captured. */
const OUR_REQUESTS = 'n4ctest';
let seq = 0;
const TEAM_ID = '3f2b8c1e-9a4d-4c7b-8e2f-1a2b3c4d5e6f';
const ALL_TEAM = `all_${orgId}`;
const teamPath = (id: string) => `/api/v1/entity/team/${id}`;
const RAW_KEY = 'raw-secret-key-value-0123456789';
const SLUG = 'ingest-bot';
const SA_NAME = 'Ingest Bot';
const SA_DESCRIPTION = 'Feeds the nightly import';
const KEY_NAME = 'nightly-import-key';
const TEAM_NAME = 'Platform Guild';

type Row = Record<string, any>;

describe('org, team and service-account audit lines (HTTP)', function () {
  this.timeout(20000);
  const backend = new FakeBackend();
  let server: Server;
  let baseUrl: string;
  let records: Row[];
  let users: Row[];
  let ada: Row; let max: Row; let mia: Row;
  let keys: Row[];
  let logo: Row | null;
  let publishFails: boolean;
  let revokeFails: boolean;
  let createRace: boolean;
  let restoreInFlight: boolean;
  let activeKeys: number | undefined;

  const root = () => (Logger.getInstance() as any).logger as winston.Logger;
  const token = (u: Row) => authJwtGenerator(JWT_SECRET, u.email, u._id, u.orgId, u.fullName, 'business', u.role);
  const live = (o: string, id: string) => users.find((u) => u._id === id && u.orgId === o && !u.isDeleted) ?? null;
  const liveSa = (o: string, id: string) => users.find((u) => u._id === id && u.orgId === o && u.kind === 'service' && !u.isDeleted) ?? null;

  /** The audit records, reduced to what each test pins. */
  const audits = () =>
    records
      .filter((r) => r.service === 'audit')
      .map((r) => {
        const out: Row = { event: r.message, outcome: r.outcome };
        for (const key of ['reason', 'actor', 'target', 'team_id', 'key_id', 'scopes']) if (r[key] != null) out[key] = r[key];
        return out;
      });
  const PRIVATE = () => [ada.email, max.email, mia.email, ada.fullName, max.fullName, mia.fullName,
    SA_NAME, SA_DESCRIPTION, KEY_NAME, TEAM_NAME, RAW_KEY, 'phsvc_'];
  const expectNoPrivateData = (extra: string[] = []) => {
    for (const r of records) {
      const raw = JSON.stringify(r);
      for (const p of [...PRIVATE(), ...extra]) expect(raw, `${r.message} leaks ${p}`).to.not.include(p);
    }
  };

  async function send(method: string, path: string, who: Row, init: { body?: unknown; form?: FormData } = {}) {
    records.length = 0;
    const headers: Record<string, string> = { 'x-request-id': `${OUR_REQUESTS}-${++seq}`, authorization: `Bearer ${token(who)}` };
    if (init.body !== undefined) headers['content-type'] = 'application/json';
    const res = await fetch(`${baseUrl}${path}`, {
      method,
      headers,
      body: init.form ?? (init.body === undefined ? undefined : JSON.stringify(init.body)),
      signal: AbortSignal.timeout(5000),
    });
    const text = await res.text();
    let parsed: any = text;
    try { parsed = text ? JSON.parse(text) : undefined; } catch { /* plain text */ }
    // The access line is written on 'close'; wait for it, bounded, so every test sees whole requests.
    const deadline = Date.now() + 2000;
    while (!records.some((r) => r.message === 'http.request.end')) {
      if (Date.now() > deadline) expect.fail(`no http.request.end; saw ${records.map((r) => r.message).join(', ')}`);
      await new Promise((resolve) => setImmediate(resolve));
    }
    return { status: res.status, body: parsed };
  }
  const call = (method: string, path: string, who: Row, body?: unknown) => send(method, path, who, { body });

  before(async () => { await backend.start(); });
  after(async () => { await backend.stop(); });

  beforeEach(async () => {
    records = [];
    backend.reset();
    logo = { logo: 'bG9nbw==' };
    publishFails = false;
    revokeFails = false;
    createRace = false;
    restoreInFlight = false;
    activeKeys = undefined;
    root().level = 'debug';
    sinon.stub(winston.Logger.prototype, 'log').callsFake(function (this: unknown, info: Row) {
      if (String(info.request_id).startsWith(OUR_REQUESTS)) records.push(info);
      return this;
    } as any);
    const mk = (fullName: string, email: string, role: string, extra: Row = {}): Row =>
      ({ _id: oid(), orgId, email, fullName, role, hasLoggedIn: true, isDeleted: false, kind: 'user', ...extra });
    ada = mk('Ada Lovelace', 'ada@acme.test', 'admin');
    max = mk('Max Planck', 'max@acme.test', 'member');
    mia = mk('Mia Hamm', 'mia@acme.test', 'member');
    users = [ada, max, mia];
    keys = [];

    const fake = (name: string, impl: Row): never =>
      new Proxy(impl, {
        get: (t, k: string) => {
          if (k in t) return t[k];
          if (k === 'then') return undefined;
          return () => { throw new Error(`fake ${name} repository has no ${k}`); };
        },
      }) as never;

    sinon.stub(userRepo, 'getUserRepository').returns(fake('user', {
      getLiveById: async (id: string) => users.find((u) => u._id === id && !u.isDeleted) ?? null,
      getRoleSnapshot: async (o: string, id: string) => { const u = live(o, id); return u ? { role: u.role, kind: u.kind } : null; },
      getByEmail: async (e: string) => users.find((u) => u.email === e) ?? null,
      create: async (o: string, f: Row) => {
        if (createRace) throw new UniqueConstraintError('users', 'email');
        const u = { _id: oid(), orgId: o, isDeleted: false, ...f }; users.push(u); return { ...u };
      },
      restoreServiceAccount: async (o: string, id: string, f: Row) => {
        const u = users.find((x) => x._id === id && x.orgId === o && x.isDeleted);
        if (!u) return null;
        Object.assign(u, { fullName: f.fullName, description: f.description, restoreOpId: f.restoreOpId, isDeleted: false, isDisabled: true });
        return { ...u };
      },
      abortServiceAccountRestore: async (id: string) => { const u = users.find((x) => x._id === id); if (u) u.isDeleted = true; },
      finishServiceAccountRestore: async (id: string) => { const u = users.find((x) => x._id === id && !x.isDeleted); if (!u) return null; u.isDisabled = false; return { ...u }; },
      getServiceAccount: async (o: string, id: string) => { const u = liveSa(o, id); return u ? { ...u } : null; },
      getLiveFields: async (o: string, id: string) => liveSa(o, id),
      enableServiceAccount: async (o: string, id: string) => { const u = liveSa(o, id); if (!u || restoreInFlight) return false; u.isDisabled = false; return true; },
      updateServiceAccount: async (o: string, id: string, c: Row) => { const u = liveSa(o, id); if (!u) return false; Object.assign(u, c); return true; },
      markDeleted: async (o: string, id: string) => { const u = live(o, id); if (u) u.isDeleted = true; },
    }));
    sinon.stub(groupRepo, 'getGroupRepository').returns(fake('group', {
      addToEveryone: async () => undefined,
      removeFromAll: async () => undefined,
    }));
    sinon.stub(orgRepo, 'getOrgRepository').returns(fake('org', {
      getLive: async (o: string) => (o === orgId ? { _id: orgId, registeredName: 'Acme Ltd', isDeleted: false } : null),
      update: async (o: string, f: Row) => ({ _id: o, ...f }),
      softDelete: async (o: string) => ({ _id: o, isDeleted: true }),
      setLogo: async (_o: string, l: string) => { logo = { logo: l }; },
      removeLogo: async (o: string) => { const had = logo; logo = null; return had ? { _id: o } : null; },
      setOnboardingStatus: async (o: string, s: string) => ({ _id: o, onBoardingStatus: s }),
    }));
    sinon.stub(activityRepo, 'getActivityRepository').returns(fake('activity', { latestSessionEnding: async () => null, latestPasswordChange: async () => null }));
    sinon.stub(pictureRepo, 'getPictureRepository').returns(fake('picture', { listSet: async () => [] }));
    const tokenApp = { clientId: `svc-system:${orgId}` };
    sinon.stub(oauthAppRepo, 'getOAuthAppRepository').returns(fake('oauth-app', {
      findByClientId: async () => tokenApp,
      listClientIdsActingAs: async () => [],
    }));

    const config: Row = { jwtSecret: JWT_SECRET, scopedJwtSecret: SCOPED_SECRET, connectorBackend: backend.url, cmBackend: backend.url, frontendUrl: 'http://app.n4c.test', maxOAuthClientRequestsPerMinute: 10000 };
    registerAppUpstreams(config);
    const logger = Logger.getInstance({ service: 'UserManagement' });
    const events = { start: async () => undefined, stop: async () => undefined, publishEvent: async () => { if (publishFails) throw new Error('outbox down'); } };
    const oauthTokens = {
      generateTokens: async () => { const t = { accessTokenId: oid(), accessToken: RAW_KEY }; keys.push({ id: t.accessTokenId, live: true }); return t; },
      countActiveAccessTokensForUser: async () => activeKeys ?? keys.filter((t) => t.live).length,
      revokeAccessTokenById: async (id: string) => { const t = keys.find((x) => x.id === id && x.live); if (t) t.live = false; return Boolean(t); },
      revokeEveryTokenForUser: async () => { if (revokeFails) throw new Error('token store down'); },
      revokeDelegatedTokensForActor: async () => 0,
      revokeAllTokensForApp: async () => undefined,
    };
    const configService = { getMcpScopes: async () => ['kb:read', 'semantic:write', 'agent:execute'] };
    const scopeValidator = { validateScopesForApp: () => undefined, getScopeDefinitions: () => [] };
    const delegation = { revokeAllForServiceAccount: async () => undefined };
    const tokenService = new ServiceTokenService(...([logger, { encrypt: (s: string) => s }, configService, oauthTokens, scopeValidator, delegation] as unknown as ConstructorParameters<typeof ServiceTokenService>));
    const accounts = new ServiceAccountsService(...([events] as unknown as ConstructorParameters<typeof ServiceAccountsService>));
    accounts.setTokenRevoker(tokenService);
    const container = new Container();
    container.bind('Logger').toConstantValue(logger);
    container.bind('AppConfig').toConstantValue(config);
    container.bind('AuthMiddleware').toConstantValue(new AuthMiddleware(logger, new AuthTokenService(JWT_SECRET, SCOPED_SECRET)));
    container.bind('OrgController').toConstantValue(new OrgController(...([config, {}, logger, events] as unknown as ConstructorParameters<typeof OrgController>)));
    container.bind('TeamsController').toConstantValue(new TeamsController(config as never, logger));
    container.bind('ServiceAccountsController').toConstantValue(new ServiceAccountsController(accounts));
    container.bind('ServiceTokenController').toConstantValue(new ServiceTokenController(tokenService, scopeValidator as never));

    const app = express();
    app.use(requestContextMiddleware);
    app.use(accessLog());
    app.use(express.json());
    app.use('/api/v1/org', createOrgRouter(container));
    app.use('/api/v1/teams', createTeamsRouter(container));
    app.use('/api/v1/service-accounts', createServiceAccountsRouter(container));
    app.use('/api/v1/service-tokens', createServiceTokenRouter(container));
    app.use(ErrorMiddleware.handleError());
    server = await new Promise<Server>((resolve) => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
    baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });

  afterEach(async () => {
    sinon.restore();
    registerUpstreams({});
    await new Promise((resolve) => server.close(resolve));
  });

  describe('org settings', () => {
    it('O1 update → org.update success, the org is org_id', async () => {
      expect((await call('PUT', '/api/v1/org', ada, { registeredName: 'Acme Holdings' })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'org.update', outcome: 'success', actor: ada._id }]);
      expect(records.find((r) => r.service === 'audit')!.org_id).to.equal(orgId);
      expectNoPrivateData(['Acme Holdings']);
    });

    it('O2 delete → org.delete success', async () => {
      expect((await call('DELETE', '/api/v1/org', ada)).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'org.delete', outcome: 'success', actor: ada._id }]);
    });

    it('O3 upload a logo → org.logo.update reason=set', async () => {
      const png = await sharp({ create: { width: 2, height: 2, channels: 3, background: '#336699' } }).png().toBuffer();
      const form = new FormData();
      form.append('file', new Blob([new Uint8Array(png)], { type: 'image/png' }), 'logo.png');
      expect((await send('PUT', '/api/v1/org/logo', ada, { form })).status).to.equal(201);
      expect(audits()).to.deep.equal([{ event: 'org.logo.update', outcome: 'success', reason: 'set', actor: ada._id }]);
    });

    it('O4 remove the logo → org.logo.update reason=removed', async () => {
      expect((await call('DELETE', '/api/v1/org/logo', ada)).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'org.logo.update', outcome: 'success', reason: 'removed', actor: ada._id }]);
    });

    it('O4b [guard] remove when there is no logo → 404, no line', async () => {
      logo = null;
      expect((await call('DELETE', '/api/v1/org/logo', ada)).status).to.equal(404);
      expect(audits()).to.deep.equal([]);
    });

    it('O5 onboarding notConfigured → not_configured; configured → configured; skipped → skipped', async () => {
      expect((await call('PUT', '/api/v1/org/onboarding-status', ada, { status: 'configured' })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'org.onboarding.update', outcome: 'success', reason: 'configured', actor: ada._id }]);
      expect((await call('PUT', '/api/v1/org/onboarding-status', ada, { status: 'notConfigured' })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'org.onboarding.update', outcome: 'success', reason: 'not_configured', actor: ada._id }]);
      expect((await call('PUT', '/api/v1/org/onboarding-status', ada, { status: 'skipped' })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'org.onboarding.update', outcome: 'success', reason: 'skipped', actor: ada._id }]);
    });

    it('O6 [guard] a member is turned away by userAdminCheck → 403, no line', async () => {
      expect((await call('PUT', '/api/v1/org', max, { registeredName: 'Mine' })).status).to.equal(403);
      expect((await call('DELETE', '/api/v1/org', max)).status).to.equal(403);
      expect(audits()).to.deep.equal([]);
    });
  });

  describe('teams', () => {
    const team = (id = TEAM_ID, members: Row[] = []) => ({ id, name: TEAM_NAME, orgId, memberCount: members.length, members });
    const reply = (method: string, path: string, r: Reply) => backend.on(method, path, r);
    const created = (id = TEAM_ID): Reply => ({ status: 200, body: { status: 'success', data: team(id) } });
    const updated = (id = TEAM_ID): Reply => ({ status: 200, body: { status: 'success', team: team(id) } });
    const refused = (status: number, detail: string): Reply => ({ status, body: { detail } });

    it('T1 create with members → team.create, then team.member.add per member; the creator is not listed', async () => {
      reply('POST', '/api/v1/entity/team', created());
      const body = { name: TEAM_NAME, userRoles: [{ userId: ada._id, role: 'OWNER' }, { userId: max._id, role: 'READER' }, { userId: mia._id, role: 'WRITER' }, { userId: max._id, role: 'READER' }] };
      expect((await call('POST', '/api/v1/teams', ada, body)).status).to.equal(201);
      expect(audits()).to.deep.equal([
        { event: 'team.create', outcome: 'success', actor: ada._id, target: TEAM_ID },
        { event: 'team.member.add', outcome: 'success', actor: ada._id, target: max._id, team_id: TEAM_ID },
        { event: 'team.member.add', outcome: 'success', actor: ada._id, target: mia._id, team_id: TEAM_ID },
      ]);
      expectNoPrivateData();
    });

    it('T1a member entries the route schema drops (no role, legacy userIds) get no line', async () => {
      reply('POST', '/api/v1/entity/team', created());
      const body = { name: TEAM_NAME, userIds: [mia._id], userRoles: [{ userId: max._id }, { userId: mia._id, role: 'READER' }] };
      expect((await call('POST', '/api/v1/teams', ada, body)).status).to.equal(201);
      expect(audits()).to.deep.equal([
        { event: 'team.create', outcome: 'success', actor: ada._id, target: TEAM_ID },
        { event: 'team.member.add', outcome: 'success', actor: ada._id, target: mia._id, team_id: TEAM_ID },
      ]);
    });

    it('T1d the team service answers without a team id → team.create success reason=no_team_id, no target, no member lines', async () => {
      reply('POST', '/api/v1/entity/team', { status: 200, body: { status: 'success', data: { name: TEAM_NAME, orgId } } });
      expect((await call('POST', '/api/v1/teams', ada, { name: TEAM_NAME, userRoles: [{ userId: max._id, role: 'READER' }] })).status).to.equal(201);
      expect(audits()).to.deep.equal([{ event: 'team.create', outcome: 'success', reason: 'no_team_id', actor: ada._id }]);
    });

    it('T1b [guard] the team service fails → 500, no line', async () => {
      reply('POST', '/api/v1/entity/team', refused(500, 'Failed to create team'));
      expect((await call('POST', '/api/v1/teams', ada, { name: TEAM_NAME, userIds: [max._id] })).status).to.equal(500);
      expect(audits()).to.deep.equal([]);
    });

    it('T1c a taken name (409) → team.create failure name_taken', async () => {
      reply('POST', '/api/v1/entity/team', refused(409, 'A team with this name already exists'));
      expect((await call('POST', '/api/v1/teams', ada, { name: TEAM_NAME })).status).to.equal(409);
      expect(audits()).to.deep.equal([{ event: 'team.create', outcome: 'failure', reason: 'name_taken', actor: ada._id }]);
    });

    it('T2 rename → team.update success', async () => {
      reply('PUT', teamPath(TEAM_ID), updated());
      expect((await call('PUT', `/api/v1/teams/${TEAM_ID}`, ada, { name: 'Renamed Guild' })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'team.update', outcome: 'success', actor: ada._id, target: TEAM_ID }]);
      expectNoPrivateData(['Renamed Guild']);
    });

    it('T2b an empty description is a change → team.update success', async () => {
      reply('PUT', teamPath(TEAM_ID), updated());
      expect((await call('PUT', `/api/v1/teams/${TEAM_ID}`, ada, { description: '' })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'team.update', outcome: 'success', actor: ada._id, target: TEAM_ID }]);
    });

    it('T2c a membership-only PUT writes no team.update line', async () => {
      reply('PUT', teamPath(TEAM_ID), updated());
      expect((await call('PUT', `/api/v1/teams/${TEAM_ID}`, ada, { removeUserIds: [max._id] })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'team.member.remove', outcome: 'success', actor: ada._id, target: max._id, team_id: TEAM_ID }]);
    });

    it('T3 remove, role change, add, in the order the team service writes; entries it skips are skipped', async () => {
      reply('PUT', teamPath(TEAM_ID), updated());
      const body = {
        addUserRoles: [{ userId: mia._id, role: 'READER' }, { role: 'READER' }],
        removeUserIds: [max._id],
        updateUserRoles: [{ userId: ada._id, role: 'OWNER' }, { userId: mia._id }],
      };
      expect((await call('PUT', `/api/v1/teams/${TEAM_ID}`, ada, body)).status).to.equal(200);
      expect(audits()).to.deep.equal([
        { event: 'team.member.remove', outcome: 'success', actor: ada._id, target: max._id, team_id: TEAM_ID },
        { event: 'team.member.role.change', outcome: 'success', actor: ada._id, target: ada._id, team_id: TEAM_ID },
        { event: 'team.member.add', outcome: 'success', actor: ada._id, target: mia._id, team_id: TEAM_ID },
      ]);
    });

    it('T3b the same user listed twice gets one line', async () => {
      reply('PUT', teamPath(TEAM_ID), updated());
      expect((await call('PUT', `/api/v1/teams/${TEAM_ID}`, ada, { removeUserIds: [max._id, max._id] })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'team.member.remove', outcome: 'success', actor: ada._id, target: max._id, team_id: TEAM_ID }]);
    });

    it('T4 not the owner (403) → one team.update failure not_owner, no membership line', async () => {
      reply('PUT', teamPath(TEAM_ID), refused(403, 'User does not have permission to update this team'));
      expect((await call('PUT', `/api/v1/teams/${TEAM_ID}`, max, { name: 'Mine now', addUserIds: [mia._id] })).status).to.equal(403);
      expect(audits()).to.deep.equal([{ event: 'team.update', outcome: 'failure', reason: 'not_owner', actor: max._id, target: TEAM_ID }]);
    });

    it('T5 a team the caller cannot see (404) → team.update failure not_found', async () => {
      reply('PUT', teamPath(TEAM_ID), refused(404, 'This team was not found'));
      expect((await call('PUT', `/api/v1/teams/${TEAM_ID}`, max, { name: 'X' })).status).to.equal(404);
      expect(audits()).to.deep.equal([{ event: 'team.update', outcome: 'failure', reason: 'not_found', actor: max._id, target: TEAM_ID }]);
    });

    it('T5b a taken name on rename (409) → team.update failure name_taken', async () => {
      reply('PUT', teamPath(TEAM_ID), refused(409, 'A team with this name already exists'));
      expect((await call('PUT', `/api/v1/teams/${TEAM_ID}`, ada, { name: 'Taken' })).status).to.equal(409);
      expect(audits()).to.deep.equal([{ event: 'team.update', outcome: 'failure', reason: 'name_taken', actor: ada._id, target: TEAM_ID }]);
    });

    it('T6 delete → team.delete success', async () => {
      reply('DELETE', teamPath(TEAM_ID), { status: 200, body: { status: 'success', message: 'Team deleted successfully' } });
      expect((await call('DELETE', `/api/v1/teams/${TEAM_ID}`, ada)).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'team.delete', outcome: 'success', actor: ada._id, target: TEAM_ID }]);
    });

    it('T6a delete a team the caller cannot see (404) → team.delete failure not_found; 403 → not_owner', async () => {
      reply('DELETE', teamPath(TEAM_ID), refused(404, 'This team was not found'));
      expect((await call('DELETE', `/api/v1/teams/${TEAM_ID}`, max)).status).to.equal(404);
      expect(audits()).to.deep.equal([{ event: 'team.delete', outcome: 'failure', reason: 'not_found', actor: max._id, target: TEAM_ID }]);
      reply('DELETE', teamPath(TEAM_ID), refused(403, 'User does not have permission to delete this team'));
      expect((await call('DELETE', `/api/v1/teams/${TEAM_ID}`, max)).status).to.equal(403);
      expect(audits()).to.deep.equal([{ event: 'team.delete', outcome: 'failure', reason: 'not_owner', actor: max._id, target: TEAM_ID }]);
    });

    it('T6b delete the All team (403) → team.delete failure protected_team, target = the All team', async () => {
      reply('DELETE', teamPath(ALL_TEAM), refused(403, 'The default All team cannot be deleted'));
      expect((await call('DELETE', `/api/v1/teams/${ALL_TEAM}`, ada)).status).to.equal(403);
      expect(audits()).to.deep.equal([{ event: 'team.delete', outcome: 'failure', reason: 'protected_team', actor: ada._id, target: ALL_TEAM }]);
    });

    it('T6c the All team can be renamed and its members changed: its id is shown, never invalid_id', async () => {
      reply('PUT', teamPath(ALL_TEAM), updated(ALL_TEAM));
      expect((await call('PUT', `/api/v1/teams/${ALL_TEAM}`, ada, { name: 'Everyone at Acme', addUserRoles: [{ userId: mia._id, role: 'READER' }] })).status).to.equal(200);
      expect(audits()).to.deep.equal([
        { event: 'team.update', outcome: 'success', actor: ada._id, target: ALL_TEAM },
        { event: 'team.member.add', outcome: 'success', actor: ada._id, target: mia._id, team_id: ALL_TEAM },
      ]);
    });

    it('T6d a non-owner PUT on the All team (403) → team.update failure not_owner, target = the All team', async () => {
      reply('PUT', teamPath(ALL_TEAM), refused(403, 'User does not have permission to update this team'));
      expect((await call('PUT', `/api/v1/teams/${ALL_TEAM}`, max, { name: 'X' })).status).to.equal(403);
      expect(audits()).to.deep.equal([{ event: 'team.update', outcome: 'failure', reason: 'not_owner', actor: max._id, target: ALL_TEAM }]);
    });

    it('T7 [guard] the team service refuses an input (400) → no line', async () => {
      reply('PUT', teamPath(TEAM_ID), refused(400, 'Cannot remove all owners from the team. At least one owner must remain.'));
      expect((await call('PUT', `/api/v1/teams/${TEAM_ID}`, ada, { removeUserIds: [ada._id] })).status).to.equal(400);
      expect(audits()).to.deep.equal([]);
    });

    it('T8 [guard] the team service drops the connection → no audit line, one http.request.error, no controller ERROR line', async () => {
      reply('PUT', teamPath(TEAM_ID), 'drop');
      const res = await call('PUT', `/api/v1/teams/${TEAM_ID}`, ada, { name: 'X' });
      expect(res.status).to.be.at.least(500);
      expect(audits()).to.deep.equal([]);
      expect(records.filter((r) => r.message === 'http.request.error')).to.have.length(1);
      expect(records.filter((r) => /team/i.test(String(r.message)) && r.service !== 'audit')).to.deep.equal([]);
    });

    it('T10 [guard] a member value that is not a user id is a 400 before the team service; no line, never printed', async () => {
      expect((await call('PUT', `/api/v1/teams/${TEAM_ID}`, ada, { removeUserIds: [mia.email] })).status).to.equal(400);
      expect(audits()).to.deep.equal([]);
      expect(backend.calls).to.have.length(0);
    });
  });

  describe('service accounts', () => {
    const SA = '/api/v1/service-accounts';
    const create = () => call('POST', SA, ada, { slug: SLUG, fullName: SA_NAME, description: SA_DESCRIPTION });
    const saId = () => users.find((u) => u.kind === 'service')!._id;
    const patch = (body: Row, id = saId()) => call('PATCH', `${SA}/${id}`, ada, body);

    it('S1 create → service_account.create success, target = the account', async () => {
      expect((await create()).status).to.equal(201);
      expect(audits()).to.deep.equal([{ event: 'service_account.create', outcome: 'success', actor: ada._id, target: saId() }]);
      expectNoPrivateData([SLUG]);
    });

    it('S1b a deleted account brought back by name → service_account.create reason=restore', async () => {
      await create();
      const id = saId();
      users.find((u) => u._id === id)!.isDeleted = true;
      expect((await create()).status).to.equal(201);
      expect(audits()).to.deep.equal([{ event: 'service_account.create', outcome: 'success', reason: 'restore', actor: ada._id, target: id }]);
    });

    it('S1c [guard] a restore whose old keys cannot be revoked is undone → 500, no success line', async () => {
      await create();
      users.find((u) => u._id === saId())!.isDeleted = true;
      revokeFails = true;
      expect((await create()).status).to.equal(500);
      expect(audits()).to.deep.equal([]);
    });

    it('S2 a taken name → service_account.create failure name_taken', async () => {
      await create();
      expect((await create()).status).to.equal(409);
      expect(audits()).to.deep.equal([{ event: 'service_account.create', outcome: 'failure', reason: 'name_taken', actor: ada._id }]);
    });

    it('S2b two creates race on the unique index → name_taken', async () => {
      createRace = true;
      expect((await create()).status).to.equal(409);
      expect(audits()).to.deep.equal([{ event: 'service_account.create', outcome: 'failure', reason: 'name_taken', actor: ada._id }]);
    });

    it('S3 profile, disable, enable → service_account.update with that reason', async () => {
      await create();
      const id = saId();
      expect((await patch({ fullName: 'Renamed Bot' })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'service_account.update', outcome: 'success', reason: 'profile', actor: ada._id, target: id }]);
      expectNoPrivateData(['Renamed Bot']);
      expect((await patch({ isDisabled: true })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'service_account.update', outcome: 'success', reason: 'disable', actor: ada._id, target: id }]);
      expect((await patch({ isDisabled: false })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'service_account.update', outcome: 'success', reason: 'enable', actor: ada._id, target: id }]);
    });

    it('S3d profile and disable in one PATCH → two lines', async () => {
      await create();
      expect((await patch({ description: 'New text', isDisabled: true })).status).to.equal(200);
      expect(audits()).to.deep.equal([
        { event: 'service_account.update', outcome: 'success', reason: 'profile', actor: ada._id, target: saId() },
        { event: 'service_account.update', outcome: 'success', reason: 'disable', actor: ada._id, target: saId() },
      ]);
    });

    it('S3e enable while a restore is in flight → service_account.update failure restoring', async () => {
      await create();
      restoreInFlight = true;
      expect((await patch({ isDisabled: false })).status).to.equal(409);
      expect(audits()).to.deep.equal([{ event: 'service_account.update', outcome: 'failure', reason: 'restoring', actor: ada._id, target: saId() }]);
    });

    it('S4 delete → service_account.delete success', async () => {
      await create();
      const id = saId();
      expect((await call('DELETE', `${SA}/${id}`, ada)).status).to.equal(204);
      expect(audits()).to.deep.equal([{ event: 'service_account.delete', outcome: 'success', actor: ada._id, target: id }]);
    });

    it('S5 an account of another org reads as missing → not_found on update and delete', async () => {
      const foreign = { _id: oid(), orgId: otherOrg, email: 'svc@other.test', kind: 'service', role: 'member', isDeleted: false };
      users.push(foreign);
      expect((await patch({ fullName: 'Mine' }, foreign._id)).status).to.equal(404);
      expect(audits()).to.deep.equal([{ event: 'service_account.update', outcome: 'failure', reason: 'not_found', actor: ada._id, target: foreign._id }]);
      expect((await call('DELETE', `${SA}/${foreign._id}`, ada)).status).to.equal(404);
      expect(audits()).to.deep.equal([{ event: 'service_account.delete', outcome: 'failure', reason: 'not_found', actor: ada._id, target: foreign._id }]);
    });

    it('S6 the event cannot be published after the write → 500, the create line is already written (the account exists)', async () => {
      publishFails = true;
      expect((await create()).status).to.equal(500);
      expect(audits()).to.deep.equal([{ event: 'service_account.create', outcome: 'success', actor: ada._id, target: saId() }]);
    });

    it('S7 [guard] a member is turned away by userAdminCheck → 403, no line', async () => {
      expect((await call('POST', SA, max, { slug: SLUG, fullName: SA_NAME })).status).to.equal(403);
      expect(audits()).to.deep.equal([]);
    });
  });

  describe('service-account keys', () => {
    const K = '/api/v1/service-tokens';
    let sa: string;
    const mint = (scopes: string[] = ['kb:read'], who = ada) => call('POST', K, who, { serviceAccountId: sa, name: KEY_NAME, scopes });
    const revoke = (keyId: string) => call('DELETE', `${K}/${keyId}?serviceAccountId=${sa}`, ada);

    beforeEach(async () => {
      await call('POST', '/api/v1/service-accounts', ada, { slug: SLUG, fullName: SA_NAME });
      sa = users.find((u) => u.kind === 'service')!._id;
    });

    it('K1 mint → service_account.token.create with key_id and sorted scopes; the key itself is in no record', async () => {
      const res = await mint(['semantic:write', 'kb:read', 'kb:read']);
      expect(res.status).to.equal(201);
      expect(res.body.token.accessToken).to.include(RAW_KEY);
      expect(audits()).to.deep.equal([{ event: 'service_account.token.create', outcome: 'success', actor: ada._id, target: sa, key_id: res.body.token.id, scopes: 'kb:read,semantic:write' }]);
      expectNoPrivateData([SLUG]);
    });

    it('K2 a disabled account → token.create failure account_disabled', async () => {
      users.find((u) => u._id === sa)!.isDisabled = true;
      expect((await mint()).status).to.equal(400);
      expect(audits()).to.deep.equal([{ event: 'service_account.token.create', outcome: 'failure', reason: 'account_disabled', actor: ada._id, target: sa }]);
    });

    it('K3 the write scope → token.create failure scope_denied', async () => {
      expect((await mint(['kb:read', 'agent:execute'])).status).to.equal(400);
      expect(audits()).to.deep.equal([{ event: 'service_account.token.create', outcome: 'failure', reason: 'scope_denied', actor: ada._id, target: sa }]);
    });

    it('K3b [guard] a scope the instance does not offer is an input error → no line', async () => {
      expect((await mint(['nope:read'])).status).to.equal(400);
      expect(audits()).to.deep.equal([]);
    });

    it('K4 over the active-key limit → token.create failure token_limit, and the new key is revoked', async () => {
      activeKeys = 51;
      expect((await mint()).status).to.equal(400);
      expect(audits()).to.deep.equal([{ event: 'service_account.token.create', outcome: 'failure', reason: 'token_limit', actor: ada._id, target: sa }]);
      expect(keys.map((k) => k.live)).to.deep.equal([false]);
    });

    it('K5 revoke → token.revoke success with key_id', async () => {
      const keyId = (await mint()).body.token.id;
      expect((await revoke(keyId)).status).to.equal(204);
      expect(audits()).to.deep.equal([{ event: 'service_account.token.revoke', outcome: 'success', actor: ada._id, target: sa, key_id: keyId }]);
    });

    it('K5b revoke a key that does not exist → token.revoke failure not_found, with the key_id asked for', async () => {
      const missing = oid();
      expect((await revoke(missing)).status).to.equal(404);
      expect(audits()).to.deep.equal([{ event: 'service_account.token.revoke', outcome: 'failure', reason: 'not_found', actor: ada._id, target: sa, key_id: missing }]);
    });

    it('K5c mint for an account of another org → token.create failure not_found', async () => {
      const foreign = oid();
      users.push({ _id: foreign, orgId: otherOrg, email: 'svc@other.test', kind: 'service', role: 'member', isDeleted: false });
      sa = foreign;
      expect((await mint()).status).to.equal(404);
      expect(audits()).to.deep.equal([{ event: 'service_account.token.create', outcome: 'failure', reason: 'not_found', actor: ada._id, target: foreign }]);
    });

    it('K6 a rotation (mint the new key, revoke the old) → two lines on the same account, different key_id', async () => {
      const old = (await mint()).body.token.id;
      const fresh = (await mint()).body.token.id;
      expect((await revoke(old)).status).to.equal(204);
      expect(audits()).to.deep.equal([{ event: 'service_account.token.revoke', outcome: 'success', actor: ada._id, target: sa, key_id: old }]);
      expect(fresh).to.not.equal(old);
    });
  });

  describe('a provider that hides user ids (EE)', () => {
    it('P1 refusal lines show the service account the way user ids are shown, never raw', async () => {
      await call('POST', '/api/v1/service-accounts', ada, { slug: SLUG, fullName: SA_NAME });
      const sa = users.find((u) => u.kind === 'service')!._id;
      const provider = Object.assign(new seam.DefaultLoggingProvider(), {
        fileLoggingEnabled: () => false,
        logIdentity: (ids: Record<string, unknown>) => ({ ...ids, ...(ids.user_id !== undefined && { user_id: 'hashed-user' }) }),
      });
      sinon.stub(seam, 'getProvider').callsFake(() => provider);
      users.find((u) => u._id === sa)!.isDisabled = true;
      const refusals: Array<[string, string, string]> = [
        ['POST', '/api/v1/service-tokens', 'service_account.token.create'],
        ['DELETE', `/api/v1/service-tokens/${oid()}?serviceAccountId=${sa}`, 'service_account.token.revoke'],
      ];
      for (const [method, path, event] of refusals) {
        await call(method, path, ada, method === 'POST' ? { serviceAccountId: sa, name: KEY_NAME, scopes: ['kb:read'] } : undefined);
        expect(audits().map((a) => [a.event, a.outcome, a.target])).to.deep.equal([[event, 'failure', 'hashed-user']]);
        expect(JSON.stringify(records.filter((r) => r.service === 'audit'))).to.not.include(sa);
      }
      const foreign = oid();
      users.push({ _id: foreign, orgId: otherOrg, email: 'svc@other.test', kind: 'service', role: 'member', isDeleted: false });
      for (const [method, event] of [['PATCH', 'service_account.update'], ['DELETE', 'service_account.delete']] as const) {
        await call(method, `/api/v1/service-accounts/${foreign}`, ada, method === 'PATCH' ? { fullName: 'Mine' } : undefined);
        expect(audits().map((a) => [a.event, a.outcome, a.target])).to.deep.equal([[event, 'failure', 'hashed-user']]);
        expect(JSON.stringify(records.filter((r) => r.service === 'audit'))).to.not.include(foreign);
      }
    });
  });
});
