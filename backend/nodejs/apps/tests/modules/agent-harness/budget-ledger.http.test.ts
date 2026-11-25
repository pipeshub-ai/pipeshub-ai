/**
 * P6.5 NH-1, NS-1..NS-3 over HTTP: the real internal router, BudgetLedgerService,
 * ExecutionService, the attempt and gateway token authorities, in-memory stores.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { Container } from 'inversify';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { InMemoryConfigDocumentStore } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import { InMemoryBudgetLedger } from '../../../src/modules/agent-harness/adapters/in-memory-budget-ledger';
import { GatewayTokenAuthority } from '../../../src/modules/agent-harness/application/gateway-token-authority';
import { BudgetLedgerService } from '../../../src/modules/agent-harness/application/budget-ledger-service';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_BUDGET_LEDGER,
  AGENT_HARNESS_EXECUTION_SERVICE,
  createHarnessAuthority,
} from '../../../src/modules/agent-harness/composition';
import {
  AttemptKeyring,
  PublishedAttemptKeys,
  WorkerCredentialSet,
} from '../../../src/modules/agent-harness/domain/authority';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { gatewayGrantDigests } from '../../../src/modules/agent-harness/domain/gateway-token';
import { usdMicros } from '../../../src/modules/agent-harness/domain/budget-ledger';
import { createAgentHarnessInternalRouter } from '../../../src/modules/agent-harness/transport/internal-routes';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import {
  contractClock,
  ContractClock,
  ContractHarness,
  createContractHarness,
} from './support/execution-store-contract';
import { HARNESS_ENABLED } from './support/rollout';
import { CURRENT_WORKER_CAPABILITIES } from './support/worker-capabilities';

describe('P6.5: budget ledger routes over HTTP', function () {
  this.timeout(20_000);
  let server: Server;
  let base: string;
  let h: ContractHarness;
  let clock: ContractClock;
  let credential: string;
  // SEC-C-10: a gateway token is minted with the egress proxy's own credential.
  let gateway: GatewayTokenAuthority;
  let ledger: InMemoryBudgetLedger;

  beforeEach(async () => {
    clock = contractClock();
    const store = new InMemoryExecutionStore(clock);
    h = createContractHarness(
      {
        executionStore: store,
        journal: store.journal,
        payloads: new InMemoryPayloadStore(),
        admissionStore: store,
        dispatches: async () => [],
        deletePayload: async () => undefined,
      },
      clock,
    );
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
    const stores = {
      workerCredential: new InMemoryConfigDocumentStore<WorkerCredentialSet>(),
      attemptSigningKeys: new InMemoryConfigDocumentStore<AttemptKeyring>(),
      attemptPublicKeys: new InMemoryConfigDocumentStore<PublishedAttemptKeys>(),
      roleCredentials: {
        scrape: new InMemoryConfigDocumentStore<WorkerCredentialSet>(),
        'egress-proxy': new InMemoryConfigDocumentStore<WorkerCredentialSet>(),
        'sandbox-broker': new InMemoryConfigDocumentStore<WorkerCredentialSet>(),
      },
    };
    const authority = createHarnessAuthority(stores, h.service, clock);
    await authority.provision(null);
    credential = (await stores.workerCredential.read()).value!.current;
    await authority.credentialFor('egress-proxy');
    gateway = new GatewayTokenAuthority({
      signingKeys: new InMemoryConfigDocumentStore(),
      publishedKeys: new InMemoryConfigDocumentStore(),
      clock,
    });
    ledger = new InMemoryBudgetLedger(() => clock.now());
    const container = new Container();
    container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue(h.service);
    container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue(authority);
    container
      .bind(AGENT_HARNESS_BUDGET_LEDGER)
      .toConstantValue(new BudgetLedgerService({
        ledger,
        runs: h.service,
        attempts: authority,
        gateways: gateway,
        clock,
      }));
    const app = express();
    app.use(express.json());
    app.use('/internal', createAgentHarnessInternalRouter(container));
    app.use(ErrorMiddleware.handleError());
    server = await new Promise<Server>((resolve) => {
      const listening = app.listen(0, () => resolve(listening));
    });
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  });

  afterEach(async () => {
    await new Promise((resolve) => server.close(resolve));
  });

  const claimed = async (): Promise<{ run: HarnessRun; attemptToken: string }> => {
    const admitted = await h.admit();
    const response = await fetch(`${base}/internal/runs/${admitted.run.runId}/claim`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${credential}` },
      body: JSON.stringify({
        orgId: admitted.run.orgId,
        workerId: 'worker-a',
        leaseDurationMs: 60_000,
        workerCapabilities: CURRENT_WORKER_CAPABILITIES,
      }),
    });
    const body = (await response.json()) as Record<string, any>;
    expect(response.status, JSON.stringify(body)).to.equal(200);
    return { run: body['run'], attemptToken: body['attemptToken']['token'] };
  };

  const gatewayToken = async (run: HarnessRun): Promise<string> => {
    const pinned = (await h.service.pinned(run.orgId, run.runId))!.pinned!;
    return (
      await gateway.issue({
        binding: 'run',
        orgId: run.orgId,
        sessionId: run.sessionId,
        runId: run.runId,
        parentRunId: null,
        attemptId: run.attemptId!,
        fence: run.fence,
        ...gatewayGrantDigests(pinned.spec),
      })
    ).token;
  };

  const call = async (
    path: string,
    bearer: string | null,
    body?: Record<string, unknown>,
  ): Promise<{ status: number; body: Record<string, any> }> => {
    const response = await fetch(`${base}/internal${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers: {
        'content-type': 'application/json',
        ...(bearer === null ? {} : { authorization: `Bearer ${bearer}` }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (body === undefined || response.status === 200) {
      expect(response.headers.get('cache-control') ?? 'no-store').to.equal('no-store');
    }
    return { status: response.status, body: (await response.json()) as Record<string, any> };
  };

  const reserve = (run: HarnessRun, bearer: string | null, extra: Record<string, unknown> = {}) =>
    call(`/runs/${run.runId}/budget/reservations`, bearer, {
      key: 'chatcmpl_1',
      requested: { usdMicros: 300_000, tokens: 3_000 },
      minimum: { usdMicros: 1_000, tokens: 100 },
      ttlS: 900,
      ...extra,
    });

  it('NH-1/NS-1: a run admitted with no account opens lazily from its pin, at the opening spend', async () => {
    const { run } = await claimed();
    const token = await gatewayToken(run);
    const reserved = await reserve(run, token, { opening: { usdMicros: 250_000, tokens: 40 } });
    expect(reserved.status, JSON.stringify(reserved.body)).to.equal(200);
    expect(reserved.body['reservation']).to.include({ key: 'chatcmpl_1', state: 'open' });
    expect(reserved.body['reservation']['granted']).to.deep.equal({ usdMicros: 300_000, tokens: 3_000 });
    const pinned = (await h.service.pinned(run.orgId, run.runId))!.pinned!;
    const cap = usdMicros(pinned.spec.budgets.maxCostUsd);
    const left = await call(`/runs/${run.runId}/budget`, token);
    expect(left.body['remaining']['committed']).to.deep.equal({ usdMicros: 250_000, tokens: 40 });
    expect(left.body['remaining']['available']['usdMicros']).to.equal(cap - 250_000 - 300_000);
    const again = await reserve(run, token, { key: 'chatcmpl_2', opening: { usdMicros: 999_999, tokens: 0 } });
    expect(again.status).to.equal(200);
    expect((await ledger.remaining(run.orgId, run.runId)).committed.usdMicros).to.equal(250_000);
  });

  it('NH-1: commit and release with the gateway token; the worker’s attempt token reaches the same account', async () => {
    const { run, attemptToken } = await claimed();
    const token = await gatewayToken(run);
    await reserve(run, token);
    const committed = await call(`/runs/${run.runId}/budget/reservations/chatcmpl_1/commit`, token, {
      actual: { usdMicros: 100_000, tokens: 1_000 },
    });
    expect(committed.status, JSON.stringify(committed.body)).to.equal(200);
    expect(committed.body).to.deep.include({ charged: { usdMicros: 100_000, tokens: 1_000 }, crossed: false });
    expect(committed.body['reservation']['state']).to.equal('committed');
    const fromWorker = await reserve(run, attemptToken, {
      key: 'call_1',
      attemptId: run.attemptId,
      fence: run.fence,
    });
    expect(fromWorker.status, JSON.stringify(fromWorker.body)).to.equal(200);
    const released = await call(`/runs/${run.runId}/budget/reservations/call_1/release`, attemptToken, {
      attemptId: run.attemptId,
      fence: run.fence,
    });
    expect(released.body['reservation']['state']).to.equal('released');
    expect((await ledger.remaining(run.orgId, run.runId)).committed.usdMicros).to.equal(100_000);
  });

  it('WP66-HOLD-N1: a cut call commits its estimate with a hold, and the gateway token reconciles it', async () => {
    const { run } = await claimed();
    const token = await gatewayToken(run);
    await reserve(run, token);
    const committed = await call(`/runs/${run.runId}/budget/reservations/chatcmpl_1/commit`, token, {
      actual: { usdMicros: 20_000, tokens: 200 },
      hold: { usdMicros: 250_000, tokens: 2_500 },
      holdTtlS: 600,
    });
    expect(committed.status, JSON.stringify(committed.body)).to.equal(200);
    expect(committed.body['remaining']).to.deep.include({ estimated: 1, reserved: { usdMicros: 250_000, tokens: 2_500 } });
    const reconciled = await call(`/runs/${run.runId}/budget/reservations/chatcmpl_1/reconcile`, token, {
      actual: { usdMicros: 90_000, tokens: 900 },
    });
    expect(reconciled.status, JSON.stringify(reconciled.body)).to.equal(200);
    expect(reconciled.body['charged']).to.deep.equal({ usdMicros: 90_000, tokens: 900 });
    expect(reconciled.body['remaining']).to.deep.include({ estimated: 0, reserved: { usdMicros: 0, tokens: 0 } });
    const timeline = await ledger.remaining(run.orgId, run.runId);
    expect(timeline.committed.usdMicros).to.equal(90_000);
  });

  it('WP66-HOLD-N2: a hold names its time, a reconcile holds nothing, and no caller names a hold', async () => {
    const { run } = await claimed();
    const token = await gatewayToken(run);
    await reserve(run, token);
    const path = `/runs/${run.runId}/budget/reservations/chatcmpl_1`;
    expect((await call(`${path}/commit`, token, { actual: { usdMicros: 1, tokens: 1 }, hold: { usdMicros: 1, tokens: 1 } })).status).to.equal(400);
    expect((await call(`${path}/commit`, token, { actual: { usdMicros: 1, tokens: 1 }, holdTtlS: 60 })).status).to.equal(400);
    expect((await call(`${path}/commit`, token, { actual: { usdMicros: 1, tokens: 1 }, hold: { usdMicros: 1, tokens: 1 }, holdTtlS: 7_200 })).status).to.equal(400);
    expect((await call(`${path}/reconcile`, token, { actual: { usdMicros: 1, tokens: 1 }, hold: { usdMicros: 1, tokens: 1 }, holdTtlS: 60 })).status).to.equal(400);
    expect((await call(`/runs/${run.runId}/budget/reservations/chatcmpl_1%23hold/release`, token, {})).status).to.equal(400);
    const open = await call(`${path}/reconcile`, token, { actual: { usdMicros: 1, tokens: 1 } });
    expect([open.status, open.body['error']?.['code']]).to.deep.equal([409, 'HTTP_AGENT_HARNESS_BUDGET_RESERVATION_SETTLED']);
    const other = await claimed();
    const theirs = await gatewayToken(other.run);
    expect((await call(`${path}/reconcile`, theirs, { actual: { usdMicros: 1, tokens: 1 } })).status).to.equal(401);
  });

  it('GA-15 MROLE-9: a commit names its model role; a role that is not one of the four is 400', async () => {
    const { run } = await claimed();
    const token = await gatewayToken(run);
    await reserve(run, token);
    const unknown = await call(`/runs/${run.runId}/budget/reservations/chatcmpl_1/commit`, token, {
      actual: { usdMicros: 1_000, tokens: 10 },
      modelRole: 'turbo',
    });
    expect(unknown.status).to.equal(400);
    const fast = await call(`/runs/${run.runId}/budget/reservations/chatcmpl_1/commit`, token, {
      actual: { usdMicros: 1_000, tokens: 10 },
      modelRole: 'fast',
    });
    expect(fast.status, JSON.stringify(fast.body)).to.equal(200);
    expect(fast.body).to.deep.include({ charged: { usdMicros: 1_000, tokens: 10 } });
  });

  it('NH-1: no token, the worker credential or another run’s token is 401', async () => {
    const { run } = await claimed();
    const other = await claimed();
    for (const bearer of [null, credential, await gatewayToken(other.run), other.attemptToken]) {
      expect((await reserve(run, bearer)).status).to.equal(401);
    }
  });

  it('NH-1: refusals are 409 AGENT_HARNESS_BUDGET_*; a malformed body or key is 400', async () => {
    const { run } = await claimed();
    const token = await gatewayToken(run);
    const pinned = (await h.service.pinned(run.orgId, run.runId))!.pinned!;
    const cap = usdMicros(pinned.spec.budgets.maxCostUsd);
    const big = await reserve(run, token, {
      requested: { usdMicros: cap + 1, tokens: 0 },
      minimum: { usdMicros: cap + 1, tokens: 0 },
    });
    expect(big.status).to.equal(409);
    expect(JSON.stringify(big.body)).to.include('AGENT_HARNESS_BUDGET_INSUFFICIENT');
    const missing = await call(`/runs/${run.runId}/budget/reservations/nope/commit`, token, {
      actual: { usdMicros: 1, tokens: 0 },
    });
    expect(JSON.stringify(missing.body)).to.include('AGENT_HARNESS_BUDGET_RESERVATION_MISSING');
    expect((await reserve(run, token, { key: 'bad key/' })).status).to.equal(400);
    expect((await reserve(run, token, { requested: { usdMicros: -1, tokens: 0 } })).status).to.equal(400);
    expect((await reserve(run, token, { unexpected: true })).status).to.equal(400);
    expect(
      (await call(`/runs/${run.runId}/budget/reservations/chatcmpl_9/commit`, token, {})).status,
    ).to.equal(400);
  });

  it('NS-2/NS-3: a lapsed lease may settle but not reserve; an expired token settles within the grace', async () => {
    const { run } = await claimed();
    const token = await gatewayToken(run);
    await reserve(run, token);
    clock.set(clock.now() + 11 * 60_000);
    const late = await reserve(run, token, { key: 'chatcmpl_2' });
    expect(late.status).to.equal(401);
    const settled = await call(`/runs/${run.runId}/budget/reservations/chatcmpl_1/commit`, token, {
      actual: { usdMicros: 5_000, tokens: 50 },
    });
    expect(settled.status, JSON.stringify(settled.body)).to.equal(200);
    clock.set(clock.now() + 61 * 60_000);
    const tooLate = await call(`/runs/${run.runId}/budget/reservations/chatcmpl_1/release`, token, {});
    expect(tooLate.status).to.equal(401);
  });

  it('NS-2: a token whose attempt no longer holds the lease cannot reserve (409 RUN_NOT_LEASED)', async () => {
    const { run } = await claimed();
    const token = await gatewayToken(run);
    clock.set(clock.now() + 61_000);
    const refused = await reserve(run, token);
    expect(refused.status).to.equal(409);
    expect(JSON.stringify(refused.body)).to.include('RUN_NOT_LEASED');
  });

  it("SR-13: stage 1's request-task budget routes are gone (404), whatever the caller presents", async () => {
    for (const path of ['/requests/req-1/tasks/ct-1/budget', '/requests/req-1/tasks/ct-1/budget/reservations']) {
      const response = await fetch(`${base}/internal${path}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', authorization: `Bearer ${credential}` },
        body: '{}',
      });
      expect(response.status).to.equal(404);
    }
  });
});
