/**
 * QA-26 QA26-2..QA26-8 (EM-32): org B and a second user of org A replay org
 * A's ids on every tenant-facing harness route, as `CROSS_TENANT_ROUTES`
 * lists them, through the composition on a real MongoDB replica set
 * (`support/cross-tenant-rig.ts`). Every replay answers 403 or 404 (401 on
 * the token routes), names nothing of org A it was not handed, and org A's
 * stores are the same afterwards; org A's own callers still read every
 * object (the positive control).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose from 'mongoose';
import { connectHarnessMongo, disconnectHarnessMongo } from './support/harness-mongo';
import { auditLinksIdle } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import {
  concretePath,
  CROSS_TENANT_ROUTES,
  CrossTenantRow,
  mountedRoutes,
  routeKey,
  rowQuery,
  tenantParams,
  TenantIds,
} from './support/cross-tenant-routes';
import { CrossTenantRig, Reply, SeededTenant, seedTenant, startCrossTenantRig, tenant } from './support/cross-tenant-rig';

const REFUSED = [403, 404];
const TOKEN_REFUSED = [401, 403, 404];

interface Replay {
  route: string;
  row: CrossTenantRow;
  as: string;
  authorization: string | null;
  path: string;
  body?: unknown;
  headers?: Record<string, string>;
  /** Statuses that are a refusal on this row. */
  refused: number[];
  /** Strings the answer must not carry: what the caller may not see and did not send. */
  hidden: string[];
}

interface Finding {
  route: string;
  as: string;
  path: string;
  status: number;
  problem: string;
  body: string;
}

/** A refusal the route's published contract answers with something other than 403 or 404. */
function refusedByContract(row: CrossTenantRow, reply: Reply): boolean {
  if (reply.status !== 200) return false;
  if (row.refusal === 'idempotent-200') return reply.text === JSON.stringify({ message: 'Conversation deleted successfully', conversation: null });
  if (row.refusal === 'in-band-sse') {
    // Nothing but the stream's opening and its error: no regeneration started.
    const events = [...reply.text.matchAll(/^data: (.*)$/gm)].map((m) => JSON.parse(m[1]!) as { type: string; name?: string });
    return (
      events.some((e) => e.type === 'RUN_ERROR') &&
      events.every((e) => e.type === 'RUN_ERROR' || (e.type === 'CUSTOM' && e.name === 'conversation_created'))
    );
  }
  return false;
}

/** Every assignment of A or B to the row's tenant params except all-B (that one is B's own). */
function mixtures(names: string[]): Array<Record<string, 'A' | 'B'>> {
  const combos: Array<Record<string, 'A' | 'B'>> = [];
  for (let mask = 1; mask < 1 << names.length; mask += 1) {
    combos.push(Object.fromEntries(names.map((name, i) => [name, mask & (1 << i) ? 'A' : 'B'])));
  }
  return combos;
}

