/**
 * User and group audit lines over HTTP: one `audit` record per user or group
 * change (actor, target, org, outcome, reason), and no email or name in any
 * record.
 *
 * Served the way app.ts mounts the routers (request context, access line, JSON
 * body, router, error middleware) with the real users / user-groups / org
 * routers and controllers, AuthMiddleware + AuthTokenService (real JWTs), the
 * admin / self / exists / account-type / SMTP middlewares, MailService and
 * Logger. Faked: repositories (in memory), event producers, the auth-methods
 * lookup; the config manager and communication service via nock. Every
 * winston record written for this file's requests is captured.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import jwt from 'jsonwebtoken';
import nock from 'nock';
import winston from 'winston';
import type { AddressInfo } from 'net';
import type { Server } from 'http';
import mongoose from 'mongoose';
import { Container } from 'inversify';
import { createUserRouter } from '../../../src/modules/user_management/routes/users.routes';
import { createUserGroupRouter } from '../../../src/modules/user_management/routes/userGroups.routes';
import { createOrgRouter } from '../../../src/modules/user_management/routes/org.routes';
import { UserController } from '../../../src/modules/user_management/controller/users.controller';
import { UserGroupController } from '../../../src/modules/user_management/controller/userGroups.controller';
import { OrgController } from '../../../src/modules/user_management/controller/org.controller';
import { MailService } from '../../../src/modules/user_management/services/mail.service';
import { MAX_ORG_ADMINS } from '../../../src/modules/user_management/services/user-admin.service';
import { DEMO_ACCOUNT_DOMAIN } from '../../../src/modules/user_management/services/demo-accounts.service';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { AuthTokenService } from '../../../src/libs/services/authtoken.service';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { requestContextMiddleware } from '../../../src/libs/middlewares/request.context';
import { accessLog } from '../../../src/libs/middlewares/access-log.middleware';
import { Logger } from '../../../src/libs/services/logger.service';
import { authJwtGenerator } from '../../../src/libs/utils/createJwt';
import { TokenScopes } from '../../../src/libs/enums/token-scopes.enum';
import * as userRepo from '../../../src/libs/repositories/user';
import * as groupRepo from '../../../src/libs/repositories/group';
import * as credentialRepo from '../../../src/libs/repositories/credential';
import * as activityRepo from '../../../src/libs/repositories/activity';
import * as orgRepo from '../../../src/libs/repositories/org';
import * as oauthAppRepo from '../../../src/libs/repositories/oauth-app';
import { ProjectService } from '../../../src/modules/projects/services/project.service';

const JWT_SECRET = 'user-audit-jwt-secret';
const SCOPED_SECRET = 'user-audit-scoped-secret';
const CM = 'http://cm.user-audit.test';
const COMM = 'http://comm.user-audit.test';
const oid = () => new mongoose.Types.ObjectId().toHexString();
// connectors.routes installs axios-retry (3 retries on a 5xx) on the global axios when a worker loads it, so a
// failing mail interceptor must outlast the retries; an unmatched retry's nock error would quote the request body.
const MAIL_TRIES = 4;
const orgId = oid();
/** Every request this file makes carries a request id with this prefix; only those lines are captured. */
const OUR_REQUESTS = 'n4btest';
let requestSeq = 0;
const requestId = () => ({ 'x-request-id': `${OUR_REQUESTS}-${++requestSeq}` });

type Row = Record<string, any>;
const mkUser = (fullName: string, email: string, role: string, extra: Row = {}): Row => ({
  _id: oid(), orgId, email, fullName, role, hasLoggedIn: true, isDeleted: false, kind: 'user', ...extra,
});

