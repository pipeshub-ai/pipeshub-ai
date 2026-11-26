import 'reflect-metadata';
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import yaml from 'js-yaml';
import express from 'express';
import sinon from 'sinon';
import { Application } from '../../src/app';
import { editionBoot } from '../../src/config';
import { internalListenerConfig } from '../../src/modules/agent-harness/transport/internal-listener';
import { noopHarnessEdition } from '../../src/modules/agent-harness/edition';
import { ConfigService } from '../../src/modules/tokens_manager/services/cm.service';
import { KeyValueStoreService } from '../../src/libs/services/keyValueStore.service';
import * as cmConfig from '../../src/modules/configuration_manager/config/config';

/**
 * API-01 (C12.01, OWD-13 as amended): the harness's public surface and
 * pipeshub-openapi.yaml do not drift apart, in either direction. A route the
 * server answers must be documented with its method and path parameters, and
 * a documented harness operation must be served (AGENTS.md: a mismatch is blocking).
 *
 * The routes come from the server's own wiring: Application.configureRoutes()
 * runs against stub containers and every Express mount it makes is recorded, so
 * a router added to app.ts is checked without anyone listing it here.
 */

// Every injected dependency is a callable that returns itself, so each router
// registers its true paths and methods without any real service behind it.
// A number where a router does arithmetic on a config value (main's per-IP auth limit multiplies its own).
const asPrimitive = (hint: string): unknown => (hint === 'string' ? '' : 1);
const anything: unknown = new Proxy(function stub() {}, {
  get: (_target, prop) =>
    prop === 'then' ? undefined : prop === Symbol.toPrimitive ? asPrimitive : anything,
  apply: () => anything,
  construct: () => anything as object,
});
const stubContainer = {
  get: () => anything,
  isBound: () => true,
  getAll: () => [],
};

/** The OWD-13 surface: chat conversations and runs, agent conversations, harness admin, control plane, MCP hub. */
const HARNESS_SCOPES = [
  /^\/conversations(\/|$)/,
  /^\/agents\/(\{agentKey\}\/)?(conversations|runs)(\/|$)/,
  /^\/agents\/(\{agentKey\}\/export|import)$/,
  /^\/agent-harness\//,
  /^\/control-plane\//,
  /^\/mcp-servers\/instances\/\{instanceId\}\/(manifest|runner)$/,
];
const inScope = (path: string): boolean => HARNESS_SCOPES.some((scope) => scope.test(path));

const API_BASE = '/api/v1';
const HTTP_METHODS = ['get', 'put', 'post', 'delete', 'patch', 'options', 'head', 'trace'];

interface Layer {
  route?: { path: string | string[]; methods: Record<string, boolean> };
}
interface RouterLike {
  stack: Layer[];
}
interface Mount {
  parent: RouterLike;
  path: string;
  child: RouterLike;
}

const isRouter = (handler: unknown): handler is RouterLike =>
  typeof handler === 'function' && Array.isArray((handler as Partial<RouterLike>).stack);

/** Express ':param' path to an OpenAPI template relative to the /api/v1 server base. */
const toTemplate = (path: string): string => {
  const joined = path.replace(/\/+/g, '/').replace(/(.)\/$/, '$1');
  const relative = joined.startsWith(`${API_BASE}/`) ? joined.slice(API_BASE.length) : joined;
  return relative.replace(/:([A-Za-z0-9_]+)/g, '{$1}');
};

let cachedRoutes: string[] | undefined;

