/**
 * QA26-1: the cross-tenant table holds every tenant-facing harness route the
 * production container builds, and nothing else; and `app.ts` mounts exactly
 * those routers at those prefixes. A new route or router cannot skip the
 * QA-26 sweep (`qa26-cross-tenant.mongo.itest.ts`).
 */
import {
  ENGINE_ACCOUNTS_PREFIX,
} from '../../../src/modules/agent-harness/transport/engine-signin-routes';
import 'reflect-metadata';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import { EnterpriseSearchAgentContainer } from '../../../src/modules/enterprise_search/container/es.container';
import { KeyValueStoreService } from '../../../src/libs/services/keyValueStore.service';
import * as composition from '../../../src/modules/agent-harness/composition';
import { AGENT_HARNESS_ADMIN_PREFIX } from '../../../src/modules/agent-harness/transport/admin-routes';
import { AGENT_HARNESS_INTERNAL_PREFIX } from '../../../src/modules/agent-harness/transport/internal-routes';
import { MEMORIES_PREFIX } from '../../../src/modules/agent-harness/transport/memory-routes';
import {
  MODEL_ACCOUNTS_PREFIX,
} from '../../../src/modules/agent-harness/transport/model-account-routes';
import { PUBLICATIONS_PREFIX } from '../../../src/modules/agent-harness/transport/publication-routes';
import { SCHEDULES_PREFIX, TRIGGERS_PREFIX } from '../../../src/modules/agent-harness/transport/schedule-routes';
import { CAPABILITY_SYNC_PATH } from '../../../src/modules/agent-harness/transport/capability-sync-routes';
import { AGENT_HARNESS_RUNS_PREFIX } from '../../../src/modules/agent-harness/transport/run-list-routes';
import { INBOX_PREFIX } from '../../../src/modules/agent-harness/transport/inbox-routes';
import {
  CROSS_TENANT_ROUTES,
  OWN_NAMESPACE_PARAMS,
  HARNESS_SURFACES,
  mountedRoutes,
  pathParams,
  routeKey,
  tenantParams,
  tenantQueryParams,
} from './support/cross-tenant-routes';

const PREFIX_CONSTANTS: Record<string, string> = {
  AGENT_HARNESS_ADMIN_PREFIX,
  AGENT_HARNESS_INTERNAL_PREFIX,
  MEMORIES_PREFIX,
  MODEL_ACCOUNTS_PREFIX,
  ENGINE_ACCOUNTS_PREFIX,
  PUBLICATIONS_PREFIX,
  SCHEDULES_PREFIX,
  TRIGGERS_PREFIX,
  CAPABILITY_SYNC_PATH,
  AGENT_HARNESS_RUNS_PREFIX,
  INBOX_PREFIX,
};

/** The internal routes run on their own listener (`createAgentHarnessInternalApp`, made by `boundedServer`), behind the same prefix. */
const MOUNTED_AS: Record<string, string> = { createAgentHarnessInternalApp: 'createAgentHarnessInternalRouter' };

async function productionContainer(): Promise<Container> {
  sinon.stub(process, 'env').value({ ...process.env, SECRET_KEY: '95123dd46344ffd846bf547f69888b7d06fdb781b8e9f758a897ad2a210fa063' });
  sinon.stub(KeyValueStoreService, 'getInstance').returns({
    connect: sinon.stub().resolves(),
    disconnect: sinon.stub().resolves(),
    isConnected: () => true,
    get: sinon.stub().resolves(null),
    usesEtcd: () => false,
  } as never);
  sinon.stub(composition, 'startSessionErasureServices').resolves();
  return EnterpriseSearchAgentContainer.initialize(
    { redisConfig: { host: 'redis', port: 6379 } } as never,
    {
      jwtSecret: 'jwt',
      scopedJwtSecret: 'scoped',
      aiBackend: 'http://query:8000',
      storageBackend: 'http://storage:3000',
      storage: { storageType: 'local' },
    } as never,
  );
}

