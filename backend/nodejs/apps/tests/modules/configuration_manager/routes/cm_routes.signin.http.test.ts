/// <reference types="mocha" />
// Serves the real configuration router over HTTP behind the real AuthMiddleware, with real
// signed session, PAT and OAuth tokens. Only Mongo reads and the key-value store are faked.
// The routes that change how the workspace signs people in must take an interactive session
// only, and the matching reads must not hand a secret to a token holder.
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import type { NextFunction, Request, Response } from 'express';
import type { AddressInfo } from 'net';
import type { Server } from 'http';
import http from 'http';
import net from 'net';
import jwt from 'jsonwebtoken';
import { Container } from 'inversify';
import { createConfigurationManagerRouter } from '../../../../src/modules/configuration_manager/routes/cm_routes';
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware';
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service';
import { OAuthTokenService } from '../../../../src/modules/oauth_provider/services/oauth_token.service';
import { OAuthAccessToken } from '../../../../src/modules/oauth_provider/schema/oauth.access_token.schema';
import { OAuthApp } from '../../../../src/modules/oauth_provider/schema/oauth.app.schema';
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware';
import { EncryptionService } from '../../../../src/libs/encryptor/encryptor';
import { loadConfigurationManagerConfig } from '../../../../src/modules/configuration_manager/config/config';
import { configPaths } from '../../../../src/modules/configuration_manager/paths/paths';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import { Org } from '../../../../src/modules/user_management/schema/org.schema';
import { UserActivities } from '../../../../src/modules/auth/schema/userActivities.schema';
import * as userAdminService from '../../../../src/modules/user_management/services/user-admin.service';
import type { Logger } from '../../../../src/libs/services/logger.service';

const JWT_SECRET = 'cm-signin-jwt-secret-0123456789abcdef';
const SCOPED_SECRET = 'cm-signin-scoped-secret-0123456789abcd';
const ISSUER = 'https://cm-signin.invalid';
const ADMIN = '64b000000000000000000001';
const MEMBER = '64b000000000000000000002';
const ORG = '64b0000000000000000000a1';
const now = () => Math.floor(Date.now() / 1000);

const chain = (result: unknown) => {
  const c: Record<string, unknown> = {};
  c.select = () => c;
  c.sort = () => c;
  c.lean = () => c;
  c.exec = async () => result;
  c.then = (ok: (v: unknown) => unknown, ko: (e: unknown) => unknown) => Promise.resolve(result).then(ok, ko);
  return c;
};

const session = (userId: string, role: 'admin' | 'member') =>
  jwt.sign({ userId, orgId: ORG, email: `${userId}@example.invalid`, role, iat: now() - 3600 }, JWT_SECRET, { expiresIn: '3h' });

const oauthAccess = (userId: string, scope: string) =>
  jwt.sign(
    { userId, orgId: ORG, iss: ISSUER, iat: now() - 60, exp: now() + 3600, jti: 'j1', scope, client_id: 'client-1', tokenType: 'oauth' },
    JWT_SECRET,
  );

const SCOPES = 'org:admin config:write config:read user:read';
const DELEGATED: Record<string, () => string> = {
  'admin PAT with config:write': () => `phpat_${oauthAccess(ADMIN, SCOPES)}`,
  'admin PAT with only config:write': () => `phpat_${oauthAccess(ADMIN, 'config:write')}`,
  'admin OAuth access token with config:write': () => oauthAccess(ADMIN, SCOPES),
  'service token (client_credentials)': () => `phsvc_${oauthAccess('client-1', SCOPES)}`,
};