/** Every "METHOD /path" the main app and the internal listener serve. */
function servedRoutes(): string[] {
  if (cachedRoutes) return cachedRoutes;
  const routerProto = express.Router as unknown as { use: (...args: unknown[]) => unknown };
  const originalUse = routerProto.use;
  const mounts: Mount[] = [];
  const apps = new Set<{ _router?: RouterLike }>();
  const appProto = express.application as unknown as { use: (...args: unknown[]) => unknown };
  const originalAppUse = appProto.use;
  routerProto.use = function recordMount(this: RouterLike, ...args: unknown[]) {
    const path = typeof args[0] === 'string' ? args[0] : '/';
    for (const handler of args.flat()) {
      if (isRouter(handler)) mounts.push({ parent: this, path, child: handler });
    }
    return originalUse.apply(this, args);
  };
  appProto.use = function recordApp(this: { _router?: RouterLike }, ...args: unknown[]) {
    apps.add(this);
    return originalAppUse.apply(this, args);
  };

  // Process-wide singletons some routers reach for are stubbed rather than
  // built, so this test leaves no configured instance behind for other tests.
  const singletons = sinon.createSandbox();
  singletons.stub(ConfigService, 'getInstance').returns(anything as ConfigService);
  singletons.stub(KeyValueStoreService, 'getInstance').returns(anything as KeyValueStoreService);
  singletons.stub(cmConfig, 'loadConfigurationManagerConfig').returns(anything as cmConfig.ConfigurationManagerConfig);
  // The edition boot's own routes need the services its initialize() starts, which this test
  // does not; the agent-harness routes this test reads are mounted by the app itself.
  singletons.stub(editionBoot, 'mountRoutes');
  // Every DI container the Application reads (any field named *Container) is the stub,
  // so a container added to app.ts later needs no change here.
  const server = new Proxy(Object.create(Application.prototype) as Record<string, unknown>, {
    get: (target, prop, receiver) =>
      typeof prop === 'string' && prop.endsWith('Container') && !(prop in target)
        ? stubContainer
        : Reflect.get(target, prop, receiver),
  }) as Record<string, unknown> & { configureRoutes: () => void };
  try {
    server.app = express();
    server.drains = [];
    server.internalListener = internalListenerConfig({});
    // The routes of this spec: an edition's own routers and control-plane routes are documented with the edition.
    server.edition = noopHarnessEdition;
    server.configureRoutes();
  } finally {
    routerProto.use = originalUse;
    appProto.use = originalAppUse;
    singletons.restore();
  }

  const routes: string[] = [];
  const walk = (router: RouterLike, prefix: string, seen: Set<RouterLike>): void => {
    if (seen.has(router)) return;
    const within = new Set(seen).add(router);
    for (const layer of router.stack) {
      if (!layer.route) continue;
      const paths = Array.isArray(layer.route.path) ? layer.route.path : [layer.route.path];
      for (const path of paths) {
        for (const method of Object.keys(layer.route.methods)) {
          routes.push(`${method === '_all' ? 'ALL' : method.toUpperCase()} ${toTemplate(`${prefix}/${path}`)}`);
        }
      }
    }
    for (const mount of mounts.filter((m) => m.parent === router)) {
      walk(mount.child, `${prefix}/${mount.path}`, within);
    }
  };
  for (const app of apps) {
    if (app._router) walk(app._router, '', new Set());
  }
  cachedRoutes = [...new Set(routes)];
  return cachedRoutes;
}


interface Parameter {
  $ref?: string;
  name?: string;
  in?: string;
  required?: boolean;
}
type PathItem = Record<string, unknown> & { parameters?: Parameter[] };
interface Spec {
  paths: Record<string, PathItem>;
  components?: { parameters?: Record<string, Parameter> };
}

let cachedSpec: Spec | undefined;
const spec = (): Spec => {
  cachedSpec ??= yaml.load(
    readFileSync(join(__dirname, '../../src/modules/api-docs/pipeshub-openapi.yaml'), 'utf8'),
  ) as Spec;
  return cachedSpec;
};

const documented = (): Set<string> =>
  new Set(
    Object.entries(spec().paths).flatMap(([path, item]) =>
      Object.keys(item)
        .filter((key) => HTTP_METHODS.includes(key))
        .map((method) => `${method.toUpperCase()} ${path}`),
    ),
  );