describe('QA-26 cross-org and cross-owner replay over every harness route (EM-32)', function () {
  this.timeout(900_000);
  let rig: CrossTenantRig;
  let A: SeededTenant;
  let B: SeededTenant;

  before(connectHarnessMongo);
  before(async () => {
    const a = tenant('A');
    const b = tenant('B');
    rig = await startCrossTenantRig([a, b]);
    A = await seedTenant(rig, a);
    B = await seedTenant(rig, b);
  });

  after(async () => {
    await rig?.close();
    await disconnectHarnessMongo();
  });

  /** Everything org A holds in Mongo and in configuration, by document. */
  async function orgSnapshot(orgId: string): Promise<Record<string, string>> {
    const db = mongoose.connection.db!;
    const snapshot: Record<string, string> = {};
    for (const { name } of await db.listCollections({}, { nameOnly: true }).toArray()) {
      for (const doc of await db.collection(name).find({}).toArray()) {
        const text = JSON.stringify(doc);
        if (text.includes(orgId)) snapshot[`${name}/${String(doc._id)}`] = text;
      }
    }
    for (const key of await rig.kv.raw.getAllKeys()) {
      const value = String(await rig.kv.raw.getKey(key));
      if (key.includes(orgId) || value.includes(orgId)) snapshot[`kv:${key}`] = value;
    }
    return snapshot;
  }

  const sent = (replay: Omit<Replay, 'row'>): string => `${replay.path} ${JSON.stringify(replay.body ?? '')} ${JSON.stringify(replay.headers ?? {})}`;

  async function send(replay: Replay, row: CrossTenantRow): Promise<Reply> {
    const [method] = replay.route.split(' ');
    if (row.stream === true && replay.authorization !== null) return rig.open(replay.path, replay.authorization);
    return rig.call(method!, replay.path, replay.authorization, replay.body, replay.headers);
  }

  function check(replay: Replay, reply: Reply, findings: Finding[]): void {
    const finding = (problem: string) =>
      findings.push({ route: replay.route, as: replay.as, path: replay.path, status: reply.status, problem, body: reply.text.slice(0, 300) });
    const contractual = replay.refused.length > 0 && refusedByContract(replay.row, reply);
    if (replay.refused.length > 0 && !replay.refused.includes(reply.status) && !contractual) {
      finding(`answered ${String(reply.status)}, not ${replay.refused.join('/')}`);
    }
    if (reply.contentType.includes('text/event-stream') && replay.refused.length > 0 && !contractual) finding('opened a stream');
    const echoed = sent(replay);
    const leaked = replay.hidden.filter((value) => reply.text.includes(value) && !echoed.includes(value));
    if (leaked.length > 0) finding(`named ${leaked.join(', ')}`);
  }

  /** The replays a row gets: who calls with which ids, and what counts as refused. */
  function replays(route: string, row: CrossTenantRow): Array<Omit<Replay, 'row'>> {
    const [method, pattern] = route.split(' ') as [string, string];
    const params = tenantParams(pattern);
    const all = (t: SeededTenant) => concretePath(pattern, () => t.ids);
    const query = (ids: TenantIds) => rowQuery(row, ids);
    const user = (userId: string, as: string, path: string, target: TenantIds, refused: number[], hidden: string[]): Omit<Replay, 'row'> => ({
      route,
      as,
      authorization: rig.bearer(userId),
      path: `${path}${query(target)}`,
      ...(row.body !== undefined ? { body: row.body(target, { orgId: '', userId }) } : {}),
      ...(row.headers !== undefined ? { headers: row.headers(target) } : {}),
      refused,
      hidden,
    });
    const orgHidden = A.canaries;
    const peerHidden = A.canaries.filter((value) => value !== A.agentKey);
    switch (row.guard) {
      case 'owner':
      case 'actor-or-admin':
      case 'admin': {
        const out: Array<Omit<Replay, 'row'>> = [
          user(B.owner, 'B member', all(A), A.ids, REFUSED, orgHidden),
          user(B.admin, 'B admin', all(A), A.ids, REFUSED, orgHidden),
          user(A.peer, 'A peer', all(A), A.ids, REFUSED, peerHidden),
        ];
        if (row.guard === 'owner' && row.adminMay !== true) out.push(user(A.admin, 'A admin', all(A), A.ids, REFUSED, peerHidden));
        if (row.guard === 'admin') out.push(user(A.owner, 'A owner (member)', all(A), A.ids, REFUSED, []));
        for (const mix of mixtures(params).filter((m) => Object.values(m).includes('B'))) {
          const path = concretePath(pattern, (name) => (mix[name] === 'A' ? A.ids : B.ids));
          // An id both orgs share (the kernel names interactions per run) leaves B's own path.
          if (path === all(B)) continue;
          out.push(user(B.owner, `B member, mixed ${JSON.stringify(mix)}`, path, B.ids, REFUSED, orgHidden));
        }
        return out;
      }
      case 'admin-reads-own-org':
        // B's admin may read B's records with A's ids in the filter: an answer, but nothing of A in it.
        return [
          user(B.admin, "B admin, A's ids in the query", all(A), A.ids, [], orgHidden),
          user(B.owner, 'B member', all(A), A.ids, REFUSED, orgHidden),
          user(A.owner, 'A owner (member)', all(A), A.ids, REFUSED, []),
          user(A.peer, 'A peer (member)', all(A), A.ids, REFUSED, peerHidden),
        ];
      case 'admin-own-org':
        // The path is B's own: B's admin acts on B's org (any answer, nothing of A in it); no member gets through.
        return [
          user(B.admin, "B admin, on B's own", all(B), B.ids, [], orgHidden),
          user(B.owner, 'B member', all(B), B.ids, REFUSED, orgHidden),
          user(A.owner, 'A owner (member)', all(A), A.ids, REFUSED, []),
          user(A.peer, 'A peer (member)', all(A), A.ids, REFUSED, peerHidden),
        ];
      case 'org':
        return [
          user(B.owner, 'B member', all(A), A.ids, REFUSED, orgHidden),
          user(B.admin, 'B admin', all(A), A.ids, REFUSED, orgHidden),
        ];
      case 'caller':
        return method !== 'GET'
          ? []
          : [
              user(B.owner, 'B member', all(B), B.ids, [], orgHidden),
              user(B.admin, 'B admin', all(B), B.ids, [], orgHidden),
              user(A.peer, 'A peer', all(A), A.ids, [], [A.privateText, A.ids.memoryId, A.ids.agentRunId, A.ids.chatRunId]),
            ];
      case 'relay':
        return [user(B.owner, 'B member', all(A), A.ids, [], orgHidden)];
      case 'run-token': {
        const token = (authorization: string, as: string): Omit<Replay, 'row'> => ({
          route,
          as,
          authorization,
          path: `${all(A)}${query(A.ids)}`,
          ...(row.body !== undefined ? { body: row.body(A.ids, { orgId: B.orgId, userId: B.owner }) } : {}),
          refused: TOKEN_REFUSED,
          hidden: orgHidden,
        });
        return [
          token(B.attemptToken, "B run's attempt token"),
          token(B.gatewayToken, "B run's gateway token"),
          token(rig.bearer(A.owner), "A owner's user bearer"),
        ];
      }
      case 'worker': {
        const body = row.body?.(A.ids, { orgId: B.orgId, userId: B.owner }) as Record<string, unknown> | undefined;
        return [
          {
            route,
            as: `${row.role ?? 'worker'} credential naming org B`,
            authorization: row.role === 'egress-proxy' ? rig.egressCredential : rig.credential,
            path: `${all(A)}${query(B.ids)}`,
            ...(body !== undefined ? { body: { ...body, orgId: B.orgId } } : {}),
            refused: REFUSED,
            hidden: orgHidden,
          },
          { route, as: "B member's user bearer", authorization: rig.bearer(B.owner), path: all(A), ...(body ? { body } : {}), refused: TOKEN_REFUSED, hidden: orgHidden },
        ];
      }
      case 'deployment':
        return [
          {
            route,
            as: "B member's user bearer",
            authorization: rig.bearer(B.owner),
            path: all(A),
            ...(route.startsWith('GET ') ? {} : { body: {} }),
            refused: TOKEN_REFUSED,
            hidden: orgHidden,
          },
        ];
      case 'service':
        return [{ route, as: "B member's user bearer", authorization: rig.bearer(B.owner), path: all(A), body: {}, refused: TOKEN_REFUSED, hidden: orgHidden }];
      case 'link':
        return [
          { route, as: 'a guessed link', authorization: null, path: `${all(A)}${query(A.ids)}`, refused: [404], hidden: orgHidden },
          {
            route,
            as: "B admin's user bearer and a guessed link",
            authorization: rig.bearer(B.admin),
            path: `${all(A)}${query(A.ids)}`,
            refused: [404],
            hidden: orgHidden,
          },
        ];
      case 'trigger':
        return [
          { route, as: 'a guessed secret', authorization: null, path: all(A), headers: row.headers!(A.ids), refused: [401, 404], hidden: orgHidden },
          {
            route,
            as: "org B's schedule secret",
            authorization: null,
            path: all(A),
            headers: { 'x-pipeshub-trigger-token': B.webhookToken },
            refused: [401, 404],
            hidden: orgHidden,
          },
        ];
    }
  }

  async function liveFindings(): Promise<Finding[]> {
    const findings: Finding[] = [];
    for (const [route, row] of Object.entries(CROSS_TENANT_ROUTES)) {
      if (row.live !== true) continue;
      const [, pattern] = route.split(' ') as [string, string];
      const caller = row.guard === 'admin' || row.guard === 'admin-reads-own-org' || row.guard === 'admin-own-org' ? A.admin : A.owner;
      const path = `${concretePath(pattern, () => A.ids)}${rowQuery(row, A.ids)}`;
      const reply = row.stream === true ? await rig.open(path, rig.bearer(caller)) : await rig.call('GET', path, rig.bearer(caller));
      if (reply.status < 200 || reply.status >= 300) {
        findings.push({ route, as: 'A rightful caller', path, status: reply.status, problem: 'the rightful caller is refused', body: reply.text.slice(0, 300) });
      }
    }
    return findings;
  }

  it('QA26-6: org A reaches every object it owns with the ids the sweep replays', async () => {
    expect(await liveFindings()).to.deep.equal([]);
  });

  it('QA26-2..QA26-5, QA26-7, QA26-8: every replay is refused, names nothing of org A, and changes nothing of org A', async () => {
    const mounted = mountedRoutes(rig.mountedSurfaces()).map(routeKey).sort();
    expect(mounted, 'the rig mounts exactly the table').to.deep.equal(Object.keys(CROSS_TENANT_ROUTES).sort());

    // The seeding's audit links wait for the org's next commits (PERF-E3): they are the seeding's, not the sweep's.
    await auditLinksIdle();
    const before = await orgSnapshot(A.orgId);
    const findings: Finding[] = [];
    let count = 0;
    for (const [route, row] of Object.entries(CROSS_TENANT_ROUTES)) {
      for (const replay of replays(route, row).map((r) => ({ ...r, row }))) {
        check(replay, await send(replay, row), findings);
        count += 1;
      }
    }
    const relayed = rig.publicationCalls.filter((authorization) => !authorization.includes(B.owner));
    expect(relayed, "the query service only ever saw the caller's own credential").to.deep.equal([]);
    expect(findings, `${String(findings.length)} of ${String(count)} replays`).to.deep.equal([]);
    const after = await orgSnapshot(A.orgId);
    const changed = Object.keys({ ...before, ...after }).filter((key) => before[key] !== after[key]);
    expect(changed, "org A's documents the sweep changed").to.deep.equal([]);
    expect(await liveFindings(), 'org A still reaches everything').to.deep.equal([]);
  });
});
