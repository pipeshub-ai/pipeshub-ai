/**
 * C-11: a lost claim race is normal operation (a broker hint for a run another
 * attempt holds). Through the real internal router and the app's
 * ErrorMiddleware it answers 409 as before, but is logged at info with its
 * reason code, never at error with a stack. A claim an engine switch refuses
 * (WP-111) answers the same 409 and leaves a live attempt running.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { Container } from 'inversify';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { InMemoryConfigDocumentStore } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_EXECUTION_SERVICE,
  createHarnessAuthority,
} from '../../../src/modules/agent-harness/composition';
import {
  AGENT_HARNESS_INTERNAL_PREFIX,
  createAgentHarnessInternalRouter,
} from '../../../src/modules/agent-harness/transport/internal-routes';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import {
  contractClock,
  ContractHarness,
  createContractHarness,
} from './support/execution-store-contract';
import { CHILD_RESULT } from './support/child-run-contract';
import { CURRENT_WORKER_CAPABILITIES } from './support/worker-capabilities';
import { ENGINE_HOLDER, engineOffService, heldEngineChild } from './support/engine-child';

describe('agent harness claim race logging (C-11)', function () {
  this.timeout(20_000);
  let server: Server;
  let base: string;
  let h: ContractHarness;
  let credential: string;
  let originalLogger: unknown;
  let log: { error: sinon.SinonStub; warn: sinon.SinonStub; info: sinon.SinonStub; debug: sinon.SinonStub };

  beforeEach(async () => {
    const storeClock = contractClock();
    const store = new InMemoryExecutionStore(storeClock);
    h = createContractHarness(
      {
        executionStore: store,
        journal: store.journal,
        payloads: new InMemoryPayloadStore(),
        admissionStore: store,
        childRuns: store,
        dispatches: async () => [],
        deletePayload: async () => undefined,
      },
      storeClock,
    );
    const stores = {
      workerCredential: new InMemoryConfigDocumentStore(),
      attemptSigningKeys: new InMemoryConfigDocumentStore(),
      attemptPublicKeys: new InMemoryConfigDocumentStore(),
    };
    // Every engine is switched off for the routes; `h.service` is the holder's own, earlier claim.
    const served = engineOffService(h.backend);
    const authority = createHarnessAuthority(stores as never, served);
    await authority.provision(null);
    credential = ((await stores.workerCredential.read()).value as { current: string }).current;

    log = { error: sinon.stub(), warn: sinon.stub(), info: sinon.stub(), debug: sinon.stub() };
    originalLogger = (ErrorMiddleware as any).logger;
    (ErrorMiddleware as any).logger = log;

    const container = new Container();
    container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue(served);
    container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue(authority);
    const app = express();
    app.use(express.json());
    app.use(AGENT_HARNESS_INTERNAL_PREFIX, createAgentHarnessInternalRouter(container));
    app.use(ErrorMiddleware.handleError());
    server = await new Promise<Server>((resolve) => {
      const listening = app.listen(0, () => resolve(listening));
    });
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}${AGENT_HARNESS_INTERNAL_PREFIX}`;
  });

  afterEach(async () => {
    (ErrorMiddleware as any).logger = originalLogger;
    await new Promise((resolve) => server.close(resolve));
  });

  const claim = async (runId: string, orgId: string, workerId: string, workerCapabilities?: unknown) => {
    const response = await fetch(`${base}/runs/${runId}/claim`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${credential}` },
      body: JSON.stringify({ orgId, workerId, leaseDurationMs: 2_000, workerCapabilities }),
    });
    return { status: response.status, body: (await response.json()) as Record<string, any> };
  };

  it('answers the loser 409 and logs it at info with the reason code', async () => {
    const { run } = await h.admit();
    expect((await claim(run.runId, run.orgId, 'worker-a')).status).to.equal(200);

    const lost = await claim(run.runId, run.orgId, 'worker-b');

    expect(lost.status).to.equal(409);
    expect(lost.body.error.code).to.equal('HTTP_AGENT_HARNESS_RUN_NOT_CLAIMABLE');
    expect(log.error.called, 'an expected refusal is not an error').to.be.false;
    const refused = log.info.getCalls().find((c) => c.args[0] === 'http.request.error');
    expect(refused, 'the refusal is logged at info').to.not.equal(undefined);
    expect(refused!.args[1]).to.include({ status: 409, method: 'POST', error_class: 'conflict' });
    expect(refused!.args[1].error.code).to.equal('HTTP_AGENT_HARNESS_RUN_NOT_CLAIMABLE');
    // The logger keeps a stack only on an ERROR line (main #5516), so an info refusal is written without one.
  });

  it('WP-111: an engine switched off never cuts a live attempt short: another claim is 409, the run stays running, its holder commits', async () => {
    const held = await heldEngineChild(h, 60_000);
    h.setNow(held.leaseExpiresAt! - 1);

    const refused = await claim(held.runId, held.orgId, 'engine-b', CURRENT_WORKER_CAPABILITIES);

    expect(refused.status).to.equal(409);
    expect(refused.body.error.code).to.equal('HTTP_AGENT_HARNESS_RUN_NOT_CLAIMABLE');
    expect(await h.service.get(held.orgId, held.runId)).to.include({
      status: 'running',
      workerId: ENGINE_HOLDER,
      attemptId: held.attemptId,
    });
    const done = await h.service.commit({
      commandId: 'engine-a:done',
      orgId: held.orgId,
      runId: held.runId,
      workerId: ENGINE_HOLDER,
      attemptId: held.attemptId as string,
      fence: held.fence,
      events: [],
      legacyEvents: [],
      transition: { status: 'completed', outcome: 'answer', childResult: CHILD_RESULT } as never,
      outbox: [],
    });
    expect(done.run.status).to.equal('completed');
  });
});
