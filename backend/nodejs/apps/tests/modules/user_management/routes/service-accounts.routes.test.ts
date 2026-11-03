import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import mongoose from 'mongoose';
import { createServiceAccountsRouter } from '../../../../src/modules/user_management/routes/service-accounts.routes';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import type { Logger } from '../../../../src/libs/services/logger.service';
import type { AppConfig } from '../../../../src/modules/tokens_manager/config/config';

interface RouteLayer {
  route?: {
    path: string;
    methods: Record<string, boolean>;
    stack: { handle: (req: any, res: any, next: any) => unknown }[];
  };
}

function buildRouter(maxRequestsPerMinute = 10_000) {
  const container = new Container();
  const controller = {
    list: sinon.stub(),
    create: sinon.stub(),
    get: sinon.stub(),
    update: sinon.stub(),
    remove: sinon.stub(),
  };
  container
    .bind('ServiceAccountsController')
    .toConstantValue(controller as any);
  container.bind('AuthMiddleware').toConstantValue({
    // The chain under test starts after authentication, so this just marks
    // the request as carrying whatever credential the test is about.
    authenticate: (_req: any, _res: any, next: any) => next(),
  } as any);
  // Typed to the members the router asks for, rather than widened, so a
  // change in either shape shows up here.
  const logger: Pick<Logger, 'info' | 'warn' | 'error' | 'debug'> = {
    info: sinon.stub(),
    warn: sinon.stub(),
    error: sinon.stub(),
    debug: sinon.stub(),
  };
  const config: Pick<AppConfig, 'maxOAuthClientRequestsPerMinute'> = {
    maxOAuthClientRequestsPerMinute: maxRequestsPerMinute,
  };
  container.bind('Logger').toConstantValue(logger);
  // Rate limited as the token and personal access token routers are. The
  // default ceiling is high enough never to trip, which keeps the other cases
  // about the gates they are named for.
  container.bind('AppConfig').toConstantValue(config);
  return { router: createServiceAccountsRouter(container), controller };
}

function routesOf(router: any): {
  method: string;
  path: string;
  stack: RouteLayer['route'];
}[] {
  return (router.stack as RouteLayer[])
    .filter((layer) => layer.route)
    .map((layer) => ({
      method: Object.keys(layer.route!.methods)[0]!.toUpperCase(),
      path: layer.route!.path,
      stack: layer.route!,
    }));
}

/**
 * Runs a route's middleware in order until one of them ends the request or
 * calls next with an error. Returns whichever happened.
 */
async function runChain(
  route: RouteLayer['route'],
  req: any,
): Promise<{ error?: any; reachedEnd: boolean }> {
  const res = {
    status: sinon.stub().returnsThis(),
    json: sinon.stub().returnsThis(),
    send: sinon.stub().returnsThis(),
  };

  for (const layer of route!.stack) {
    let nextError: any;
    let called = false;
    await new Promise<void>((resolve) => {
      const next = (err?: any) => {
        called = true;
        nextError = err;
        resolve();
      };
      const result = layer.handle(req, res as any, next) as unknown;
      if (result instanceof Promise) {
        result.then(
          () => {
            if (!called) resolve();
          },
          (err) => {
            nextError = err;
            resolve();
          },
        );
      } else if (!called) {
        resolve();
      }
    });
    if (nextError) return { error: nextError, reachedEnd: false };
    if (!called) return { reachedEnd: false };
  }
  return { reachedEnd: true };
}

/**
 * Different call sites end the chain differently — some await `lean()`, some
 * call `exec()` after it — so the stub satisfies both rather than pinning one.
 */
function stubUserLookup(doc: Record<string, unknown> | null) {
  // Self-chaining, and awaitable at any point in the chain.
  const chain: any = {
    select: () => chain,
    lean: () => chain,
    exec: async () => doc,
    then: (resolve: (value: unknown) => unknown) => resolve(doc),
  };
  return sinon.stub(Users, 'findOne').returns(chain);
}