const resolveParameter = (parameter: Parameter): Parameter => {
  if (!parameter.$ref) return parameter;
  const name = parameter.$ref.replace('#/components/parameters/', '');
  return spec().components?.parameters?.[name] ?? parameter;
};

const harnessRoutes = (): string[] => servedRoutes().filter((route) => inScope(route.split(' ')[1]!));

describe('C-10: agent-harness routes are in the OpenAPI spec (API-01, C12.01: no drift either way)', function () {
  // Building every router of the app takes a while on a loaded host.
  this.timeout(120_000);

  it('finds the routes it checks, from the app wiring (the build did not silently register nothing)', () => {
    const routes = harnessRoutes();
    expect(routes.length).to.be.greaterThan(150);
    for (const route of [
      'POST /conversations/runs',
      'POST /conversations/{sessionId}/runs',
      'POST /agents/{agentKey}/runs',
      'POST /agents/{agentKey}/conversations/{sessionId}/runs',
      'POST /conversations/create',
      'POST /agents/{agentKey}/conversations/stream',
      'POST /agent-harness/internal/runs/{runId}/commit',
      'GET /agent-harness/admin/dependencies',
      'PUT /agent-harness/model-accounts/coding-engine',
      'POST /agent-harness/schedules/{scheduleId}/run',
      'POST /agent-harness/triggers/{scheduleId}/webhook',
      'DELETE /agent-harness/publications/{publicationId}',
      'PATCH /agent-harness/memories/{memoryId}',
      'POST /control-plane/internal/capabilities/sync',
    ]) {
      expect(routes).to.include(route);
    }
  });

  it("S1R: neither serves nor documents stage 1's request token or request-task budget routes", () => {
    const retired = (route: string): boolean =>
      route === 'POST /agent-harness/gateway-tokens' || route.includes('/agent-harness/internal/requests/');
    expect(servedRoutes().filter(retired)).to.deep.equal([]);
    expect([...documented()].filter(retired)).to.deep.equal([]);
  });

  it('API-01: no harness route answers every method (router.all hides what the spec should say)', () => {
    expect(harnessRoutes().filter((route) => route.startsWith('ALL '))).to.deep.equal([]);
  });

  it('documents every route with its method (API-01: no route missing from the spec)', () => {
    const operations = documented();
    const missing = harnessRoutes().filter((route) => !operations.has(route));
    expect(missing, 'add these to pipeshub-openapi.yaml').to.deep.equal([]);
  });

  it('API-01: every documented harness operation is served (no spec path without a route)', () => {
    const served = new Set(servedRoutes());
    const orphaned = [...documented()].filter(
      (operation) => inScope(operation.split(' ')[1]!) && !served.has(operation),
    );
    expect(orphaned, 'remove these from pipeshub-openapi.yaml or serve them').to.deep.equal([]);
  });

  it('API-01: every documented harness operation declares exactly the path parameters in its path', () => {
    const wrong: string[] = [];
    for (const [path, item] of Object.entries(spec().paths)) {
      if (!inScope(path)) continue;
      const inPath = [...path.matchAll(/\{([^}]+)\}/g)].map((match) => match[1]!).sort();
      for (const method of Object.keys(item).filter((key) => HTTP_METHODS.includes(key))) {
        const operation = item[method] as { parameters?: Parameter[] };
        const declared = [...(item.parameters ?? []), ...(operation.parameters ?? [])]
          .map(resolveParameter)
          .filter((parameter) => parameter.in === 'path');
        const names = [...new Set(declared.map((parameter) => parameter.name!))].sort();
        const optional = declared.filter((parameter) => parameter.required !== true);
        if (JSON.stringify(names) !== JSON.stringify(inPath) || optional.length > 0) {
          wrong.push(
            `${method.toUpperCase()} ${path}: declares [${names.join(', ')}]` +
              (optional.length ? ` (not required: ${optional.map((p) => p.name).join(', ')})` : ''),
          );
        }
      }
    }
    expect(wrong, 'fix these path parameters in pipeshub-openapi.yaml').to.deep.equal([]);
  });
});
