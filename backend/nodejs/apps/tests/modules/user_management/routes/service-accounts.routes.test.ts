import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import mongoose from 'mongoose';
import { createServiceAccountsRouter } from '../../../../src/modules/user_management/routes/service-accounts.routes';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';

interface RouteLayer {
  route?: {
    path: string;
    methods: Record<string, boolean>;
    stack: { handle: (req: any, res: any, next: any) => unknown }[];
  };
}

function buildRouter() {
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