/** `[mount argument, factory]` of every `this.app.use(…, factory(this.<container>))` in app.ts. */
function appMounts(): Array<{ mount: string; factory: string }> {
  const source = readFileSync(join(__dirname, '../../../src/app.ts'), 'utf8');
  const mounts: Array<{ mount: string; factory: string }> = [];
  for (const match of source.matchAll(/this\.app\.use\(\s*([^,()]+?),\s*(?:[\w.]+\(\s*)?(?:http\.createServer\(\s*)?(create\w+)\(this\.\w+\)/g)) {
    mounts.push({ mount: match[1]!.trim(), factory: match[2]! });
  }
  for (const match of source.matchAll(/(?:http\.createServer|boundedServer)\(\s*(create\w+)\(this\.\w+\)/g)) {
    mounts.push({ mount: 'AGENT_HARNESS_INTERNAL_PREFIX', factory: match[1]! });
  }
  return mounts;
}

const mountPrefix = (mount: string): string =>
  mount.startsWith("'") ? mount.slice(1, -1) : (PREFIX_CONSTANTS[mount] ?? `<unresolved ${mount}>`);

describe('QA-26 cross-tenant route inventory (QA26-1)', () => {
  let container: Container;

  before(async () => {
    container = await productionContainer();
  });

  after(() => {
    sinon.restore();
    (EnterpriseSearchAgentContainer as unknown as { instance: Container | undefined }).instance = undefined;
  });

  it('has a row for every tenant-facing route of the production container, and no row without a route', () => {
    const mounted = mountedRoutes(container).map(routeKey);
    expect(new Set(mounted).size, 'one route per method and path').to.equal(mounted.length);
    const rows = Object.keys(CROSS_TENANT_ROUTES);
    expect(mounted.filter((route) => !rows.includes(route)), 'routes with no cross-tenant row').to.deep.equal([]);
    expect(rows.filter((row) => !mounted.includes(row)), 'rows with no route').to.deep.equal([]);
  });

  it('mounts every harness router in app.ts at the prefix the table assumes', () => {
    const mounts = appMounts();
    for (const surface of HARNESS_SURFACES) {
      const at = mounts
        .filter(({ factory }) => (MOUNTED_AS[factory] ?? factory) === surface.name)
        .map(({ mount }) => mountPrefix(mount));
      expect(at, surface.name).to.deep.equal([surface.prefix]);
    }
    const harnessFactories = readFileSync(join(__dirname, '../../../src/app.ts'), 'utf8')
      .split(/;\s*\n/)
      .filter((statement) => statement.includes("from './modules/agent-harness/"))
      .flatMap((statement) => [...statement.matchAll(/\b(create\w+(?:Router|App))\b/g)].map((match) => match[1]!));
    const surfaces = HARNESS_SURFACES.map((surface) => surface.name);
    expect(
      harnessFactories.filter((factory) => !surfaces.includes(MOUNTED_AS[factory] ?? factory)),
      'harness routers app.ts mounts that the sweep does not cover',
    ).to.deep.equal([]);
  });

  it('names a tenant object on every row whose guard decides by one, and none on the rows that do not', () => {
    const naming = new Set(['owner', 'org', 'actor-or-admin', 'admin', 'run-token', 'worker', 'trigger']);
    // A route with no ids in its path (G-13's batch decision) names its objects in the body the itest replays.
    const sentinel = new Proxy({}, { get: (_t, name) => `qa26-sentinel-${String(name)}` }) as never;
    const bodyNames = (row: (typeof CROSS_TENANT_ROUTES)[string]): string[] =>
      row.body !== undefined && JSON.stringify(row.body(sentinel, { orgId: '', userId: '' })).includes('qa26-sentinel-') ? ['body'] : [];
    for (const [route, row] of Object.entries(CROSS_TENANT_ROUTES)) {
      const path = route.split(' ')[1]!;
      if (naming.has(row.guard)) {
        expect([...tenantParams(path), ...tenantQueryParams(row), ...bodyNames(row)], `${route} (${row.guard})`).to.not.deep.equal([]);
      } else {
        // The worker credential serves every org, so a deployment row may name the org and still no object of it.
        const named = [
          ...pathParams(path).filter(
            (name) => !OWN_NAMESPACE_PARAMS.has(name) && !(row.guard === 'deployment' && name === 'orgId'),
          ),
          ...tenantQueryParams(row),
        ];
        expect(named, `${route} (${row.guard})`).to.satisfy(
          (names: string[]) => row.guard === 'relay' || names.length === 0,
        );
      }
    }
  });
});
