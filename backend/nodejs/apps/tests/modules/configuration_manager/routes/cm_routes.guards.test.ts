import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import type { NextFunction, Request, RequestHandler, Response } from 'express';
import { Container } from 'inversify';
import { createConfigurationManagerRouter } from '../../../../src/modules/configuration_manager/routes/cm_routes';
import { userAdminCheck } from '../../../../src/modules/user_management/middlewares/userAdminCheck';

// Every configuration route has to say who may call it. A route added without
// userAdminCheck would let any signed-in member read or change workspace
// settings, so this walks the real router and fails on the first one that does.

// Routes a member may call. Each is documented as not needing admin and
// returns nothing a member must not see.
const MEMBER_ROUTES = new Set([
  'GET /smtpConfig/status',
  'GET /platform/feature-flags/effective',
  'GET /ai-models/available/:modelType',
  'GET /frontendPublicUrl',
  'GET /connectorPublicUrl',
]);

// Routes members can call today that can return secrets. Not safe; kept open
// only until the product decision listed under "Left as they are" in PR #3502.
// GET /web-search returns web-search API keys unmasked unless
// HIDE_SECRET_CONFIG=true; the agent builder calls it for members.
const MEMBER_ROUTES_EXPOSING_SECRETS_PENDING_DECISION = new Set(['GET /web-search']);

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
  const memberMayCall = (r: InspectedRoute) =>
    MEMBER_ROUTES.has(r.id) || MEMBER_ROUTES_EXPOSING_SECRETS_PENDING_DECISION.has(r.id);

  it('finds the routes it is meant to check', () => {
    expect(routes.length).to.be.greaterThan(60);
    const ids = routes.map((r) => r.id);
    for (const id of [...MEMBER_ROUTES, ...MEMBER_ROUTES_EXPOSING_SECRETS_PENDING_DECISION]) {
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
