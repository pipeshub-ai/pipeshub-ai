import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import type { NextFunction, Request, RequestHandler, Response } from 'express';
import type { AddressInfo } from 'net';
import type { Server } from 'http';
import mongoose from 'mongoose';
import { Container } from 'inversify';
import { createConfigurationManagerRouter } from '../../../../src/modules/configuration_manager/routes/cm_routes';
import { userAdminCheck } from '../../../../src/modules/user_management/middlewares/userAdminCheck';
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware';
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service';
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware';
import { authJwtGenerator } from '../../../../src/libs/utils/createJwt';
import { EncryptionService } from '../../../../src/libs/encryptor/encryptor';
import type { Logger } from '../../../../src/libs/services/logger.service';
import { loadConfigurationManagerConfig } from '../../../../src/modules/configuration_manager/config/config';
import { configPaths } from '../../../../src/modules/configuration_manager/paths/paths';
import { CONFIG_SECRET_PLACEHOLDER } from '../../../../src/modules/configuration_manager/utils/maskConfigSecrets';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import { UserActivities } from '../../../../src/modules/auth/schema/userActivities.schema';

// Every configuration route has to say who may call it. A route added without
// userAdminCheck would let any signed-in member read or change workspace
// settings, so this walks the real router and fails on the first one that does.

// Routes a member may call. Each is documented as not needing admin and
// returns nothing a member must not see. GET /web-search masks API keys for
// members; the HTTP test below checks it.
const MEMBER_ROUTES = new Set([
  'GET /smtpConfig/status',
  'GET /platform/feature-flags/effective',
  'GET /ai-models/available/:modelType',
  'GET /frontendPublicUrl',
  'GET /connectorPublicUrl',
  'GET /web-search',
]);

interface RouteLayer {
  route?: {
    path: string;
    methods: Record<string, boolean>;
    stack: Array<{ handle: RequestHandler }>;
  };
}

interface InspectedRoute {
  id: string;
  path: string;
  handlers: RequestHandler[];
}

describe('Configuration manager routes: who may call them', () => {
  const authenticate: RequestHandler = function authenticate(
    _req: Request,
    _res: Response,
    next: NextFunction,
  ) {
    next();
  };
  const serviceTokenCheck: RequestHandler = function serviceTokenCheck(
    _req: Request,
    _res: Response,
    next: NextFunction,
  ) {
    next();
  };
  let routes: InspectedRoute[];

  before(() => {
    const container = new Container();
    const events = { start: sinon.stub(), publishEvent: sinon.stub(), stop: sinon.stub() };
    container.bind('KeyValueStoreService').toConstantValue({});
    container.bind('AppConfig').toConstantValue({ aiBackend: 'http://ai', cmBackend: 'http://cm' });
    container.bind('EntitiesEventProducer').toConstantValue(events);
    container.bind('AiConfigEventProducer').toConstantValue(events);
    container.bind('SyncEventProducer').toConstantValue(events);
    container.bind('ConfigService').toConstantValue({});
    container.bind('AuthMiddleware').toConstantValue({
      authenticate,
      scopedTokenValidator: () => serviceTokenCheck,
    });
    container.bind('SamlController').toConstantValue({});

    const router = createConfigurationManagerRouter(container);
    routes = (router.stack as unknown as RouteLayer[]).flatMap((layer) => {
      const route = layer.route;
      if (!route) return [];
      return Object.keys(route.methods).map((method) => ({
        id: `${method.toUpperCase()} ${route.path}`,
        path: route.path,
        handlers: route.stack.map((s) => s.handle),
      }));
    });
  });

  const isInternal = (r: InspectedRoute) => r.path.startsWith('/internal/');
  const memberMayCall = (r: InspectedRoute) => MEMBER_ROUTES.has(r.id);

  it('finds the routes it is meant to check', () => {
    expect(routes.length).to.be.greaterThan(60);
    const ids = routes.map((r) => r.id);
    for (const id of MEMBER_ROUTES) {
      expect(ids, id).to.include(id);
    }
  });

  it('checks the service token first on every internal route, and never takes a user session', () => {
    const wrong = routes
      .filter(isInternal)
      .filter((r) => r.handlers[0] !== serviceTokenCheck || r.handlers.includes(authenticate))
      .map((r) => r.id);

    expect(wrong).to.deep.equal([]);
  });

  it('puts every other route behind a signed-in user', () => {
    const open = routes
      .filter((r) => !isInternal(r))
      .filter((r) => r.handlers[0] !== authenticate)
      .map((r) => r.id);

    expect(open).to.deep.equal([]);
  });

  it('requires an admin on every user route except the listed member routes', () => {
    const missingAdmin = routes
      .filter((r) => !isInternal(r) && !memberMayCall(r))
      .filter((r) => !r.handlers.includes(userAdminCheck))
      .map((r) => r.id);

    expect(missingAdmin).to.deep.equal([]);
  });

  it('checks for an admin before the handler that does the work', () => {
    const late = routes
      .filter((r) => r.handlers.includes(userAdminCheck))
      .filter((r) => r.handlers.indexOf(userAdminCheck) === r.handlers.length - 1)
      .map((r) => r.id);

    expect(late).to.deep.equal([]);
  });
});

