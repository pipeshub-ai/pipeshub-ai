/**
 * SEC-C-11 (ADR-GW-02): every `run-token` route of the internal router holds a run's token to one rule
 * (`runTokenRefusal`): a superseded attempt, an attempt whose lease lapsed, or the attempt that ended the run is
 * refused on each, and what is journaled names the token's attempt, never the body's. The routes are read from the
 * router, so a new run-token route fails here until it declares how it is checked.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import { Container } from 'inversify';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { InMemoryBudgetLedger } from '../../../src/modules/agent-harness/adapters/in-memory-budget-ledger';
import { InMemoryConfigDocumentStore as ConfigDocs } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { BudgetLedgerService } from '../../../src/modules/agent-harness/application/budget-ledger-service';
import { GatewayRefusalJournal } from '../../../src/modules/agent-harness/application/gateway-refusal-journal';
import { GatewayTokenAuthority } from '../../../src/modules/agent-harness/application/gateway-token-authority';
import { GatewayTokenService } from '../../../src/modules/agent-harness/application/gateway-token-service';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_BUDGET_LEDGER,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_GATEWAY_TOKENS,
  createHarnessAuthority,
} from '../../../src/modules/agent-harness/composition';
import { AttemptKeyring, PublishedAttemptKeys, WorkerCredentialSet } from '../../../src/modules/agent-harness/domain/authority';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { runTokenRefusal } from '../../../src/modules/agent-harness/domain/run-token-standing';
import { validatingPreparer } from '../../../src/modules/agent-harness/ports/journal-store';
import { routeGuardOf } from '../../../src/modules/agent-harness/transport/internal-auth';
import { createAgentHarnessInternalRouter } from '../../../src/modules/agent-harness/transport/internal-routes';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { contractClock, createContractHarness } from './support/execution-store-contract';
import { CURRENT_WORKER_CAPABILITIES } from './support/worker-capabilities';
import { HARNESS_ENABLED } from './support/rollout';
import { proofFor, testBindingKeys } from './support/binding-proof';

// Short enough that a lapsed lease is taken over well inside the contract harness's run deadline.
const LEASE_MS = 2_000;

/** A sample request per run-token route; a route missing here fails the enumeration test. */
const SAMPLES: Record<string, Record<string, unknown> | undefined> = {
  'GET /runs/:runId/model-grant': undefined,
  'GET /runs/:runId/budget': undefined,
  'POST /runs/:runId/budget/reservations': {
    key: 'k-stale',
    requested: { usdMicros: 1_000, tokens: 10 },
    minimum: { usdMicros: 1_000, tokens: 10 },
    ttlS: 60,
  },
  'POST /runs/:runId/refusals': { gateway: 'model', reason: 'unsupported_parameter', status: 400, param: 'tools.type' },
  'POST /runs/:runId/egress': { host: 'api.example', via: 'http' },
  'POST /runs/:runId/egress-volume': {
    kind: 'run_threshold', direction: 'up', bytes: 2_000_000, thresholdBytes: 1_000_000, alias: 'tunnel',
  },
  'POST /runs/:runId/model-fallback': {
    from: { provider: 'openai', model: 'a' }, to: { provider: 'openai', model: 'b' }, reason: 'timeout',
  },
  'POST /runs/:runId/secret-hidden': { gateway: 'model', kind: 'pipeshub_pat' },
};

/**
 * Settling a reservation the attempt made while it held the lease: the spend happened, so a later claim must not
 * lose it (`settle` in ADR-GW-02). These alone accept a superseded attempt's token, and only for a reservation its
 * own attempt made (SEC-C-20, D5).
 */
const SETTLES = [
  'POST /runs/:runId/budget/reservations/:key/commit',
  'POST /runs/:runId/budget/reservations/:key/reconcile',
  'POST /runs/:runId/budget/reservations/:key/release',
];

interface RouteLayer {
  route?: { path: string; methods: Record<string, boolean>; stack: Array<{ handle: unknown }> };
}