// A mail server that accepts any connection, and the mail service's settings-reload endpoint.
async function startSinks() {
  const smtp = net.createServer((socket) => {
    socket.write('220 sink ESMTP\r\n');
    socket.on('data', (chunk) => {
      const line = chunk.toString().toUpperCase();
      if (line.startsWith('EHLO')) socket.write('250-sink\r\n250 OK\r\n');
      else if (line.startsWith('QUIT')) socket.end('221 bye\r\n');
      else socket.write('250 OK\r\n');
    });
    socket.on('error', () => undefined);
  });
  const mail = http.createServer((_req, res) => { res.statusCode = 200; res.end('{}'); });
  await Promise.all([
    new Promise<void>((r) => smtp.listen(0, '127.0.0.1', () => r())),
    new Promise<void>((r) => mail.listen(0, '127.0.0.1', () => r())),
  ]);
  return {
    smtpPort: (smtp.address() as AddressInfo).port,
    mailUrl: `http://127.0.0.1:${(mail.address() as AddressInfo).port}`,
    close: () => Promise.all([new Promise((r) => smtp.close(r)), new Promise((r) => mail.close(r))]),
  };
}

interface Write { path: string; body: unknown }
let sinks: Awaited<ReturnType<typeof startSinks>>;
const smtpBody = () => ({ host: '127.0.0.1', port: sinks.smtpPort, fromEmail: 'noreply@example.invalid' });
const WRITES: Write[] = [
  { path: '/configurationManager/smtpConfig', get body() { return smtpBody(); } },
  { path: '/configurationManager/authConfig/microsoft', body: { clientId: 'cid' } },
  { path: '/configurationManager/authConfig/google', body: { clientId: 'cid' } },
  { path: '/configurationManager/frontendPublicUrl', body: { url: 'https://frontend.example.invalid' } },
  { path: '/configurationManager/connectorPublicUrl', body: { url: 'https://connectors.example.invalid' } },
];

