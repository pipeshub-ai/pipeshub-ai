/**
 * SEC-AZ-07 (C6.23), SEC-LG-02 (C6.38, the ACL half): an IDOR sweep over
 * every harness fetch route (a run and what hangs off it: its events,
 * timeline, activity, steps, command logs, follow-ups; the session's
 * workspace snapshots, files and archive; a schedule and its firings). The
 * list is every GET of the mounted routers that names a tenant's object, so a
 * new route is swept without a change here (the QA-26 inventory test holds the
 * table equal to the routers).
 *
 * QA-26 accepts 403 or 404. Here a fetch must not even say the object exists:
 * another org's member and admin, a colleague, and an org admin who is not in
 * the session (the transcript ACL) all get exactly 404, the answer an id that
 * never existed gets; and ids guessed from a real one (the next or previous
 * ObjectId, a neighbouring character) get 404 from everyone, the owner too.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { ChatCompareFeature } from '../../../src/modules/enterprise_search/utils/chat-compare-feature';
import { connectHarnessMongo, disconnectHarnessMongo } from './support/harness-mongo';
import {
  concretePath,
  CROSS_TENANT_ROUTES,
  CrossTenantRow,
  mountedRoutes,
  paramValue,
  routeKey,
  rowQuery,
  tenantParams,
} from './support/cross-tenant-routes';
import { CrossTenantRig, Reply, SeededTenant, seedTenant, startCrossTenantRig, tenant } from './support/cross-tenant-rig';

/** The guards under which a person (not a token or a service) fetches a tenant's object. */
const PERSON_GUARDS = new Set<CrossTenantRow['guard']>(['owner', 'org', 'actor-or-admin', 'admin']);

interface FetchRoute {
  route: string;
  pattern: string;
  row: CrossTenantRow;
}

/** Every mounted GET that names a tenant's object and that a person reaches. */
function fetchRoutes(rig: CrossTenantRig): FetchRoute[] {
  return mountedRoutes(rig.mountedSurfaces())
    .filter((route) => route.method === 'GET' && tenantParams(route.path).length > 0)
    .map((route) => ({ route: routeKey(route), pattern: route.path, row: CROSS_TENANT_ROUTES[routeKey(route)]! }))
    .filter(({ row }) => row !== undefined && PERSON_GUARDS.has(row.guard));
}

/** Ids one step from a real one: the next and previous value of its last hex digit, and a changed last character. */
function neighbours(id: string): string[] {
  const last = id.at(-1)!;
  const out = new Set<string>();
  if (/[0-9a-f]/i.test(last)) {
    const n = parseInt(last, 16);
    out.add(`${id.slice(0, -1)}${((n + 1) % 16).toString(16)}`);
    out.add(`${id.slice(0, -1)}${((n + 15) % 16).toString(16)}`);
  } else {
    out.add(`${id.slice(0, -1)}${last === 'Z' ? 'Y' : 'Z'}`);
  }
  out.delete(id);
  return [...out];
}

interface Finding {
  route: string;
  as: string;
  path: string;
  status: number;
  body: string;
}