describe('service account routes are gated for machine credentials too', () => {
  const orgId = new mongoose.Types.ObjectId().toString();
  const userId = new mongoose.Types.ObjectId().toString();

  afterEach(() => sinon.restore());

  it('registers every route behind a scope check', () => {
    const { router } = buildRouter();
    const routes = routesOf(router);

    expect(routes.map((r) => `${r.method} ${r.path}`).sort()).to.deep.equal([
      'DELETE /:id',
      'GET /',
      'GET /:id',
      'PATCH /:id',
      'POST /',
    ]);
  });

  it('refuses an admin OAuth token that lacks the scope', async () => {
    // An administrator's personal access token minted only to read a
    // knowledge base must not be able to create a principal, even though the
    // person behind it is an admin. userAdminCheck alone would let this
    // through, because it asks about the person and not the credential.
    const { router, controller } = buildRouter();
    const post = routesOf(router).find(
      (r) => r.method === 'POST' && r.path === '/',
    )!;

    const isAdmin = stubUserLookup({ role: 'admin' });

    const outcome = await runChain(post.stack, {
      user: { userId, orgId, isOAuth: true, oauthScopes: ['kb:read'] },
      body: { slug: 'nightly-sync', fullName: 'Nightly sync' },
      params: {},
      query: {},
    });

    expect(outcome.error).to.exist;
    expect(outcome.error.statusCode ?? outcome.error.status).to.equal(403);
    expect(controller.create.called).to.equal(false);
    // Refused on the credential, before anyone asked whether the person is an
    // admin.
    expect(isAdmin.called).to.equal(false);
  });

  it('lets an OAuth token through when it does carry the scope', async () => {
    const { router } = buildRouter();
    const post = routesOf(router).find(
      (r) => r.method === 'POST' && r.path === '/',
    )!;

    stubUserLookup({ role: 'admin' });

    const outcome = await runChain(post.stack, {
      user: { userId, orgId, isOAuth: true, oauthScopes: ['user:invite'] },
      body: { slug: 'nightly-sync', fullName: 'Nightly sync' },
      params: {},
      query: {},
    });

    expect(outcome.error).to.equal(undefined);
  });

  it('still refuses a session JWT belonging to someone who is not an admin', async () => {
    // Scopes are not enforced for session JWTs, so this is the case that
    // userAdminCheck exists for. Both gates are needed; neither covers the
    // other.
    const { router, controller } = buildRouter();
    const post = routesOf(router).find(
      (r) => r.method === 'POST' && r.path === '/',
    )!;

    stubUserLookup({ role: 'member' });

    const outcome = await runChain(post.stack, {
      user: { userId, orgId, isOAuth: false },
      body: { slug: 'nightly-sync', fullName: 'Nightly sync' },
      params: {},
      query: {},
    });

    expect(outcome.error).to.exist;
    expect(controller.create.called).to.equal(false);
  });
});

/**
 * Runs the router's own middleware — the layers registered with `router.use`,
 * which is where the limiter sits. `runChain` above walks a matched route's
 * stack and so never reaches it.
 */
/** The request members the limiter reads: who is calling, and from where. */
interface LimiterRequest {
  user?: { userId: string; orgId: string };
  ip: string;
  headers: Record<string, string>;
  method: string;
  url: string;
}

/** The response members it writes when it refuses one. */
interface LimiterResponse {
  status: (code: number) => LimiterResponse;
  json: () => LimiterResponse;
  send: () => LimiterResponse;
  end: () => LimiterResponse;
  setHeader: () => LimiterResponse;
  getHeader: () => undefined;
  removeHeader: () => LimiterResponse;
  headersSent: boolean;
}

type MiddlewareLayer = {
  route?: unknown;
  handle: (
    req: LimiterRequest,
    res: LimiterResponse,
    next: () => void,
  ) => unknown;
};

async function runRouterMiddleware(
  router: ReturnType<typeof createServiceAccountsRouter>,
  req: LimiterRequest,
): Promise<{ status?: number }> {
  const recorded: { status?: number } = {};
  const res: LimiterResponse = {
    status: (code: number) => {
      recorded.status = code;
      return res;
    },
    json: () => res,
    send: () => res,
    end: () => res,
    setHeader: () => res,
    getHeader: () => undefined,
    removeHeader: () => res,
    headersSent: false,
  };

  const layers = (
    router as unknown as { stack: MiddlewareLayer[] }
  ).stack.filter((layer) => layer.route === undefined);

  for (const layer of layers) {
    let passed = false;
    await new Promise<void>((resolve) => {
      const next = () => {
        passed = true;
        resolve();
      };
      const maybe = layer.handle(req, res, next);
      if (maybe instanceof Promise) void maybe.finally(() => resolve());
      else if (!passed) setImmediate(resolve);
    });
    if (recorded.status !== undefined) break;
  }

  return recorded;
}

describe('service account routes are rate limited', () => {
  it('puts a ceiling in front of the routes, as the token routes have', () => {
    // Every route here is admin-only, so this is not about untrusted callers:
    // it bounds what a stolen admin credential can do in one burst, since each
    // created account is a principal with its own view of the organisation and
    // an address that is taken for good. Identified by the limiter's own
    // `resetKey`/`getKey` rather than by a function name, which is empty.
    const { router } = buildRouter();
    type MaybeLimiter = ((...args: unknown[]) => unknown) & {
      resetKey?: unknown;
      getKey?: unknown;
    };
    const stack = (router as unknown as { stack: { handle: unknown }[] }).stack;
    const limiters = stack.filter((layer) => {
      if (typeof layer.handle !== 'function') return false;
      const handle = layer.handle as MaybeLimiter;
      return (
        typeof handle.resetKey === 'function' &&
        typeof handle.getKey === 'function'
      );
    });

    expect(limiters.length).to.equal(1);
  });

  it('refuses a second request from the same administrator once the ceiling is one', async () => {
    // Registered is not the same as enforced, so this drives the middleware
    // rather than inspecting it.
    const { router } = buildRouter(1);
    const userId = new mongoose.Types.ObjectId().toString();
    const req = () => ({
      user: { userId, orgId: new mongoose.Types.ObjectId().toString() },
      ip: '203.0.113.7',
      headers: {},
      method: 'GET',
      url: '/',
    });

    const first = await runRouterMiddleware(router, req());
    const second = await runRouterMiddleware(router, req());

    expect(first.status).to.equal(undefined);
    expect(second.status).to.equal(429);
  });
});
