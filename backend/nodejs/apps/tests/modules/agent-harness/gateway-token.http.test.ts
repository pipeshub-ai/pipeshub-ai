/**
 * E2 NA-6, NA-7 over HTTP: the real internal router and gateway-token router,
 * GatewayTokenService, ExecutionService and in-memory stores.
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
import { GatewayTokenAuthority } from '../../../src/modules/agent-harness/application/gateway-token-authority';
import { GatewayTokenService } from '../../../src/modules/agent-harness/application/gateway-token-service';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_GATEWAY_TOKENS,
  createHarnessAuthority,
} from '../../../src/modules/agent-harness/composition';
import {
  AttemptKeyring,
  PublishedAttemptKeys,
  WorkerCredentialSet,
} from '../../../src/modules/agent-harness/domain/authority';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { gatewayGrantDigests } from '../../../src/modules/agent-harness/domain/gateway-token';
import { createAgentHarnessInternalRouter } from '../../../src/modules/agent-harness/transport/internal-routes';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import {
  contractClock,
  ContractClock,
  ContractHarness,
  createContractHarness,
} from './support/execution-store-contract';
import { HARNESS_ENABLED } from './support/rollout';
import { proofFor, testBindingKeys } from './support/binding-proof';
import { bindingProof } from '../../../src/modules/agent-harness/domain/sandbox-binding';
import { HarnessUnauthorizedError } from '../../../src/modules/agent-harness/domain/errors';
import { CURRENT_WORKER_CAPABILITIES } from './support/worker-capabilities';

describe('E2: gateway-token routes over HTTP', function () {
  this.timeout(20_000);
  let server: Server;
  let base: string;
  let h: ContractHarness;
  let clock: ContractClock;
  let credential: string;
  // SEC-C-10: a gateway token is minted with the egress proxy's own credential.
  let proxyCredential: string;
  let gateway: GatewayTokenAuthority;

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
      attemptPublicKeys:
        new InMemoryConfigDocumentStore<PublishedAttemptKeys>(),
      roleCredentials: {
        scrape: new InMemoryConfigDocumentStore<WorkerCredentialSet>(),
        'egress-proxy': new InMemoryConfigDocumentStore<WorkerCredentialSet>(),
        'sandbox-broker': new InMemoryConfigDocumentStore<WorkerCredentialSet>(),
      },
    };
    const authority = createHarnessAuthority(stores, h.service, clock);
    await authority.provision(null);
    credential = (await stores.workerCredential.read()).value!.current;
    proxyCredential = (await authority.credentialFor('egress-proxy'))!;
    gateway = new GatewayTokenAuthority({
      signingKeys: new InMemoryConfigDocumentStore(),
      publishedKeys: new InMemoryConfigDocumentStore(),
      clock,
    });
    const container = new Container();
    container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue(h.service);
    container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue(authority);
    container
      .bind(AGENT_HARNESS_GATEWAY_TOKENS)
      .toConstantValue(new GatewayTokenService({
          tokens: gateway,
          runs: h.service,
          clock,
          bindingKeys: testBindingKeys,
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

  const claimed = async (): Promise<{
    run: HarnessRun;
    attemptToken: string;
  }> => {
    const admitted = await h.admit();
    const response = await fetch(
      `${base}/internal/runs/${admitted.run.runId}/claim`,
      {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          authorization: `Bearer ${credential}`,
        },
        body: JSON.stringify({
          orgId: admitted.run.orgId,
          workerId: 'worker-a',
          leaseDurationMs: 60_000,
          workerCapabilities: CURRENT_WORKER_CAPABILITIES,
        }),
      },
    );
    const body = (await response.json()) as Record<string, any>;
    expect(response.status, JSON.stringify(body)).to.equal(200);
    return { run: body['run'], attemptToken: body['attemptToken']['token'] };
  };

  /** As the proxy asks: by default with the broker's proof for the org and attempt it names (R38IDENT-FU-7). */
  const ask = (
    run: HarnessRun,
    bearer: string | null,
    body: Record<string, unknown> = {},
  ) => {
    const named = { orgId: run.orgId, attemptId: run.attemptId, ...body };
    return fetch(`${base}/internal/runs/${run.runId}/gateway-token`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(bearer === null ? {} : { authorization: `Bearer ${bearer}` }),
      },
      body: JSON.stringify({
        bindingProof: proofFor(
          named.orgId,
          run.runId,
          String(named.attemptId),
        ),
        ...named,
      }),
    });
  };

  it('NA-6: the egress proxy’s credential gets a token bound from Node’s own records (SEC-C-10)', async () => {
    const { run } = await claimed();
    const response = await ask(run, proxyCredential);
    expect(response.status).to.equal(200);
    expect(response.headers.get('cache-control')).to.equal('no-store');
    const body = (await response.json()) as {
      token: string;
      expiresAt: number;
    };
    const pinned = (await h.service.pinned(run.orgId, run.runId))!;
    expect(await gateway.verify(body.token)).to.deep.equal({
      binding: 'run',
      orgId: run.orgId,
      sessionId: run.sessionId,
      runId: run.runId,
      parentRunId: null,
      attemptId: run.attemptId,
      fence: run.fence,
      ...gatewayGrantDigests(pinned.pinned!.spec),
    });
  });

  it('NA-6: no credential, an attempt token or a wrong credential is 401', async () => {
    const { run, attemptToken } = await claimed();
    expect((await ask(run, null)).status).to.equal(401);
    expect((await ask(run, attemptToken)).status).to.equal(401);
    expect((await ask(run, 'phw_wrong')).status).to.equal(401);
    // SEC-C-10: the worker's credential claims runs; it does not mint gateway tokens.
    expect((await ask(run, credential)).status).to.equal(401);
  });

  it('NA-6: another attempt, a released lease or an expired lease is refused (409 RUN_NOT_LEASED)', async () => {
    const { run } = await claimed();
    const other = await ask(run, proxyCredential, { attemptId: 'attempt-other' });
    expect(other.status).to.equal(409);
    expect(JSON.stringify(await other.json())).to.include('RUN_NOT_LEASED');
    clock.set(clock.now() + 61_000);
    const expired = await ask(run, proxyCredential);
    expect(expired.status).to.equal(409);
  });

  it('NA-6: a run in another org is not found; a malformed body is 400', async () => {
    const { run } = await claimed();
    expect(
      (await ask(run, proxyCredential, { orgId: 'org-elsewhere' })).status,
    ).to.equal(404);
    expect((await ask(run, proxyCredential, { fence: 1 })).status).to.equal(400);
  });

  describe('R38IDENT-FU-7: only a run attempt the broker put behind the proxy', () => {
    const refusedWith = async (response: globalThis.Response): Promise<number> => {
      expect(response.headers.get('cache-control') ?? '').to.not.include('public');
      const text = await response.text();
      expect(text).to.not.include('"token"');
      return response.status;
    };

    it('a proof the broker made for another run, attempt or org is 401', async () => {
      const { run } = await claimed();
      const elsewhere = [
        proofFor(run.orgId, 'run-other', run.attemptId!),
        proofFor(run.orgId, run.runId, 'attempt-other'),
        proofFor('org-elsewhere', run.runId, run.attemptId!),
        // Field boundaries move nothing: the fields are length-prefixed.
        proofFor(run.orgId + run.runId, '', run.attemptId!),
      ];
      for (const bindingProof of elsewhere) {
        expect(await refusedWith(await ask(run, proxyCredential, { bindingProof }))).to.equal(401);
      }
    });

    it('a proof under another key, tampered, re-encoded or empty is 401; none at all is 400', async () => {
      const { run } = await claimed();
      const genuine = proofFor(run.orgId, run.runId, run.attemptId!);
      const mac = genuine.slice(3);
      const flipped = `${mac[0] === 'A' ? 'B' : 'A'}${mac.slice(1)}`;
      // The last character's spare bits decode to the same bytes: a re-encoding, not the broker's proof.
      const last = mac.at(-1)!;
      const respelled = `${mac.slice(0, -1)}${last === 'A' ? 'B' : String.fromCharCode(last.charCodeAt(0) ^ 1)}`;
      const forged = [
        bindingProof(Buffer.alloc(32, 9), { orgId: run.orgId, runId: run.runId, attemptId: run.attemptId! }),
        `v1.${flipped}`,
        `v2.${mac}`,
        `V1.${mac}`,
        `v1.${mac}=`,
        ` ${genuine}`,
        `v1.${Buffer.from(mac, 'base64url').toString('base64')}`,
        `v1.${respelled}`,
        '',
      ];
      for (const bindingProof of forged) {
        expect(await refusedWith(await ask(run, proxyCredential, { bindingProof })), bindingProof).to.be.oneOf([400, 401]);
      }
      const response = await fetch(`${base}/internal/runs/${run.runId}/gateway-token`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', authorization: `Bearer ${proxyCredential}` },
        body: JSON.stringify({ orgId: run.orgId, attemptId: run.attemptId }),
      });
      expect(response.status).to.equal(400);
    });

    it('a genuine proof still needs the proxy’s own credential, and the lease', async () => {
      const { run } = await claimed();
      expect((await ask(run, credential)).status).to.equal(401);
      expect((await ask(run, null)).status).to.equal(401);
      clock.set(clock.now() + 61_000);
      expect((await ask(run, proxyCredential)).status).to.equal(409);
    });

    it('a proof kept from an earlier attempt mints nothing once the run is claimed again', async () => {
      const admitted = await h.admit();
      const claim = async (workerId: string): Promise<HarnessRun> => {
        const response = await fetch(`${base}/internal/runs/${admitted.run.runId}/claim`, {
          method: 'POST',
          headers: { 'content-type': 'application/json', authorization: `Bearer ${credential}` },
          body: JSON.stringify({
            orgId: admitted.run.orgId,
            workerId,
            leaseDurationMs: 1_000,
            workerCapabilities: CURRENT_WORKER_CAPABILITIES,
          }),
        });
        const body = (await response.json()) as Record<string, any>;
        expect(response.status, JSON.stringify(body)).to.equal(200);
        return body['run'];
      };
      const first = await claim('worker-a');
      const earlier = proofFor(first.orgId, first.runId, first.attemptId!);
      clock.set(clock.now() + 1_500);
      const now = await claim('worker-b');
      expect(now.attemptId).to.not.equal(first.attemptId);
      expect((await ask(now, proxyCredential, { attemptId: first.attemptId, bindingProof: earlier })).status).to.equal(409);
      expect((await ask(now, proxyCredential, { bindingProof: earlier })).status).to.equal(401);
      expect((await ask(now, proxyCredential)).status).to.equal(200);
    });

    it('a Node holding no broker key honours no proxy request', async () => {
      const { run } = await claimed();
      const keyless = new GatewayTokenService({ tokens: gateway, runs: h.service, clock });
      const refused = await keyless
        .forAttempt(run.orgId, run.runId, run.attemptId!, proofFor(run.orgId, run.runId, run.attemptId!))
        .catch((error: unknown) => error);
      expect(refused).to.be.instanceOf(HarnessUnauthorizedError);
    });
  });

  const grantOf = (run: HarnessRun, bearer: string | null) =>
    fetch(`${base}/internal/runs/${run.runId}/model-grant`, {
      headers: bearer === null ? {} : { authorization: `Bearer ${bearer}` },
    });

  const gatewayTokenOf = async (run: HarnessRun): Promise<string> => {
    const response = await ask(run, proxyCredential);
    return ((await response.json()) as { token: string }).token;
  };

  it('NG-2: the run’s gateway token reads the run’s pinned model grant', async () => {
    const { run } = await claimed();
    const response = await grantOf(run, await gatewayTokenOf(run));
    const body = (await response.json()) as Record<string, any>;
    expect(response.status, JSON.stringify(body)).to.equal(200);
    expect(response.headers.get('cache-control')).to.equal('no-store');
    const pinned = (await h.service.pinned(run.orgId, run.runId))!.pinned!;
    expect(body).to.deep.equal({
      runId: run.runId,
      attemptId: run.attemptId,
      routesDigest: gatewayGrantDigests(pinned.spec).routesDigest,
      routes: pinned.spec.model.policy.map((route) => ({
        provider: route.provider,
        model: route.model,
      })),
      ...(pinned.spec.model.reasoningEffort !== undefined
        ? { reasoningEffort: pinned.spec.model.reasoningEffort }
        : {}),
      budgets: {
        maxCostUsd: pinned.spec.budgets.maxCostUsd,
        maxTokens: pinned.spec.budgets.maxTokens,
      },
      // A1: who acts from which engine, for the gateway's vendor-rule and key-ownership checks.
      actorId: run.actorId,
      engine: 'loop',
      // G-45: the gateway serves a provider's limited slots to higher classes first.
      priorityClass: 'interactive',
    });
  });

  it('NG-3: no token, the worker credential, an attempt token or another run’s token is 401', async () => {
    const { run, attemptToken } = await claimed();
    const other = await claimed();
    for (const bearer of [
      null,
      credential,
      attemptToken,
      await gatewayTokenOf(other.run),
    ]) {
      expect((await grantOf(run, bearer)).status).to.equal(401);
    }
  });

  it('NG-4: once the attempt no longer holds the lease the grant is refused (409)', async () => {
    const { run } = await claimed();
    const token = await gatewayTokenOf(run);
    clock.set(clock.now() + 61_000);
    const response = await grantOf(run, token);
    expect(response.status).to.equal(409);
    expect(JSON.stringify(await response.json())).to.include('RUN_NOT_LEASED');
  });
});