describe('SEC-AZ-07 IDOR sweep: every run, snapshot and schedule fetch is not found to anyone else (C6.23)', function () {
  this.timeout(900_000);
  let rig: CrossTenantRig;
  let A: SeededTenant;
  let B: SeededTenant;

  before(connectHarnessMongo);
  before(async () => {
    // Labs-gated routes are swept with their flag on: off, they answer everyone 403 before the ACL is reached.
    sinon.stub(ChatCompareFeature, 'isEnabled').resolves(true);
    const a = tenant('A');
    const b = tenant('B');
    rig = await startCrossTenantRig([a, b]);
    A = await seedTenant(rig, a);
    B = await seedTenant(rig, b);
  });

  after(async () => {
    sinon.restore();
    await rig?.close();
    await disconnectHarnessMongo();
  });

  const fetchAs = async (row: CrossTenantRow, path: string, userId: string): Promise<Reply> =>
    row.stream === true ? rig.open(path, rig.bearer(userId)) : rig.call('GET', path, rig.bearer(userId));

  it('AZ7-1: the sweep covers the run, workspace-snapshot and schedule fetches', () => {
    const routes = fetchRoutes(rig).map(({ route }) => route);
    for (const expected of [
      'GET /api/v1/conversations/:sessionId/runs/:runId',
      'GET /api/v1/agents/:agentKey/conversations/:sessionId/runs/:runId/timeline',
      'GET /api/v1/agents/:agentKey/conversations/:sessionId/runs/:runId/steps/:toolCallId',
      'GET /api/v1/agents/:agentKey/conversations/:sessionId/runs/:runId/commands/:commandId/log',
      'GET /api/v1/conversations/:sessionId/workspace/versions',
      'GET /api/v1/conversations/:sessionId/workspace/file',
      'GET /api/v1/conversations/:sessionId/workspace/archive',
      'GET /api/v1/conversations/:conversationId',
    ]) {
      expect(routes, expected).to.include(expected);
    }
  });

  it('AZ7-2: another org, a colleague, and an org admin outside the session get exactly 404 on every fetch', async () => {
    const findings: Finding[] = [];
    let count = 0;
    for (const { route, pattern, row } of fetchRoutes(rig)) {
      const path = `${concretePath(pattern, () => A.ids)}${rowQuery(row, A.ids)}`;
      const callers: Array<[string, string]> = [
        ['B member', B.owner],
        ['B admin', B.admin],
      ];
      if (row.guard !== 'org') callers.push(['A colleague', A.peer]);
      if (row.guard === 'owner' && row.adminMay !== true) callers.push(['A admin outside the session', A.admin]);
      for (const [as, userId] of callers) {
        const reply = await fetchAs(row, path, userId);
        count += 1;
        if (reply.status !== 404 || reply.contentType.includes('text/event-stream') || reply.text.includes(A.privateText)) {
          findings.push({ route, as, path, status: reply.status, body: reply.text.slice(0, 200) });
        }
      }
    }
    expect(count).to.be.greaterThan(0);
    expect(findings, `${String(findings.length)} of ${String(count)} fetches`).to.deep.equal([]);
  });

  it('AZ7-3: ids guessed from a real one are not found, to its owner and to another org alike', async () => {
    const findings: Finding[] = [];
    let count = 0;
    for (const { route, pattern, row } of fetchRoutes(rig)) {
      for (const name of tenantParams(pattern).filter((param) => param !== 'agentKey')) {
        const real = paramValue(name, pattern, A.ids);
        for (const guess of neighbours(real)) {
          const path = `${pattern.replace(/:([A-Za-z]+)/g, (_m, param: string) =>
            encodeURIComponent(param === name ? guess : paramValue(param, pattern, A.ids)),
          )}${rowQuery(row, A.ids)}`;
          const owner = row.guard === 'admin' ? A.admin : A.owner;
          for (const [as, userId] of [
            ['A (the rightful caller)', owner],
            ['B member', B.owner],
          ] as const) {
            const reply = await fetchAs(row, path, userId);
            count += 1;
            if (reply.status !== 404) findings.push({ route, as: `${as}, :${name} guessed`, path, status: reply.status, body: reply.text.slice(0, 200) });
          }
        }
      }
    }
    expect(count).to.be.greaterThan(0);
    expect(findings, `${String(findings.length)} of ${String(count)} guessed fetches`).to.deep.equal([]);
  });

  it('AZ7-4: the rightful caller still reads every live fetch (the positive control)', async () => {
    const findings: Finding[] = [];
    for (const { route, pattern, row } of fetchRoutes(rig)) {
      if (row.live !== true) continue;
      const path = `${concretePath(pattern, () => A.ids)}${rowQuery(row, A.ids)}`;
      const reply = await fetchAs(row, path, row.guard === 'admin' ? A.admin : A.owner);
      if (reply.status < 200 || reply.status >= 300) findings.push({ route, as: 'A', path, status: reply.status, body: reply.text.slice(0, 200) });
    }
    expect(findings).to.deep.equal([]);
  });
});