// Serves the real configuration router over HTTP with the real AuthMiddleware,
// real signed session tokens and real encryption of the stored settings. Only
// Mongo and the key-value store are faked.
describe('GET /configurationManager/web-search over HTTP', () => {
  const JWT_SECRET = 'cm-web-search-jwt-secret';
  const SCOPED_SECRET = 'cm-web-search-scoped-secret';
  const orgId = new mongoose.Types.ObjectId().toHexString();
  const admin = { _id: new mongoose.Types.ObjectId().toHexString(), role: 'admin' as const };
  const member = { _id: new mongoose.Types.ObjectId().toHexString(), role: 'member' as const };
  const people = [admin, member];
  const storedProviders = [
    { provider: 'serper', providerKey: 'serper', configuration: { apiKey: 'serper-real-key' }, isDefault: true },
    { provider: 'tavily', providerKey: 'tavily', configuration: { apiKey: 'tavily-real-key' }, isDefault: false },
  ];

  let server: Server;
  let baseUrl: string;
  let savedSecretKey: string | undefined;
  let savedHideSecrets: string | undefined;

  // Resolves like a Mongoose query whether it is awaited directly or via exec().
  function query<T>(value: T) {
    const q = {
      select: () => q,
      lean: () => q,
      sort: () => q,
      exec: () => Promise.resolve(value),
      then: <R>(ok: (v: T) => R, fail?: (e: unknown) => R) => Promise.resolve(value).then(ok, fail),
    };
    return q;
  }

  function sessionFor(person: { _id: string; role: 'admin' | 'member' }) {
    return authJwtGenerator(
      JWT_SECRET,
      `${person.role}@acme.test`,
      person._id,
      orgId,
      person.role,
      'business',
      person.role,
    );
  }

  interface ProviderView {
    provider: string;
    isDefault: boolean;
    configuration: { apiKey?: string };
  }

  async function listProviders(token: string) {
    const response = await fetch(`${baseUrl}/configurationManager/web-search`, {
      headers: { authorization: `Bearer ${token}` },
    });
    const body = (await response.json()) as { providers?: ProviderView[] };
    return {
      status: response.status,
      cacheControl: response.headers.get('cache-control'),
      providers: body.providers ?? [],
      raw: JSON.stringify(body),
    };
  }

  beforeEach(async () => {
    savedSecretKey = process.env.SECRET_KEY;
    savedHideSecrets = process.env.HIDE_SECRET_CONFIG;
    process.env.SECRET_KEY = process.env.SECRET_KEY || 'cm-web-search-secret';
    delete process.env.HIDE_SECRET_CONFIG;

    const cmConfig = loadConfigurationManagerConfig();
    const sealed = EncryptionService.getInstance(cmConfig.algorithm, cmConfig.secretKey).encrypt(
      JSON.stringify({ providers: storedProviders }),
    );
    const kv = {
      get: (key: string) => Promise.resolve(key === configPaths.webSearch ? sealed : null),
    };

    sinon.stub(Users, 'findOne').callsFake(((filter: { _id?: unknown; orgId?: unknown }) => {
      const found = people.find((p) => p._id === String(filter._id));
      const matchesOrg = filter.orgId === undefined || String(filter.orgId) === orgId;
      return query(found && matchesOrg ? { ...found, orgId, isDeleted: false, kind: 'user' } : null);
    }) as unknown as typeof Users.findOne);
    sinon.stub(UserActivities, 'findOne').callsFake((() => query(null)) as unknown as typeof UserActivities.findOne);

    const logger = {
      debug: sinon.stub(),
      info: sinon.stub(),
      warn: sinon.stub(),
      error: sinon.stub(),
    } as unknown as Logger;
    const events = { start: sinon.stub(), publishEvent: sinon.stub(), stop: sinon.stub() };
    const container = new Container();
    container.bind('KeyValueStoreService').toConstantValue(kv);
    container.bind('AppConfig').toConstantValue({ aiBackend: 'http://ai', cmBackend: 'http://cm' });
    container.bind('EntitiesEventProducer').toConstantValue(events);
    container.bind('AiConfigEventProducer').toConstantValue(events);
    container.bind('SyncEventProducer').toConstantValue(events);
    container.bind('ConfigService').toConstantValue({});
    container
      .bind('AuthMiddleware')
      .toConstantValue(new AuthMiddleware(logger, new AuthTokenService(JWT_SECRET, SCOPED_SECRET)));
    container.bind('SamlController').toConstantValue({});

    const app = express();
    app.use(express.json());
    app.use('/configurationManager', createConfigurationManagerRouter(container));
    app.use(ErrorMiddleware.handleError());
    server = await new Promise<Server>((resolve) => {
      const s = app.listen(0, '127.0.0.1', () => resolve(s));
    });
    baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });

  afterEach(async () => {
    sinon.restore();
    if (savedSecretKey === undefined) delete process.env.SECRET_KEY;
    else process.env.SECRET_KEY = savedSecretKey;
    if (savedHideSecrets === undefined) delete process.env.HIDE_SECRET_CONFIG;
    else process.env.HIDE_SECRET_CONFIG = savedHideSecrets;
    await new Promise((resolve) => server.close(resolve));
  });

  it('shows a member which providers are configured but masks their API keys, even with HIDE_SECRET_CONFIG off', async () => {
    const asMember = await listProviders(sessionFor(member));

    expect(asMember.status).to.equal(200);
    expect(asMember.raw).to.not.include('real-key');
    expect(asMember.providers.map((p) => [p.provider, p.isDefault])).to.deep.equal([
      ['duckduckgo', false],
      ['serper', true],
      ['tavily', false],
    ]);
    expect(asMember.providers.slice(1).map((p) => p.configuration.apiKey)).to.deep.equal([
      CONFIG_SECRET_PLACEHOLDER,
      CONFIG_SECRET_PLACEHOLDER,
    ]);

    const asAdmin = await listProviders(sessionFor(admin));

    expect(asAdmin.status).to.equal(200);
    expect(asAdmin.providers.slice(1).map((p) => p.configuration.apiKey)).to.deep.equal([
      'serper-real-key',
      'tavily-real-key',
    ]);
  });

  it('tells browsers and proxies not to keep a copy of settings answers, which depend on who asked', async () => {
    const asAdmin = await listProviders(sessionFor(admin));
    const asMember = await listProviders(sessionFor(member));
    const smtp = await fetch(`${baseUrl}/configurationManager/smtpConfig`, {
      headers: { authorization: `Bearer ${sessionFor(admin)}` },
    });

    expect(asAdmin.cacheControl).to.equal('no-store');
    expect(asMember.cacheControl).to.equal('no-store');
    expect(smtp.status).to.equal(200);
    expect(smtp.headers.get('cache-control')).to.equal('no-store');
  });
});