describe('Sign-in settings routes over HTTP with the real AuthMiddleware', function () {
  this.timeout(30000);
  let server: Server;
  let baseUrl: string;
  let writes: string[];
  let store: Map<string, string>;
  let savedSecretKey: string | undefined;
  let savedHide: string | undefined;

  before(async () => { sinks = await startSinks(); });
  after(async () => { await sinks.close(); });

  beforeEach(async () => {
    savedSecretKey = process.env.SECRET_KEY;
    savedHide = process.env.HIDE_SECRET_CONFIG;
    process.env.SECRET_KEY = process.env.SECRET_KEY || 'bebe4bcfae5282d8e801761bb8acf01feb1080daa1d12f0fb516bb12861487e6';
    writes = [];
    store = new Map();
    const kv = {
      get: async (k: string) => store.get(k) ?? null,
      set: async (k: string, v: string) => { writes.push(k); store.set(k, v); },
      delete: async (k: string) => { writes.push(`delete ${k}`); store.delete(k); },
    };

    sinon.stub(Users, 'findOne').callsFake(((q: { _id?: unknown }) => chain({
      _id: q._id, orgId: ORG, email: `${q._id}@example.invalid`, fullName: 'Test User',
      role: String(q._id) === ADMIN ? 'admin' : 'member', kind: 'user', isDeleted: false, isDisabled: false,
    })) as never);
    sinon.stub(Org, 'findOne').callsFake((() => chain({ _id: ORG, accountType: 'business' })) as never);
    sinon.stub(UserActivities, 'findOne').callsFake((() => chain(null)) as never);
    sinon.stub(OAuthAccessToken, 'findOne').callsFake((() => chain({ _id: 't1', lastUsedAt: new Date() })) as never);
    sinon.stub(OAuthAccessToken, 'updateOne').resolves({} as never);
    sinon.stub(OAuthApp, 'findOne').callsFake((() => chain({ createdBy: ADMIN, tokenIdentityUserId: null })) as never);
    sinon.stub(userAdminService, 'isUserOrgAdmin').callsFake(async (userId: string) => userId === ADMIN);

    const logger = { debug: sinon.stub(), info: sinon.stub(), warn: sinon.stub(), error: sinon.stub() } as unknown as Logger;
    const oauth = new OAuthTokenService(logger, { algorithm: 'HS256', secret: JWT_SECRET }, ISSUER);
    const events = { start: sinon.stub(), publishEvent: sinon.stub(), stop: sinon.stub() };
    const container = new Container();
    container.bind('KeyValueStoreService').toConstantValue(kv);
    container.bind('AppConfig').toConstantValue({
      aiBackend: 'http://127.0.0.1:1', cmBackend: 'http://127.0.0.1:1', communicationBackend: sinks.mailUrl, scopedJwtSecret: SCOPED_SECRET,
    });
    container.bind('EntitiesEventProducer').toConstantValue(events);
    container.bind('AiConfigEventProducer').toConstantValue(events);
    container.bind('SyncEventProducer').toConstantValue(events);
    container.bind('ConfigService').toConstantValue({ updateConfig: async () => ({ statusCode: 200, data: {} }) });
    container.bind('AuthMiddleware').toConstantValue(
      new AuthMiddleware(logger, new AuthTokenService(JWT_SECRET, SCOPED_SECRET), () => oauth),
    );

    const app = express();
    app.use(express.json());
    app.use('/configurationManager', createConfigurationManagerRouter(container));
    app.use((err: unknown, req: Request, res: Response, next: NextFunction) => ErrorMiddleware.handleError()(err as Error, req, res, next));
    server = await new Promise<Server>((resolve) => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
    baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });

  afterEach(async () => {
    sinon.restore();
    if (savedSecretKey === undefined) delete process.env.SECRET_KEY; else process.env.SECRET_KEY = savedSecretKey;
    if (savedHide === undefined) delete process.env.HIDE_SECRET_CONFIG; else process.env.HIDE_SECRET_CONFIG = savedHide;
    await new Promise((resolve) => server.close(resolve));
  });

  const post = async (w: Write, token: string | null) => {
    const res = await fetch(`${baseUrl}${w.path}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify(w.body),
    });
    return { status: res.status, text: await res.text() };
  };

  for (const w of WRITES) {
    describe(`POST ${w.path}`, () => {
      for (const kind of Object.keys(DELEGATED)) {
        it(`refuses ${kind} with the session-only 403 and saves nothing`, async () => {
          const r = await post(w, DELEGATED[kind]!());
          expect(r.status, r.text).to.equal(403);
          expect(r.text).to.contain('requires an interactive user session');
          expect(writes).to.deep.equal([]);
        });
      }

      it('refuses a request without a token (401) and a member session (403)', async () => {
        expect((await post(w, null)).status).to.equal(401);
        expect((await post(w, session(MEMBER, 'member'))).status).to.equal(403);
        expect(writes).to.deep.equal([]);
      });

      it('still lets an admin session save it', async () => {
        const r = await post(w, session(ADMIN, 'admin'));
        expect(r.status, r.text).to.equal(200);
        expect(writes.length, 'nothing was saved').to.be.greaterThan(0);
      });
    });
  }

  describe('SMTP config writes require an interactive session', () => {
    const ORIGINAL_HOST = 'smtp.original.example.invalid';
    it('a token cannot change the stored SMTP host: it stays as saved', async () => {
      const cfg = loadConfigurationManagerConfig();
      const sealed = EncryptionService.getInstance(cfg.algorithm, cfg.secretKey).encrypt(
        JSON.stringify({ host: ORIGINAL_HOST, port: 587, fromEmail: 'noreply@example.invalid' }),
      );
      store.set(configPaths.smtp, sealed);
      for (const kind of Object.keys(DELEGATED)) {
        const r = await post({ path: '/configurationManager/smtpConfig', body: smtpBody() }, DELEGATED[kind]!());
        expect(r.status, `${kind}: ${r.text}`).to.equal(403);
      }
      expect(store.get(configPaths.smtp)).to.equal(sealed);
      expect(writes).to.deep.equal([]);
    });

    const call = async (method: 'DELETE' | 'PUT', path: string, body: unknown, token: string | null) => {
      const res = await fetch(`${baseUrl}${path}`, {
        method,
        headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      return { status: res.status, text: await res.text() };
    };
    const seal = () => {
      const cfg = loadConfigurationManagerConfig();
      const sealed = EncryptionService.getInstance(cfg.algorithm, cfg.secretKey).encrypt(
        JSON.stringify({ host: ORIGINAL_HOST, port: 587, fromEmail: 'noreply@example.invalid' }),
      );
      store.set(configPaths.smtp, sealed);
      return sealed;
    };
    const OTHER_WRITES = [
      { method: 'DELETE' as const, path: '/configurationManager/smtpConfig', body: undefined },
      { method: 'PUT' as const, path: '/configurationManager/smtpConfig/enabled', body: { enabled: false } },
    ];

    for (const w of OTHER_WRITES) {
      it(`${w.method} ${w.path}: a token cannot delete or turn off the stored SMTP config`, async () => {
        const sealed = seal();
        for (const kind of Object.keys(DELEGATED)) {
          const r = await call(w.method, w.path, w.body, DELEGATED[kind]!());
          expect(r.status, `${kind}: ${r.text}`).to.equal(403);
          expect(r.text, kind).to.contain('requires an interactive user session');
        }
        expect((await call(w.method, w.path, w.body, null)).status).to.equal(401);
        expect((await call(w.method, w.path, w.body, session(MEMBER, 'member'))).status).to.equal(403);
        expect(store.get(configPaths.smtp)).to.equal(sealed);
        expect(writes).to.deep.equal([]);
      });
    }

    it('an admin session still turns the SMTP config off and deletes it', async () => {
      seal();
      const off = await call('PUT', '/configurationManager/smtpConfig/enabled', { enabled: false }, session(ADMIN, 'admin'));
      expect(off.status, off.text).to.equal(200);
      expect(writes).to.deep.equal([configPaths.smtp]);
      const del = await call('DELETE', '/configurationManager/smtpConfig', undefined, session(ADMIN, 'admin'));
      expect(del.status, del.text).to.equal(200);
      expect(store.has(configPaths.smtp)).to.equal(false);
    });
  });

  describe('reading sign-in settings with a token, secret masking switched off', () => {
    const SECRET = 'stored-secret-value-1234';
    for (const [name, path] of [['microsoft', configPaths.auth.microsoft], ['google', configPaths.auth.google]] as const) {
      for (const kind of ['admin PAT with config:write', 'admin OAuth access token with config:write']) {
        it(`GET authConfig/${name} with ${kind} does not return the stored client id`, async () => {
          process.env.HIDE_SECRET_CONFIG = 'false';
          const cfg = loadConfigurationManagerConfig();
          store.set(path, EncryptionService.getInstance(cfg.algorithm, cfg.secretKey).encrypt(JSON.stringify(name === 'microsoft' ? { clientId: SECRET, tenantId: SECRET } : { clientId: SECRET })));
          const res = await fetch(`${baseUrl}/configurationManager/authConfig/${name}?reveal=true`, {
            headers: { authorization: `Bearer ${DELEGATED[kind]!()}` },
          });
          expect(await res.text(), `HTTP ${res.status}`).to.not.contain(SECRET);
        });
      }
      it(`GET authConfig/${name} with an admin session and masking off still shows it (settings page)`, async () => {
        process.env.HIDE_SECRET_CONFIG = 'false';
        const cfg = loadConfigurationManagerConfig();
        store.set(path, EncryptionService.getInstance(cfg.algorithm, cfg.secretKey).encrypt(JSON.stringify({ clientId: SECRET })));
        const res = await fetch(`${baseUrl}/configurationManager/authConfig/${name}`, {
          headers: { authorization: `Bearer ${session(ADMIN, 'admin')}` },
        });
        expect(await res.text()).to.contain(SECRET);
      });
    }
  });
});