describe('user and group audit lines (HTTP)', function () {
  this.timeout(20000);

  let server: Server;
  let baseUrl: string;
  let records: Row[];
  let users: Row[];
  let groups: Row[];
  let blocked: Set<string>;
  let ada: Row; let max: Row; let mia: Row; let dan: Row;
  let eng: Row; let everyone: Row;
  let orgCount: number;
  let activityFails: boolean;
  let everyoneWriteFails: boolean;
  let clearPasswordFails: boolean;
  let publishFails: boolean;
  let mailQueueFails: boolean;
  let notified: Promise<void>;
  let notify: () => void;
  let allocated: { orgId: string; adminUserId: string };
  const NEW1 = 'nora.new@acme.test';
  const NEW2 = 'ned.new@acme.test';
  const CREATED = 'cora.created@acme.test';
  const DEMO = `dora@${DEMO_ACCOUNT_DOMAIN}`;
  const SIGNUP = 'sam.signup@founder.test';
  const PASSWORD = 'Signup-Pass-9!';
  const PRIVATE = () => [ada.email, max.email, mia.email, dan.email, NEW1, NEW2, CREATED, DEMO, SIGNUP,
    'mia.new@acme.test', 'max.other@acme.test', ada.fullName, max.fullName, mia.fullName, dan.fullName,
    'Sam Signup', 'Cora Created', 'Dora Demo', 'Maximilian Planck', PASSWORD];

  const root = () => (Logger.getInstance() as any).logger as winston.Logger;
  const token = (u: Row) => authJwtGenerator(JWT_SECRET, u.email, u._id, u.orgId, u.fullName, 'business', u.role);
  const live = (o: string, id: string) => users.find((u) => u._id === id && u.orgId === o && !u.isDeleted) ?? null;

  /** The audit records, reduced to what each test pins. */
  const audits = () =>
    records
      .filter((r) => r.service === 'audit')
      .map((r) => {
        const out: Row = { event: r.message, outcome: r.outcome };
        for (const key of ['reason', 'actor', 'target', 'group_id']) if (r[key] != null) out[key] = r[key];
        return out;
      });
  const failureLines = () => records.filter((r) => ['warn', 'warning', 'error'].includes(r.level));
  const expectNoPrivateData = () => {
    for (const r of records) {
      const raw = JSON.stringify(r);
      for (const p of PRIVATE()) expect(raw, `${r.message} leaks ${p}`).to.not.include(p);
    }
  };

  async function call(method: string, path: string, who: Row | null, body?: unknown, headers: Record<string, string> = {}) {
    records.length = 0;
    const res = await fetch(`${baseUrl}${path}`, {
      method,
      headers: { 'content-type': 'application/json', ...requestId(), ...(who ? { authorization: `Bearer ${token(who)}` } : {}), ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
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

  beforeEach(async () => {
    records = [];
    orgCount = 1;
    activityFails = false;
    everyoneWriteFails = false;
    clearPasswordFails = false;
    publishFails = false;
    mailQueueFails = false;
    notified = new Promise<void>((resolve) => { notify = resolve; });
    root().level = 'debug';
    // Only this file's requests count: other work in the same mocha worker (timers, sweeps, reconnects) logs meanwhile.
    sinon.stub(winston.Logger.prototype, 'log').callsFake(function (this: unknown, info: Row) {
      if (String(info.request_id).startsWith(OUR_REQUESTS)) records.push(info);
      return this;
    } as any);

    ada = mkUser('Ada Lovelace', 'ada@acme.test', 'admin');
    max = mkUser('Max Planck', 'max@acme.test', 'member');
    mia = mkUser('Mia Hamm', 'mia@acme.test', 'member', { hasLoggedIn: false });
    dan = mkUser('Dan Removed', 'dan@acme.test', 'member', { isDeleted: true });
    users = [ada, max, mia, dan];
    everyone = { _id: oid(), orgId, name: 'everyone', type: 'everyone', users: users.map((u) => u._id), isDeleted: false };
    eng = { _id: oid(), orgId, name: 'Engineering', type: 'custom', users: [], isDeleted: false };
    groups = [everyone, eng];
    blocked = new Set([max._id]);

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
      getLive: async (o: string, id: string) => live(o, id),
      getForEdit: async (o: string, id: string) => live(o, id),
      getAny: async (o: string, id: string) => users.find((u) => u._id === id && u.orgId === o) ?? null,
      getRoleSnapshot: async (o: string, id: string) => { const u = live(o, id); return u ? { role: u.role, kind: u.kind } : null; },
      existsLiveEmail: async (e: string) => users.some((u) => u.email === e && !u.isDeleted),
      listRemovedIdsByEmail: async () => [],
      create: async (o: string, f: Row) => { const u = { _id: oid(), orgId: o, isDeleted: false, kind: 'user', ...f }; users.push(u); return { ...u }; },
      createMany: async (o: string, rows: Row[]) => rows.map((f) => { const u = { _id: oid(), orgId: o, kind: 'user', ...f }; users.push(u); return { ...u }; }),
      deleteById: async (id: string) => { users = users.filter((u) => u._id !== id); },
      updateProfile: async (o: string, id: string, f: Row) => { const u = live(o, id); if (!u) return null; Object.assign(u, f); return { ...u }; },
      emailTakenByOther: async (_o: string, e: string, id: string) => users.some((u) => u.email === e && u._id !== id && !u.isDeleted),
      listLiveMembershipsByEmail: async (e: string) => users.filter((u) => u.email === e && !u.isDeleted),
      markDeleted: async (o: string, id: string) => { const u = live(o, id); if (u) u.isDeleted = true; },
      listByEmails: async (o: string, es: string[]) => users.filter((u) => u.orgId === o && es.includes(u.email)).map((u) => ({ ...u })),
      restoreByEmails: async (o: string, es: string[]) => users.filter((u) => u.orgId === o && es.includes(u.email) && u.isDeleted).map((u) => { u.isDeleted = false; return { ...u }; }),
      promoteToAdmin: async (_o: string, ids: string[]) => { for (const u of users) if (ids.includes(u._id)) u.role = 'admin'; },
      countActiveAdmins: async (o: string) => users.filter((u) => u.orgId === o && !u.isDeleted && u.role === 'admin').length,
      setRole: async (o: string, id: string, role: string) => { const u = live(o, id); if (u) u.role = role; },
      serviceAccountIdsAmong: async () => [],
      lockOrgAdminChanges: async () => undefined,
    }));
    sinon.stub(groupRepo, 'getGroupRepository').returns(fake('group', {
      addToEveryone: async (o: string, id: string) => { if (everyoneWriteFails) throw new Error('group store down'); if (o === orgId && !everyone.users.includes(id)) everyone.users.push(id); },
      removeFromEveryone: async () => undefined,
      removeFromAll: async (o: string, id: string) => { for (const g of groups) if (g.orgId === o) g.users = g.users.filter((x: string) => x !== id); },
      addMemberToGroups: async (_o: string, gids: string[], id: string) => { for (const g of groups) if (gids.includes(g._id) && !g.users.includes(id)) g.users.push(id); },
      nameTaken: async (o: string, n: string, except?: string) => groups.some((g) => g.orgId === o && g.name === n && g._id !== except && !g.isDeleted),
      create: async (o: string, f: Row) => { const g = { _id: oid(), orgId: o, users: [], isDeleted: false, ...f }; groups.push(g); return { ...g }; },
      get: async (o: string, id: string) => groups.find((g) => g._id === id && g.orgId === o) ?? null,
      getLive: async (o: string, id: string) => groups.find((g) => g._id === id && g.orgId === o && !g.isDeleted) ?? null,
      rename: async (o: string, id: string, n: string) => { const g = groups.find((x) => x._id === id && x.orgId === o); if (!g) return null; g.name = n; return { ...g }; },
      softDelete: async (o: string, id: string) => { const g = groups.find((x) => x._id === id && x.orgId === o); if (g) g.isDeleted = true; return g ? { ...g } : null; },
      // Counts the groups that changed, as Mongo's modifiedCount does.
      addMembers: async (o: string, gids: string[], uids: string[]) => { let n = 0; for (const g of groups) if (g.orgId === o && gids.includes(g._id) && !g.isDeleted) { const next = [...new Set([...g.users, ...uids])]; if (next.length !== g.users.length) n++; g.users = next; } return n; },
      removeMembers: async (o: string, gids: string[], uids: string[]) => { let n = 0; for (const g of groups) if (g.orgId === o && gids.includes(g._id) && !g.isDeleted) { const next = g.users.filter((x: string) => !uids.includes(x)); if (next.length !== g.users.length) n++; g.users = next; } return n; },
    }));
    sinon.stub(credentialRepo, 'getCredentialRepository').returns(fake('credential', {
      unblock: async (id: string) => blocked.delete(id),
      blockedUserIds: async () => [],
      clearPassword: async () => { if (clearPasswordFails) throw new Error('credential store down'); },
      createWithPassword: async () => undefined,
      discardForUser: async () => undefined,
    }));
    sinon.stub(activityRepo, 'getActivityRepository').returns(fake('activity', {
      latestSessionEnding: async () => null,
      latestPasswordChange: async () => null,
      record: async () => { if (activityFails) throw new Error('activity store down'); },
      recordMany: async () => undefined,
    }));
    sinon.stub(orgRepo, 'getOrgRepository').returns(fake('org', {
      getLive: async (o: string) => (o === orgId ? { _id: orgId, registeredName: 'Acme Ltd', shortName: 'Acme', accountType: 'business', isDeleted: false } : null),
      count: async () => orgCount,
      allocateIds: () => (allocated = { orgId: oid(), adminUserId: oid() }),
      createWithAdmin: async ({ ids, org, admin }: Row) => ({ org: { _id: ids.orgId, registeredName: org.registeredName, accountType: org.accountType }, adminUserId: ids.adminUserId, adminEmail: admin.email }),
    }));
    sinon.stub(oauthAppRepo, 'getOAuthAppRepository').returns(fake('oauth-app', {
      listClientIdsCreatedBy: async () => [],
      softDeleteCreatedBy: async () => undefined,
    }));
    sinon.stub(ProjectService, 'findProjectsWithLinkedKbForUser').resolves([]);
    sinon.stub(ProjectService, 'removeUserFromAllProjects').resolves(undefined as any);
    nock(CM).persist().get(/smtpConfig/).reply(200, { host: 'smtp.acme.test', port: 25, fromEmail: 'noreply@acme.test' });

    const config: Row = { jwtSecret: JWT_SECRET, scopedJwtSecret: SCOPED_SECRET, cmBackend: CM, communicationBackend: COMM, frontendUrl: 'http://app.user-audit.test', rsAvailable: 'false' };
    const logger = Logger.getInstance({ service: 'UserManagement' });
    const events = { start: async () => undefined, stop: async () => undefined, publishEvent: async () => { if (publishFails) throw new Error('outbox down'); } };
    const mailProducer = { publishEvent: async () => { if (mailQueueFails) throw new Error('broker unavailable'); } };
    const mailService = new MailService(...([config, logger, mailProducer] as unknown as ConstructorParameters<typeof MailService>));
    const authService = { passwordMethodEnabled: async () => ({ statusCode: 200, data: { isPasswordAuthEnabled: true } }) };
    const notifications = { start: async () => undefined, publishEvent: async () => { notify(); } };
    const authMiddleware = new AuthMiddleware(logger, new AuthTokenService(JWT_SECRET, SCOPED_SECRET));
    const container = new Container();
    container.bind('Logger').toConstantValue(logger);
    container.bind('AppConfig').toConstantValue(config);
    container.bind('AuthMiddleware').toConstantValue(authMiddleware);
    container.bind('MailService').toConstantValue(mailService);
    container.bind('AuthService').toConstantValue(authService);
    container.bind('EntitiesEventProducer').toConstantValue(events);
    container.bind('NotificationProducer').toConstantValue(notifications);
    container.bind('UserController').toConstantValue(new UserController(...([config, mailService, authService, logger, events, notifications] as unknown as ConstructorParameters<typeof UserController>)));
    container.bind('UserGroupController').toConstantValue(new UserGroupController());
    container.bind('OrgController').toConstantValue(new OrgController(...([config, mailService, logger, events] as unknown as ConstructorParameters<typeof OrgController>)));

    const app = express();
    app.use(requestContextMiddleware);
    app.use(accessLog());
    app.use(express.json());
    app.use('/api/v1/users', createUserRouter(container));
    app.use('/api/v1/userGroups', createUserGroupRouter(container));
    app.use('/api/v1/org', createOrgRouter(container));
    // EE mounts this scope on its workspace routes; OSS has no route for it, so the test mounts one.
    app.get('/api/v1/test/email-verified', authMiddleware.scopedTokenValidator(TokenScopes.EMAIL_VERIFIED), (_req, res) => { res.json({ ok: true }); });
    app.get('/api/v1/test/org-email-verify', authMiddleware.scopedTokenValidator(TokenScopes.ORG_EMAIL_VERIFY), (_req, res) => { res.json({ ok: true }); });
    app.use(ErrorMiddleware.handleError());
    server = await new Promise<Server>((resolve) => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
    baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });

  afterEach(async () => {
    sinon.restore();
    nock.cleanAll();
    await new Promise((resolve) => server.close(resolve));
  });

  /** `promise`, or a failure naming `what` after `ms`. */
  async function within<T>(promise: Promise<T>, ms: number, what: string): Promise<T> {
    let timer: NodeJS.Timeout | undefined;
    const timeout = new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error(what)), ms); });
    try { return await Promise.race([promise, timeout]); } finally { clearTimeout(timer); }
  }

  const U = '/api/v1/users';
  const G = '/api/v1/userGroups';
  const newUserId = (email: string) => users.find((u) => u.email === email)!._id;

  describe('users', () => {
    it('T1 create → user.create success, target = the new account', async () => {
      expect((await call('POST', U, ada, { fullName: 'Cora Created', email: CREATED })).status).to.equal(201);
      expect(audits()).to.deep.equal([{ event: 'user.create', outcome: 'success', actor: ada._id, target: newUserId(CREATED) }]);
      expectNoPrivateData();
    });

    it('T1b create as admin → user.create, then user.role.change new_admin', async () => {
      expect((await call('POST', U, ada, { fullName: 'Cora Created', email: CREATED, role: 'admin' })).status).to.equal(201);
      expect(audits()).to.deep.equal([
        { event: 'user.create', outcome: 'success', actor: ada._id, target: newUserId(CREATED) },
        { event: 'user.role.change', outcome: 'success', reason: 'new_admin', actor: ada._id, target: newUserId(CREATED) },
      ]);
    });

    it('T1d [guard] the creation event cannot be published → account undone, 500, no user.create line', async () => {
      publishFails = true;
      expect((await call('POST', U, ada, { fullName: 'Cora Created', email: CREATED })).status).to.equal(500);
      expect(audits()).to.deep.equal([]);
      expect(users.some((u) => u.email === CREATED)).to.equal(false);
    });

    it('T1c [guard] the account is undone when a later write fails → no user.create line', async () => {
      everyoneWriteFails = true;
      expect((await call('POST', U, ada, { fullName: 'Cora Created', email: CREATED })).status).to.equal(500);
      expect(audits()).to.deep.equal([]);
      expectNoPrivateData();
    });

    it('T3b a starting password for a real address → user.create failure password_not_allowed', async () => {
      expect((await call('POST', U, ada, { fullName: 'Cora Created', email: CREATED, password: PASSWORD })).status).to.equal(400);
      expect(audits()).to.deep.equal([{ event: 'user.create', outcome: 'failure', reason: 'password_not_allowed', actor: ada._id }]);
      expectNoPrivateData();
    });

    it('T2 create a taken address → user.create failure email_taken', async () => {
      expect((await call('POST', U, ada, { fullName: 'Max Planck', email: max.email })).status).to.equal(400);
      expect(audits()).to.deep.equal([{ event: 'user.create', outcome: 'failure', reason: 'email_taken', actor: ada._id }]);
      expectNoPrivateData();
    });

    it('T3 demo account with a starting password → reason demo_password, no address in any line', async () => {
      expect((await call('POST', U, ada, { fullName: 'Dora Demo', email: DEMO, password: PASSWORD })).status).to.equal(201);
      expect(audits()).to.deep.equal([{ event: 'user.create', outcome: 'success', reason: 'demo_password', actor: ada._id, target: newUserId(DEMO) }]);
      expectNoPrivateData();
    });

    it('T4 invite two new addresses → one user.invite per invitee', async () => {
      expect((await call('POST', `${U}/bulk/invite`, ada, { emails: [NEW1, NEW2] })).status).to.equal(200);
      expect(audits()).to.deep.equal([
        { event: 'user.invite', outcome: 'success', reason: 'new', actor: ada._id, target: newUserId(NEW1) },
        { event: 'user.invite', outcome: 'success', reason: 'new', actor: ada._id, target: newUserId(NEW2) },
      ]);
      expectNoPrivateData();
    });

    it('T5b invite a removed member as admin → user.restore invite, then user.role.change promote', async () => {
      expect((await call('POST', `${U}/bulk/invite`, ada, { emails: [dan.email], role: 'admin' })).status).to.equal(200);
      expect(audits()).to.deep.equal([
        { event: 'user.restore', outcome: 'success', reason: 'invite', actor: ada._id, target: dan._id },
        { event: 'user.role.change', outcome: 'success', reason: 'promote', actor: ada._id, target: dan._id },
      ]);
    });

    it('T5 invite a removed account → user.restore reason invite', async () => {
      expect((await call('POST', `${U}/bulk/invite`, ada, { emails: [dan.email] })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'user.restore', outcome: 'success', reason: 'invite', actor: ada._id, target: dan._id }]);
      expectNoPrivateData();
    });

    it('T6 invite a pending account as admin → user.invite reinvite, then user.role.change promote', async () => {
      expect((await call('POST', `${U}/bulk/invite`, ada, { emails: [mia.email], role: 'admin' })).status).to.equal(200);
      expect(audits()).to.deep.equal([
        { event: 'user.role.change', outcome: 'success', reason: 'promote', actor: ada._id, target: mia._id },
        { event: 'user.invite', outcome: 'success', reason: 'reinvite', actor: ada._id, target: mia._id },
      ]);
      expectNoPrivateData();
    });

    it('T7 a member invites as admin → user.invite failure not_admin', async () => {
      expect((await call('POST', `${U}/bulk/invite`, max, { emails: [NEW1], role: 'admin' })).status).to.equal(403);
      expect(audits()).to.deep.equal([{ event: 'user.invite', outcome: 'failure', reason: 'not_admin', actor: max._id }]);
      expectNoPrivateData();
    });

    it('T8 an admin invite past the admin cap → user.invite failure admin_limit', async () => {
      for (let i = 1; i < MAX_ORG_ADMINS; i++) users.push(mkUser(`Admin ${i}`, `admin${i}@acme.test`, 'admin'));
      expect((await call('POST', `${U}/bulk/invite`, ada, { emails: [NEW1], role: 'admin' })).status).to.equal(400);
      expect(audits()).to.deep.equal([{ event: 'user.invite', outcome: 'failure', reason: 'admin_limit', actor: ada._id }]);
    });

    it('T4b invite a new address as admin → user.invite new, then user.role.change new_admin', async () => {
      expect((await call('POST', `${U}/bulk/invite`, ada, { emails: [NEW1], role: 'admin' })).status).to.equal(200);
      expect(audits()).to.deep.equal([
        { event: 'user.invite', outcome: 'success', reason: 'new', actor: ada._id, target: newUserId(NEW1) },
        { event: 'user.role.change', outcome: 'success', reason: 'new_admin', actor: ada._id, target: newUserId(NEW1) },
      ]);
      expectNoPrivateData();
    });

    it('T4c invite from a file → the same per-invitee lines, written by the background task, actor = the uploader', async () => {
      records.length = 0;
      const form = new FormData();
      form.append('file', new Blob([`email\n${NEW1}\n${NEW2}\n`], { type: 'text/csv' }), 'invites.csv');
      const res = await fetch(`${baseUrl}${U}/bulk/invite/upload`, {
        method: 'POST', headers: { ...requestId(), authorization: `Bearer ${token(ada)}` }, body: form, signal: AbortSignal.timeout(5000),
      });
      expect(res.status).to.equal(202);
      // The background task ends by notifying the uploader; only then is its line list complete.
      await within(notified, 2000, 'the background invite did not finish');
      expect(audits()).to.deep.equal([
        { event: 'user.invite', outcome: 'success', reason: 'new', actor: ada._id, target: newUserId(NEW1) },
        { event: 'user.invite', outcome: 'success', reason: 'new', actor: ada._id, target: newUserId(NEW2) },
      ]);
      expectNoPrivateData();
    });

    it('T4d invite a new and an active address with groups → membership lines for both, not_found for a missing group', async () => {
      const missing = oid();
      expect((await call('POST', `${U}/bulk/invite`, ada, { emails: [max.email, NEW1], groupIds: [eng._id, missing] })).status).to.equal(200);
      expect(audits()).to.deep.equal([
        { event: 'group.member.add', outcome: 'success', actor: ada._id, target: max._id, group_id: eng._id },
        { event: 'group.member.add', outcome: 'failure', reason: 'not_found', actor: ada._id, target: max._id, group_id: missing },
        { event: 'user.invite', outcome: 'success', reason: 'new', actor: ada._id, target: newUserId(NEW1) },
        { event: 'group.member.add', outcome: 'success', actor: ada._id, target: newUserId(NEW1), group_id: eng._id },
        { event: 'group.member.add', outcome: 'failure', reason: 'not_found', actor: ada._id, target: newUserId(NEW1), group_id: missing },
      ]);
      expectNoPrivateData();
    });

    it('T4e every address already active, with groups → 409 as before, and the membership it still made is audited', async () => {
      expect((await call('POST', `${U}/bulk/invite`, ada, { emails: [max.email], groupIds: [eng._id] })).status).to.equal(409);
      expect(eng.users).to.include(max._id);
      expect(audits()).to.deep.equal([
        { event: 'group.member.add', outcome: 'success', actor: ada._id, target: max._id, group_id: eng._id },
      ]);
    });

    it('T4g invite with a deleted group → the store still adds the member, so the line is a success', async () => {
      const old = { _id: oid(), orgId, name: 'Old', type: 'custom', users: [], isDeleted: true };
      groups.push(old);
      expect((await call('POST', `${U}/bulk/invite`, ada, { emails: [max.email], groupIds: [old._id] })).status).to.equal(409);
      expect(audits()).to.deep.equal([
        { event: 'group.member.add', outcome: 'success', actor: ada._id, target: max._id, group_id: old._id },
      ]);
    });

    it('T6b invite a pending admin as admin → reinvite only, no promote', async () => {
      const paula = mkUser('Paula Pending', 'paula@acme.test', 'admin', { hasLoggedIn: false });
      users.push(paula);
      expect((await call('POST', `${U}/bulk/invite`, ada, { emails: [paula.email], role: 'admin' })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'user.invite', outcome: 'success', reason: 'reinvite', actor: ada._id, target: paula._id }]);
    });

    it('T9b [guard] re-invite whose mail cannot be queued → no reinvite line', async () => {
      mailQueueFails = true;
      expect((await call('POST', `${U}/bulk/invite`, ada, { emails: [mia.email] })).status).to.equal(500);
      expect(audits()).to.deep.equal([]);
      expectNoPrivateData();
    });

    it('T9 resend an invite → user.invite reason resend', async () => {
      nock(COMM).post(/sendEmail/).reply(200, { ok: true });
      expect((await call('POST', `${U}/${mia._id}/resend-invite`, ada)).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'user.invite', outcome: 'success', reason: 'resend', actor: ada._id, target: mia._id }]);
      expectNoPrivateData();
    });

    it('T10 resend fails at the mail service → no audit line, one failure line, no address', async () => {
      nock(COMM).post(/sendEmail/).times(MAIL_TRIES).reply(500, { error: { message: `mailbox ${mia.email} unavailable` } });
      expect((await call('POST', `${U}/${mia._id}/resend-invite`, ada)).status).to.equal(500);
      expect(audits()).to.deep.equal([]);
      // The mail service's own line keeps the upstream cause (status, no body); the controller adds none.
      expect(failureLines().map((r) => r.message)).to.deep.equal(['Error sending mail', 'http.request.error', 'http.request.end']);
      expectNoPrivateData();
    });

    it('T11 promote → user.role.change promote', async () => {
      expect((await call('PUT', `${U}/${max._id}`, ada, { role: 'admin' })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'user.role.change', outcome: 'success', reason: 'promote', actor: ada._id, target: max._id }]);
    });

    it('T11b demote one of two admins → user.role.change demote', async () => {
      max.role = 'admin';
      expect((await call('PUT', `${U}/${max._id}`, ada, { role: 'member' })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'user.role.change', outcome: 'success', reason: 'demote', actor: ada._id, target: max._id }]);
    });

    it('T12 demote the last admin → user.role.change failure last_admin', async () => {
      expect((await call('PUT', `${U}/${ada._id}`, ada, { role: 'member' })).status).to.equal(400);
      expect(audits()).to.deep.equal([{ event: 'user.role.change', outcome: 'failure', reason: 'last_admin', actor: ada._id, target: ada._id }]);
    });

    it('T13 a member makes themself admin → user.role.change failure not_admin', async () => {
      expect((await call('PUT', `${U}/${max._id}`, max, { role: 'admin' })).status).to.equal(403);
      expect(audits()).to.deep.equal([{ event: 'user.role.change', outcome: 'failure', reason: 'not_admin', actor: max._id, target: max._id }]);
    });

    it('T14 promote past the admin cap → user.role.change failure admin_limit', async () => {
      for (let i = 1; i < MAX_ORG_ADMINS; i++) users.push(mkUser(`Admin ${i}`, `admin${i}@acme.test`, 'admin'));
      expect((await call('PUT', `${U}/${max._id}`, ada, { role: 'admin' })).status).to.equal(400);
      expect(audits()).to.deep.equal([{ event: 'user.role.change', outcome: 'failure', reason: 'admin_limit', actor: ada._id, target: max._id }]);
    });

    it('T15 rename yourself → user.update profile', async () => {
      expect((await call('PATCH', `${U}/${max._id}/fullname`, max, { fullName: 'Maximilian Planck' })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'user.update', outcome: 'success', reason: 'profile', actor: max._id, target: max._id }]);
      expectNoPrivateData();
    });

    it('T16 role and designation in one PUT → role change then profile update', async () => {
      expect((await call('PUT', `${U}/${max._id}`, ada, { role: 'admin', designation: 'Physicist' })).status).to.equal(200);
      expect(audits()).to.deep.equal([
        { event: 'user.role.change', outcome: 'success', reason: 'promote', actor: ada._id, target: max._id },
        { event: 'user.update', outcome: 'success', reason: 'profile', actor: ada._id, target: max._id },
      ]);
    });

    it('T17 an admin sets a user back to pending → user.update sign_in_state', async () => {
      expect((await call('PUT', `${U}/${max._id}`, ada, { hasLoggedIn: false })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'user.update', outcome: 'success', reason: 'sign_in_state', actor: ada._id, target: max._id }]);
    });

    it('T15b set your designation → user.update profile', async () => {
      expect((await call('PATCH', `${U}/${max._id}/designation`, max, { designation: 'Physicist' })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'user.update', outcome: 'success', reason: 'profile', actor: max._id, target: max._id }]);
    });

    for (const [field, value] of [['firstName', 'Maximilian'], ['lastName', 'Plank']] as const) {
      it(`T15c set your ${field} → user.update profile`, async () => {
        expect((await call('PATCH', `${U}/${max._id}/${field}`, max, { [field]: value })).status).to.equal(200);
        expect(audits()).to.deep.equal([{ event: 'user.update', outcome: 'success', reason: 'profile', actor: max._id, target: max._id }]);
      });
    }

    for (const [path, field, value] of [['fullname', 'fullName', 'Maximilian Planck'], ['firstName', 'firstName', 'Maximilian'],
      ['lastName', 'lastName', 'Plank'], ['designation', 'designation', 'Physicist']] as const) {
      it(`T15d [guard] ${field} saved but its event cannot be published → 500, still one user.update line`, async () => {
        publishFails = true;
        expect((await call('PATCH', `${U}/${max._id}/${path}`, max, { [field]: value })).status).to.equal(500);
        expect(max[field]).to.equal(value);
        expect(audits()).to.deep.equal([{ event: 'user.update', outcome: 'success', reason: 'profile', actor: max._id, target: max._id }]);
      });
    }

    it('T17b [guard] a user marks their own first sign-in → no user.update line (auth.sign_in tells it)', async () => {
      expect((await call('PUT', `${U}/${mia._id}`, mia, { hasLoggedIn: true, email: mia.email })).status).to.equal(200);
      expect(audits()).to.deep.equal([]);
    });

    it('T18 request your own email change → user.email_change.request', async () => {
      nock(COMM).post(/sendEmail/).times(2).reply(200, { ok: true });
      expect((await call('PATCH', `${U}/${mia._id}/email`, mia, { email: 'mia.new@acme.test' })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'user.email_change.request', outcome: 'success', actor: mia._id, target: mia._id }]);
      expectNoPrivateData();
    });

    it("T19 an admin changes someone else's address → failure not_self", async () => {
      expect((await call('PATCH', `${U}/${max._id}/email`, ada, { email: 'max.other@acme.test' })).status).to.equal(403);
      expect(audits()).to.deep.equal([{ event: 'user.email_change.request', outcome: 'failure', reason: 'not_self', actor: ada._id, target: max._id }]);
      expectNoPrivateData();
    });

    it('T20 change to a taken address → failure email_taken', async () => {
      expect((await call('PATCH', `${U}/${mia._id}/email`, mia, { email: max.email })).status).to.equal(400);
      expect(audits()).to.deep.equal([{ event: 'user.email_change.request', outcome: 'failure', reason: 'email_taken', actor: mia._id, target: mia._id }]);
      expectNoPrivateData();
    });

    it('T20b email change through PUT → user.email_change.request, no user.update', async () => {
      nock(COMM).post(/sendEmail/).times(2).reply(200, { ok: true });
      expect((await call('PUT', `${U}/${mia._id}`, mia, { email: 'mia.new@acme.test' })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'user.email_change.request', outcome: 'success', actor: mia._id, target: mia._id }]);
      expectNoPrivateData();
    });

    it("T20d PUT refusals: someone else's address → not_self; a taken address → email_taken", async () => {
      expect((await call('PUT', `${U}/${max._id}`, ada, { email: 'max.other@acme.test' })).status).to.equal(403);
      expect(audits()).to.deep.equal([{ event: 'user.email_change.request', outcome: 'failure', reason: 'not_self', actor: ada._id, target: max._id }]);
      expectNoPrivateData();
      expect((await call('PUT', `${U}/${mia._id}`, mia, { email: max.email })).status).to.equal(400);
      expect(audits()).to.deep.equal([{ event: 'user.email_change.request', outcome: 'failure', reason: 'email_taken', actor: mia._id, target: mia._id }]);
      expectNoPrivateData();
    });

    it('T20c [guard] email change through PUT whose mail fails → 200 with status failed, no request line', async () => {
      nock(COMM).post(/sendEmail/).times(MAIL_TRIES).reply(500, { error: { message: 'smtp down' } });
      const res = await call('PUT', `${U}/${mia._id}`, mia, { email: 'mia.new@acme.test' });
      expect(res.status).to.equal(200);
      expect(res.body.meta.emailChangeMailStatus).to.equal('failed');
      expect(audits()).to.deep.equal([]);
      expectNoPrivateData();
    });

    it('T21 delete a member → user.delete', async () => {
      expect((await call('DELETE', `${U}/${max._id}`, ada)).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'user.delete', outcome: 'success', actor: ada._id, target: max._id }]);
    });

    it('T22 delete an admin → user.delete failure not_member', async () => {
      expect((await call('DELETE', `${U}/${ada._id}`, ada)).status).to.equal(400);
      expect(audits()).to.deep.equal([{ event: 'user.delete', outcome: 'failure', reason: 'not_member', actor: ada._id, target: ada._id }]);
    });

    it('T21b [guard] deletion fails after the activity row → no user.delete line', async () => {
      clearPasswordFails = true;
      expect((await call('DELETE', `${U}/${max._id}`, ada)).status).to.equal(500);
      expect(audits()).to.deep.equal([]);
    });

    it('T23 deletion cannot be recorded → no audit line, the failure logged once', async () => {
      activityFails = true;
      expect((await call('DELETE', `${U}/${max._id}`, ada)).status).to.equal(503);
      expect(audits()).to.deep.equal([]);
      expect(failureLines().map((r) => r.message)).to.deep.equal(['http.request.error', 'http.request.end']);
      expect(failureLines()[0].metadata.error.cause).to.deep.equal({ name: 'Error', message: 'activity store down' });
    });

    it('T24 [guard] a member is stopped by the admin route guard → no audit line', async () => {
      expect((await call('DELETE', `${U}/${mia._id}`, max)).status).to.equal(403);
      expect(audits()).to.deep.equal([]);
    });

    it('T25 [guard] admin unblock keeps N4a\'s auth.account.unlock', async () => {
      expect((await call('PUT', `${U}/${max._id}/unblock`, ada)).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'auth.account.unlock', outcome: 'success', reason: 'admin', actor: ada._id, target: max._id }]);
    });

    it('T26 [guard] a validation 400 writes no audit line', async () => {
      expect((await call('PUT', `${U}/${max._id}`, ada, { mobile: 'not-a-number' })).status).to.equal(400);
      expect(audits()).to.deep.equal([]);
    });
  });

  describe('groups', () => {
    it('T27 create a group → group.create, target = the group', async () => {
      const res = await call('POST', G, ada, { name: 'Physics', type: 'custom' });
      expect(res.status).to.equal(201);
      expect(audits()).to.deep.equal([{ event: 'group.create', outcome: 'success', actor: ada._id, target: res.body._id }]);
    });

    it('T28 rename → group.update', async () => {
      expect((await call('PUT', `${G}/${eng._id}`, ada, { name: 'Eng' })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'group.update', outcome: 'success', actor: ada._id, target: eng._id }]);
    });

    it('T29 rename the everyone group → group.update failure protected_group', async () => {
      expect((await call('PUT', `${G}/${everyone._id}`, ada, { name: 'All' })).status).to.equal(403);
      expect(audits()).to.deep.equal([{ event: 'group.update', outcome: 'failure', reason: 'protected_group', actor: ada._id, target: everyone._id }]);
    });

    it('T30 delete → group.delete', async () => {
      expect((await call('DELETE', `${G}/${eng._id}`, ada)).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'group.delete', outcome: 'success', actor: ada._id, target: eng._id }]);
    });

    it('T31 delete the everyone group → group.delete failure protected_group', async () => {
      expect((await call('DELETE', `${G}/${everyone._id}`, ada)).status).to.equal(403);
      expect(audits()).to.deep.equal([{ event: 'group.delete', outcome: 'failure', reason: 'protected_group', actor: ada._id, target: everyone._id }]);
    });

    it('T32 add two users → one group.member.add per user, with group_id', async () => {
      expect((await call('POST', `${G}/add-users`, ada, { userIds: [max._id, mia._id], groupIds: [eng._id] })).status).to.equal(200);
      expect(audits()).to.deep.equal([
        { event: 'group.member.add', outcome: 'success', actor: ada._id, target: max._id, group_id: eng._id },
        { event: 'group.member.add', outcome: 'success', actor: ada._id, target: mia._id, group_id: eng._id },
      ]);
    });

    it('T33 remove a user → group.member.remove', async () => {
      eng.users = [max._id];
      expect((await call('POST', `${G}/remove-users`, ada, { userIds: [max._id], groupIds: [eng._id] })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'group.member.remove', outcome: 'success', actor: ada._id, target: max._id, group_id: eng._id }]);
    });

    it('T34 every group missing → 400, one not_found line per user × group', async () => {
      const missing = oid();
      expect((await call('POST', `${G}/add-users`, ada, { userIds: [max._id, mia._id], groupIds: [missing] })).status).to.equal(400);
      expect(audits()).to.deep.equal([
        { event: 'group.member.add', outcome: 'failure', reason: 'not_found', actor: ada._id, target: max._id, group_id: missing },
        { event: 'group.member.add', outcome: 'failure', reason: 'not_found', actor: ada._id, target: mia._id, group_id: missing },
      ]);
    });

    it('T34b one live and one missing group → success for the live one, not_found for the missing one', async () => {
      const missing = oid();
      expect((await call('POST', `${G}/add-users`, ada, { userIds: [max._id], groupIds: [eng._id, missing, eng._id] })).status).to.equal(200);
      expect(audits()).to.deep.equal([
        { event: 'group.member.add', outcome: 'success', actor: ada._id, target: max._id, group_id: eng._id },
        { event: 'group.member.add', outcome: 'failure', reason: 'not_found', actor: ada._id, target: max._id, group_id: missing },
      ]);
    });

    it('T34c every group live, the user already a member → 400 as before, success already_member', async () => {
      eng.users = [max._id];
      expect((await call('POST', `${G}/add-users`, ada, { userIds: [max._id], groupIds: [eng._id] })).status).to.equal(400);
      expect(audits()).to.deep.equal([
        { event: 'group.member.add', outcome: 'success', reason: 'already_member', actor: ada._id, target: max._id, group_id: eng._id },
      ]);
    });

    it('T34e add to a deleted group → 400, not_found', async () => {
      eng.isDeleted = true;
      expect((await call('POST', `${G}/add-users`, ada, { userIds: [max._id], groupIds: [eng._id] })).status).to.equal(400);
      expect(audits()).to.deep.equal([
        { event: 'group.member.add', outcome: 'failure', reason: 'not_found', actor: ada._id, target: max._id, group_id: eng._id },
      ]);
    });

    it('T34d remove a non-member, and from a missing group → 400, not_member and not_found', async () => {
      const missing = oid();
      expect((await call('POST', `${G}/remove-users`, ada, { userIds: [mia._id], groupIds: [eng._id, missing] })).status).to.equal(400);
      expect(audits()).to.deep.equal([
        { event: 'group.member.remove', outcome: 'success', reason: 'not_member', actor: ada._id, target: mia._id, group_id: eng._id },
        { event: 'group.member.remove', outcome: 'failure', reason: 'not_found', actor: ada._id, target: mia._id, group_id: missing },
      ]);
    });
  });

  describe('org signup and email verification', () => {
    it('T35 first org signup → org.create, actor = the new admin, org_id = the new org', async () => {
      orgCount = 0;
      expect((await call('POST', '/api/v1/org', null, { accountType: 'business', registeredName: 'Founder Inc', contactEmail: SIGNUP, adminFullName: 'Sam Signup', password: PASSWORD })).status).to.equal(200);
      expect(audits()).to.deep.equal([{ event: 'org.create', outcome: 'success', actor: allocated.adminUserId }]);
      expect(records.find((r) => r.service === 'audit')!.org_id).to.equal(allocated.orgId);
      expectNoPrivateData();
    });

    const expired = () =>
      `Bearer ${jwt.sign({ email: max.email, scopes: [TokenScopes.EMAIL_VERIFIED], exp: Math.floor(Date.now() / 1000) - 60 }, SCOPED_SECRET)}`;
    for (const [name, header, reason] of [
      ['no token', undefined, 'no_token'],
      ['forged token', 'Bearer forged.token.value', 'token_invalid'],
      ['expired token', 'expired', 'token_expired'],
    ] as const) {
      it(`T36 email-verified token refused (${name}) → auth.email.verify failure ${reason}`, async () => {
        const authorization = header === 'expired' ? expired() : header;
        const res = await call('GET', '/api/v1/test/email-verified', null, undefined, authorization ? { authorization } : {});
        expect(res.status).to.equal(401);
        expect(audits()).to.deep.equal([{ event: 'auth.email.verify', outcome: 'failure', reason }]);
        expectNoPrivateData();
      });
    }

    it('T36b the org email-verify scope is audited the same way', async () => {
      expect((await call('GET', '/api/v1/test/org-email-verify', null)).status).to.equal(401);
      expect(audits()).to.deep.equal([{ event: 'auth.email.verify', outcome: 'failure', reason: 'no_token' }]);
    });
  });

  describe('volume', () => {
    it('T37 a change is 2 INFO lines (audit + access); a refused change is 2 INFO + 1 WARN', async () => {
      await call('DELETE', `${G}/${eng._id}`, ada);
      expect(records.filter((r) => r.level === 'info').map((r) => r.message)).to.deep.equal(['group.delete', 'http.request.end']);
      await call('DELETE', `${U}/${ada._id}`, ada);
      expect(records.filter((r) => r.level === 'info').map((r) => r.message)).to.deep.equal(['user.delete', 'http.request.end']);
      expect(records.filter((r) => ['warn', 'warning'].includes(r.level)).map((r) => r.message)).to.deep.equal(['http.request.error']);
    });
  });
});
