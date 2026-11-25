/**
 * CPS TS-1/TS-2: every route of the Node toolsets router checks the OAuth scope the
 * Python route it proxies checks (backend/python/app/api/routes/toolsets.py, read
 * from the file so the two cannot drift); Node-only routes are classified below.
 * Each route's middleware chain runs as the router builds it, up to the controller.
 */
import 'reflect-metadata';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { expect } from 'chai';
import { createToolsetsRouter } from '../../../../src/modules/toolsets/routes/toolsets_routes';
import { OAuthScopeNames } from '../../../../src/libs/enums/oauth-scopes.enum';
import { ForbiddenError } from '../../../../src/libs/errors/http.errors';

type ScopeName = keyof typeof OAuthScopeNames;
type Method = 'get' | 'post' | 'put' | 'delete' | 'patch';

const PYTHON_ROUTES = resolve(
  __dirname,
  '../../../../../../python/app/api/routes/toolsets.py',
);

/** Node routes with no Python route of the same shape (pre-instance API): scoped by what they do. */
const NODE_ONLY: Record<string, ScopeName> = {
  'post /': 'CONNECTOR_WRITE',
  'get /:_/status': 'CONNECTOR_READ',
  'get /:_/config': 'CONNECTOR_READ',
  'post /:_/config': 'CONNECTOR_WRITE',
  'put /:_/config': 'CONNECTOR_WRITE',
  'delete /:_/config': 'CONNECTOR_DELETE',
  'post /:_/reauthenticate': 'CONNECTOR_WRITE',
  // Main's route table (#3596) reads it as a read of the toolset: it only builds the provider's URL.
  'get /:_/oauth/authorize': 'CONNECTOR_READ',
};

/**
 * Routes whose Node scope main #3596 chose by what the route does rather than by the Python twin's verb:
 * the OAuth callback completes a connection (write), removing credentials is a delete, and building an
 * instance's authorization URL changes nothing (read).
 */
const NODE_BY_ACTION: Record<string, ScopeName> = {
  'get /oauth/callback': 'TOOLSET_WRITE',
  'delete /instances/:_/credentials': 'TOOLSET_DELETE',
  'get /instances/:_/oauth/authorize': 'TOOLSET_READ',
};

const shape = (method: string, path: string): string =>
  `${method.toLowerCase()} ${path.replace(/\{[^}]+\}|:[A-Za-z_]+/g, ':_')}`;

function pythonScopes(): Record<string, ScopeName> {
  const source = readFileSync(PYTHON_ROUTES, 'utf-8');
  const found: Record<string, ScopeName> = {};
  const decorator =
    /@router\.(get|post|put|delete|patch)\(\s*"([^"]+)"[^\n]*?require_scopes\(OAuthScopes\.([A-Z_]+)\)/g;
  for (const [, method, path, scope] of source.matchAll(decorator)) {
    found[shape(method as string, path as string)] = scope as ScopeName;
  }
  return found;
}

interface Route {
  method: Method;
  path: string;
  stack: Array<{ handle: (req: any, res: any, next: (e?: unknown) => void) => unknown }>;
}

function routes(): Route[] {
  const container: any = {
    get: (key: string) =>
      key === 'AuthMiddleware'
        ? { authenticate: (_req: any, _res: any, next: () => void) => next() }
        : { connectorBackend: 'http://connectors.invalid' },
    isBound: () => false,
  };
  const router: any = createToolsetsRouter(container);
  return router.stack
    .filter((layer: any) => layer.route)
    .flatMap((layer: any) =>
      Object.keys(layer.route.methods).map((method) => ({
        method: method as Method,
        path: layer.route.path as string,
        stack: layer.route.stack,
      })),
    );
}

/** The first error the route's middlewares raise before its controller, or null when they all pass. */
async function guardError(route: Route, user: Record<string, unknown>): Promise<unknown> {
  const req: any = { user, body: {}, query: {}, params: {}, headers: {}, path: route.path };
  const res: any = {};
  for (const layer of route.stack.slice(0, -1)) {
    const error = await new Promise<unknown>((done) => {
      Promise.resolve(layer.handle(req, res, (e?: unknown) => done(e ?? null))).catch(done);
    });
    if (error !== null) return error;
  }
  return null;
}

const oauth = (...scopes: string[]) => ({
  userId: 'u1',
  orgId: 'o1',
  isOAuth: true,
  oauthScopes: scopes,
});

describe('toolsets router scopes (CPS TS-1, TS-2)', () => {
  const python = pythonScopes();
  const expected = (route: Route): ScopeName => {
    const key = shape(route.method, route.path);
    if (NODE_BY_ACTION[key] !== undefined) return NODE_BY_ACTION[key];
    const scope = python[key] ?? NODE_ONLY[key];
    if (scope === undefined) {
      throw new Error(`${key}: classify this route (a Python twin or NODE_ONLY)`);
    }
    // Main #3596 gave the Node router the toolset scope family; the Python route still checks the
    // connector family. Same verb on both sides: connector:read on Python is toolset:read on Node.
    return scope.replace(/^CONNECTOR_/, 'TOOLSET_') as ScopeName;
  };

  it('TS-2 reads the Python routes it mirrors', () => {
    expect(Object.keys(python).length).to.be.greaterThan(20);
    for (const key of Object.keys(NODE_ONLY)) {
      expect(python[key], `${key} has a Python twin now`).to.equal(undefined);
    }
  });

  it('TS-1 an OAuth token without the route scope is refused before the controller', async () => {
    for (const route of routes()) {
      const error = await guardError(route, oauth('org:read'));
      expect(error, `${route.method} ${route.path}`).to.be.instanceOf(ForbiddenError);
    }
  });

  it('TS-2 the scope each route requires is its Python twin\'s, in the toolset family', async () => {
    for (const route of routes()) {
      const scope = OAuthScopeNames[expected(route)];
      const error = await guardError(route, oauth(scope));
      expect(error instanceof ForbiddenError, `${route.method} ${route.path} with ${scope}`).to.equal(false);
    }
  });

  it('TS-1 a session token passes the scope check, as on every router', async () => {
    for (const route of routes()) {
      const error = await guardError(route, { userId: 'u1', orgId: 'o1' });
      expect(error instanceof ForbiddenError, `${route.method} ${route.path}`).to.equal(false);
    }
  });
});