async function world() {
  const clock = contractClock();
  const store = new InMemoryExecutionStore(clock);
  const h = createContractHarness(
    {
      executionStore: store, journal: store.journal, payloads: new InMemoryPayloadStore(), admissionStore: store,
      dispatches: async () => [], deletePayload: async () => undefined,
    },
    clock,
  );
  h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
  const stores = {
    workerCredential: new ConfigDocs<WorkerCredentialSet>(),
    attemptSigningKeys: new ConfigDocs<AttemptKeyring>(),
    attemptPublicKeys: new ConfigDocs<PublishedAttemptKeys>(),
    roleCredentials: {
      scrape: new ConfigDocs<WorkerCredentialSet>(),
      'egress-proxy': new ConfigDocs<WorkerCredentialSet>(),
      'sandbox-broker': new ConfigDocs<WorkerCredentialSet>(),
    },
  };
  const authority = createHarnessAuthority(stores, h.service, clock);
  await authority.provision(null);
  const credential = (await stores.workerCredential.read()).value!.current;
  // SEC-C-10 (ADR-CRED-01): only the egress proxy's credential mints a gateway token.
  const proxyCredential = (await authority.credentialFor('egress-proxy'))!;
  const gateway = new GatewayTokenAuthority({ signingKeys: new ConfigDocs(), publishedKeys: new ConfigDocs(), clock });
  const container = new Container();
  container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue(h.service);
  container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue(authority);
  container.bind(AGENT_HARNESS_GATEWAY_TOKENS).toConstantValue(new GatewayTokenService({
    tokens: gateway, runs: h.service, clock, bindingKeys: testBindingKeys,
    refusals: new GatewayRefusalJournal(
      store.journal, { ...validatingPreparer, resolve: async (events) => events }, async (work) => work(undefined),
    ) as GatewayRefusalJournal<unknown>,
  }));
  container.bind(AGENT_HARNESS_BUDGET_LEDGER).toConstantValue(new BudgetLedgerService({
    ledger: new InMemoryBudgetLedger(() => clock.now()), runs: h.service, attempts: authority, gateways: gateway, clock,
  }));
  const router = createAgentHarnessInternalRouter(container);
  const app = express();
  app.use(express.json());
  app.use('/internal', router);
  app.use(ErrorMiddleware.handleError());
  const server = await new Promise<Server>((resolve) => {
    const listening = app.listen(0, '127.0.0.1', () => resolve(listening));
  });
  const base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}/internal`;

  const runTokenRoutes = (): string[] =>
    (router.stack as RouteLayer[])
      .filter((layer) => layer.route !== undefined && routeGuardOf(layer.route.stack[0]?.handle) === 'run-token')
      .flatMap((layer) => Object.keys(layer.route!.methods).map((m) => `${m.toUpperCase()} ${layer.route!.path}`));

  const claim = async (runId: string, orgId: string): Promise<HarnessRun> => {
    const response = await fetch(`${base}/runs/${runId}/claim`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${credential}` },
      body: JSON.stringify({ orgId, workerId: 'worker-a', leaseDurationMs: LEASE_MS, workerCapabilities: CURRENT_WORKER_CAPABILITIES }),
    });
    const body = (await response.json()) as { run: HarnessRun };
    expect(response.status, JSON.stringify(body)).to.equal(200);
    return body.run;
  };
  const admitted = async (): Promise<HarnessRun> => {
    const { run } = await h.admit();
    return claim(run.runId, run.orgId);
  };
  const tokenFor = async (run: HarnessRun): Promise<string> => {
    const response = await fetch(`${base}/runs/${run.runId}/gateway-token`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${proxyCredential}` },
      body: JSON.stringify({
        orgId: run.orgId, attemptId: run.attemptId, bindingProof: proofFor(run.orgId, run.runId, run.attemptId!),
      }),
    });
    expect(response.status).to.equal(200);
    return ((await response.json()) as { token: string }).token;
  };
  const call = async (route: string, runId: string, token: string, body = SAMPLES[route], key = 'k-stale') => {
    const [method, path] = route.split(' ') as [string, string];
    const response = await fetch(`${base}${path.replace(':runId', runId).replace(':key', key)}`, {
      method,
      headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
      ...(method === 'GET' ? {} : { body: JSON.stringify(body ?? {}) }),
    });
    return { status: response.status, text: await response.text() };
  };
  const gatewayEvents = async (run: HarnessRun) =>
    (await store.journal.readRun(run.orgId, run.runId, 0, 1_000)).filter((event) =>
      event.type.startsWith('gateway.') || event.type.startsWith('egress.') ||
      event.type.startsWith('model.'),
    );
  const end = async (run: HarnessRun) => {
    await h.service.commit({
      commandId: `end-${run.runId}`, orgId: run.orgId, runId: run.runId, workerId: 'worker-a',
      attemptId: run.attemptId!, fence: run.fence, events: [], legacyEvents: [],
      transition: { status: 'failed' }, outbox: [],
    });
    return (await h.service.get(run.orgId, run.runId)) as HarnessRun;
  };
  return {
    clock, base, runTokenRoutes, admitted, claim, tokenFor, call, gatewayEvents, end,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

type World = Awaited<ReturnType<typeof world>>;

describe('SEC-C-11: every run-token route holds the token to its attempt', function () {
  this.timeout(30_000);
  let w: World;
  beforeEach(async () => {
    w = await world();
  });
  afterEach(async () => {
    await w.close();
  });

  const reportsAndActs = (): string[] => w.runTokenRoutes().filter((route) => !SETTLES.includes(route));

  it('knows every run-token route the router mounts: a new one must say how it is checked', () => {
    const routes = w.runTokenRoutes();
    expect(routes.length, 'the enumeration found the gateway routes').to.be.greaterThan(8);
    expect([...routes].sort()).to.deep.equal([...Object.keys(SAMPLES), ...SETTLES].sort());
  });

  it('refuses a superseded attempt on every non-settling run-token route, and journals nothing', async () => {
    const first = await w.admitted();
    const stale = await w.tokenFor(first);
    w.clock.set(w.clock.now() + LEASE_MS + 1_000);
    const second = await w.claim(first.runId, first.orgId);
    expect(second.fence).to.be.greaterThan(first.fence);
    for (const route of reportsAndActs()) {
      const reply = await w.call(route, first.runId, stale);
      expect(reply.status, `${route}: ${reply.text}`).to.equal(409);
      expect(reply.text, route).to.contain('RUN_NOT_LEASED');
    }
    expect(await w.gatewayEvents(first)).to.deep.equal([]);
    // The live attempt's own token still reports, as itself.
    const live = await w.tokenFor(second);
    const reply = await w.call('POST /runs/:runId/refusals', second.runId, live);
    expect(reply.status, reply.text).to.equal(200);
    const [event] = await w.gatewayEvents(second);
    expect(event?.attemptId).to.equal(second.attemptId);
  });

  it('refuses a token whose lease lapsed (no new claim yet) on every non-settling route', async () => {
    const run = await w.admitted();
    const token = await w.tokenFor(run);
    w.clock.set(w.clock.now() + LEASE_MS + 1_000);
    for (const route of reportsAndActs()) {
      expect((await w.call(route, run.runId, token)).status, route).to.equal(409);
    }
  });

  it('a replay after the lease was taken over is refused: the token answered before, never again', async () => {
    const run = await w.admitted();
    const token = await w.tokenFor(run);
    const before = await w.call('POST /runs/:runId/refusals', run.runId, token);
    expect(before.status, before.text).to.equal(200);
    w.clock.set(w.clock.now() + LEASE_MS + 1_000);
    await w.claim(run.runId, run.orgId);
    const replay = await w.call('POST /runs/:runId/egress', run.runId, token);
    expect(replay.status, replay.text).to.equal(409);
  });

  it('refuses the attempt that ended the run on every non-settling route: nothing journals after the end', async () => {
    const run = await w.admitted();
    const token = await w.tokenFor(run);
    const ended = await w.end(run);
    expect(ended.attemptId).to.equal(null);
    for (const route of reportsAndActs()) {
      const reply = await w.call(route, run.runId, token);
      expect(reply.status, `${route} after the end: ${reply.text}`).to.equal(409);
    }
    expect(await w.gatewayEvents(run)).to.deep.equal([]);
  });

  it("names the attempt only from the token: an attempt id in a report's body is refused, nothing recorded", async () => {
    const run = await w.admitted();
    const token = await w.tokenFor(run);
    for (const attemptId of ['someone-else', run.attemptId!, ' ', '']) {
      const reply = await w.call('POST /runs/:runId/refusals', run.runId, token, {
        ...SAMPLES['POST /runs/:runId/refusals'], attemptId,
      });
      expect(reply.status, reply.text).to.equal(400);
    }
    for (const fence of [run.fence + 1, 0]) {
      const reply = await w.call('POST /runs/:runId/secret-hidden', run.runId, token, {
        ...SAMPLES['POST /runs/:runId/secret-hidden'], fence,
      });
      expect(reply.status, reply.text).to.equal(400);
    }
    expect(await w.gatewayEvents(run)).to.deep.equal([]);
  });

  it("refuses another run's token on every run-token route, settlements included", async () => {
    const mine = await w.admitted();
    const theirs = await w.admitted();
    const token = await w.tokenFor(theirs);
    for (const route of w.runTokenRoutes()) {
      const reply = await w.call(route, mine.runId, token, SAMPLES[route] ?? { actual: { usdMicros: 0, tokens: 0 } });
      expect(reply.status, `${route}: ${reply.text}`).to.equal(401);
    }
    expect(await w.gatewayEvents(mine)).to.deep.equal([]);
  });

  it('settles what a superseded attempt reserved while it held the lease: that spend happened', async () => {
    const run = await w.admitted();
    const stale = await w.tokenFor(run);
    const reserve = 'POST /runs/:runId/budget/reservations';
    expect((await w.call(reserve, run.runId, stale)).status).to.equal(200);
    w.clock.set(w.clock.now() + LEASE_MS + 1_000);
    await w.claim(run.runId, run.orgId);
    const committed = await w.call(SETTLES[0]!, run.runId, stale, { actual: { usdMicros: 500, tokens: 5 } });
    expect(committed.status, committed.text).to.equal(200);
    for (const route of SETTLES.slice(1)) {
      const reply = await w.call(route, run.runId, stale, route.endsWith('reconcile') ? { actual: { usdMicros: 400, tokens: 4 } } : {});
      expect(reply.text, `${route} is a settlement, not refused for the lease`).to.not.contain('RUN_NOT_LEASED');
    }
  });
});

describe("SEC-C-20: a reservation is settled only by the attempt that made it", function () {
  this.timeout(30_000);
  let w: World;
  beforeEach(async () => {
    w = await world();
  });
  afterEach(async () => {
    await w.close();
  });
  const reserve = 'POST /runs/:runId/budget/reservations';
  const bodyOf = (route: string) =>
    route.endsWith('release') ? {} : { actual: { usdMicros: 400, tokens: 4 } };
  const takenOver = async () => {
    const first = await w.admitted();
    const stale = await w.tokenFor(first);
    expect((await w.call(reserve, first.runId, stale)).status).to.equal(200);
    w.clock.set(w.clock.now() + LEASE_MS + 1_000);
    const second = await w.claim(first.runId, first.orgId);
    const live = await w.tokenFor(second);
    const fresh = await w.call(reserve, first.runId, live, { ...SAMPLES[reserve], key: 'k-new' });
    expect(fresh.status, fresh.text).to.equal(200);
    return { first, second, stale, live };
  };

  it("refuses a superseded attempt's settlement of the live attempt's reservation, on every settling route", async () => {
    const { first, stale, live } = await takenOver();
    for (const route of SETTLES) {
      for (const key of ['k-new', encodeURIComponent('k-new'), '%6B-new']) {
        const reply = await w.call(route, first.runId, stale, bodyOf(route), key);
        expect(reply.status, `${route} ${key}: ${reply.text}`).to.equal(409);
        expect(reply.text, route).to.contain('BUDGET_RESERVATION_MISSING');
      }
    }
    // The live attempt's reservation is untouched: it still commits it, once.
    const committed = await w.call(SETTLES[0]!, first.runId, live, bodyOf(SETTLES[0]!), 'k-new');
    expect(committed.status, committed.text).to.equal(200);
    expect(JSON.parse(committed.text).charged.usdMicros).to.equal(400);
  });

  it("refuses the live attempt's settlement of the superseded attempt's reservation; the superseded attempt settles its own", async () => {
    const { first, stale, live } = await takenOver();
    for (const route of SETTLES) {
      const reply = await w.call(route, first.runId, live, bodyOf(route));
      expect(reply.status, `${route}: ${reply.text}`).to.equal(409);
      expect(reply.text, route).to.contain('BUDGET_RESERVATION_MISSING');
    }
    const own = await w.call(SETTLES[0]!, first.runId, stale, bodyOf(SETTLES[0]!));
    expect(own.status, own.text).to.equal(200);
    const replay = await w.call(SETTLES[0]!, first.runId, live, bodyOf(SETTLES[0]!));
    expect(replay.status, 'a replay of the commit under the live token').to.equal(409);
  });

  it('a body naming another fence or attempt changes nothing: the binding is the token\'s', async () => {
    const { first, second, stale } = await takenOver();
    for (const route of SETTLES) {
      const reply = await w.call(route, first.runId, stale, { ...bodyOf(route), fence: second.fence, attemptId: second.attemptId }, 'k-new');
      expect(reply.status, `${route}: ${reply.text}`).to.be.oneOf([400, 409]);
      expect(reply.text, route).to.not.contain('"state":"committed"');
    }
    const still = await w.call(SETTLES[2]!, first.runId, stale, {}, 'k-new');
    expect(still.text).to.contain('BUDGET_RESERVATION_MISSING');
  });
});

describe('SEC-C-11: runTokenRefusal, the one rule', () => {
  const NOW = 1_000_000;
  const run = (fields: Partial<HarnessRun>): HarnessRun =>
    ({
      status: 'running', attemptId: 'a-2', fence: 2, leaseExpiresAt: NOW + 1, updatedAt: NOW - 10, ...fields,
    }) as HarnessRun;
  const holder = { attemptId: 'a-2', fence: 2 };

  it('act: only the running, lease-holding attempt', () => {
    expect(runTokenRefusal(run({}), holder, 'act', NOW)).to.equal(null);
    expect(runTokenRefusal(run({ leaseExpiresAt: NOW }), holder, 'act', NOW)).to.equal('run_not_leased');
    expect(runTokenRefusal(run({ status: 'cancelling' }), holder, 'act', NOW)).to.equal('run_not_leased');
    expect(runTokenRefusal(run({}), { attemptId: 'a-1', fence: 1 }, 'act', NOW)).to.equal('attempt_superseded');
    expect(runTokenRefusal(run({}), { attemptId: 'a-1', fence: 2 }, 'act', NOW)).to.equal('run_not_leased');
  });

  it('report: the holding attempt, while the run runs or is cancelled; never once it let the run go', () => {
    expect(runTokenRefusal(run({ status: 'cancelling' }), holder, 'report', NOW)).to.equal(null);
    expect(runTokenRefusal(run({ leaseExpiresAt: NOW }), holder, 'report', NOW)).to.equal('run_not_leased');
    for (const status of ['completed', 'failed', 'cancelled', 'queued', 'paused', 'waiting_approval'] as const) {
      const letGo = run({ status, attemptId: null, leaseExpiresAt: null, updatedAt: NOW });
      expect(runTokenRefusal(letGo, holder, 'report', NOW), status).to.equal('run_not_leased');
    }
    expect(runTokenRefusal(run({ fence: 3, attemptId: 'a-3' }), holder, 'report', NOW)).to.equal('attempt_superseded');
  });

  it('settle: any attempt of the run', () => {
    expect(runTokenRefusal(run({ fence: 9, attemptId: 'a-9' }), { attemptId: 'a-1', fence: 1 }, 'settle', NOW)).to.equal(null);
  });
});
