/**
 * P6.1 over HTTP: the real internal router, child controller, attempt-token
 * authority, ChildRunService, ExecutionService and in-memory store (CH-1),
 * and a parent reading the sealed content its child wrote (CH-3).
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
import { ChildRunService } from '../../../src/modules/agent-harness/application/child-run-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunContentService } from '../../../src/modules/agent-harness/application/run-content-service';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_CHILD_RUNS,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_RUN_CONTENT,
  createHarnessAuthority,
} from '../../../src/modules/agent-harness/composition';
import {
  AttemptKeyring,
  PublishedAttemptKeys,
  WorkerCredentialSet,
} from '../../../src/modules/agent-harness/domain/authority';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { createAgentHarnessInternalRouter } from '../../../src/modules/agent-harness/transport/internal-routes';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import {
  contractClock,
  ContractHarness,
  createContractHarness,
} from './support/execution-store-contract';
import { dataKeyRing, testCipher } from './support/data-protection';
import { agentDefinition } from './support/agent-definition';
import { HARNESS_ENABLED } from './support/rollout';
import { CURRENT_WORKER_CAPABILITIES } from './support/worker-capabilities';

const LEASE_MS = 5_000;

interface Claimed {
  run: HarnessRun;
  token: string;
}

describe('child runs over HTTP (P6.1)', function () {
  this.timeout(20_000);
  let server: Server;
  let base: string;
  let h: ContractHarness;
  let credential: string;
  let store: InMemoryExecutionStore;

  beforeEach(async () => {
    const storeClock = contractClock();
    store = new InMemoryExecutionStore(storeClock);
    const payloads = new InMemoryPayloadStore();
    h = createContractHarness(
      {
        executionStore: store,
        journal: store.journal,
        payloads,
        admissionStore: store,
        childRuns: store,
        dispatches: async () => [],
        deletePayload: async () => undefined,
      },
      storeClock,
    );
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
    h.setAgentDefinition((agentKey) =>
      agentDefinition({ agentKey, maxChildren: 1 }),
    );
    const now = Date.UTC(2026, 8, 27);
    const stores = {
      workerCredential: new InMemoryConfigDocumentStore<WorkerCredentialSet>(),
      attemptSigningKeys: new InMemoryConfigDocumentStore<AttemptKeyring>(),
      attemptPublicKeys:
        new InMemoryConfigDocumentStore<PublishedAttemptKeys>(),
    };
    const authority = createHarnessAuthority(stores, h.service, {
      now: () => now,
    });
    await authority.provision(null);
    credential = (await stores.workerCredential.read()).value!.current;
    const container = new Container();
    container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue(h.service);
    container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue(authority);
    container
      .bind(AGENT_HARNESS_CHILD_RUNS)
      .toConstantValue(
        new ChildRunService({
          runs: store,
          children: store,
          journals: new JournalService(payloads, testCipher()),
        }),
      );
    container
      .bind(AGENT_HARNESS_RUN_CONTENT)
      .toConstantValue(
        new RunContentService(h.service, dataKeyRing().cipher, payloads),
      );
    const app = express();
    app.use(express.json());
    app.use('/internal', createAgentHarnessInternalRouter(container));
    app.use(ErrorMiddleware.handleError());
    server = await new Promise<Server>((resolve) => {
      const listening = app.listen(0, () => resolve(listening));
    });
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}/internal`;
  });

  afterEach(async () => {
    await new Promise((resolve) => server.close(resolve));
  });

  const claimById = async (
    orgId: string,
    runId: string,
    workerId: string,
  ): Promise<Claimed> => {
    const response = await fetch(`${base}/runs/${runId}/claim`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${credential}`,
      },
      body: JSON.stringify({
        orgId,
        workerId,
        leaseDurationMs: LEASE_MS,
        workerCapabilities: CURRENT_WORKER_CAPABILITIES,
      }),
    });
    const body = (await response.json()) as Record<string, any>;
    expect(response.status, JSON.stringify(body)).to.equal(200);
    return { run: body['run'], token: body['attemptToken']['token'] };
  };

  const parent = async (): Promise<Claimed> => {
    const admitted = await h.admit({
      agentKey: 'agent-1',
      authorization: 'Bearer user',
    });
    return claimById(admitted.run.orgId, admitted.run.runId, 'worker-p');
  };

  const spawn = (
    c: Claimed,
    body: Record<string, unknown> = {},
    bearer: string = c.token,
  ) =>
    fetch(`${base}/runs/${c.run.runId}/children`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${bearer}`,
      },
      body: JSON.stringify({
        attemptId: c.run.attemptId,
        fence: c.run.fence,
        invocationId: 't1.0',
        goal: 'summarise the tickets',
        ...body,
      }),
    });

  const code = async (response: Response): Promise<string> =>
    ((await response.json()) as { error: { code: string } }).error.code;

  it('CH-1: admits a child, replays it, and refuses with reason codes', async () => {
    const c = await parent();
    const first = await spawn(c, {
      grants: { tools: ['jira@inst-42/search_issues'] },
    });
    const body = (await first.json()) as {
      childRunId: string;
      created: boolean;
    };
    expect(first.status, JSON.stringify(body)).to.equal(200);
    expect(body.created).to.equal(true);
    const replay = await spawn(c, {
      grants: { tools: ['jira@inst-42/search_issues'] },
    });
    expect(await replay.json()).to.deep.equal({ ...body, created: false });

    const limit = await spawn(c, { invocationId: 't1.1' });
    expect(limit.status).to.equal(409);
    expect(await code(limit)).to.equal('HTTP_AGENT_HARNESS_CHILD_LIMIT_REACHED');

    const wide = await spawn(c, {
      invocationId: 't1.2',
      grants: { egress: 'allowlist:x' },
    });
    expect(wide.status).to.equal(409);
    expect(await code(wide)).to.equal('HTTP_AGENT_HARNESS_CHILD_GRANT_TOO_LARGE');

    const unknownField = await spawn(c, { invocationId: 't1.3', runKind: 'x' });
    expect(unknownField.status).to.equal(400);

    const other = await parent();
    const foreign = await spawn(c, {}, other.token);
    expect(foreign.status).to.equal(401);
  });

  it("OWD-2 / SEC-AZ-04: a child cannot widen through admission: never more grants, never another principal", async () => {
    const admitted = await h.admit({
      agentKey: 'support-bot',
      authorization: 'Bearer svc',
      via: 'service',
      principalKind: 'agent_service',
    });
    const c = await claimById(admitted.run.orgId, admitted.run.runId, 'worker-p');
    expect(c.run).to.not.have.property('attribution');

    for (const grants of [{ egress: 'allowlist:x' }, { tools: ['slack@inst-1/post_message'] }]) {
      const wider = await spawn(c, { invocationId: 't3.0', grants });
      expect(wider.status, JSON.stringify(grants)).to.equal(409);
      expect(await code(wider)).to.equal('HTTP_AGENT_HARNESS_CHILD_GRANT_TOO_LARGE');
    }
    for (const identity of [
      { principalRef: 'p_admin' },
      { onBehalfOf: 'p_admin' },
      { actorId: 'admin' },
      { principalKind: 'user' },
    ]) {
      const refused = await spawn(c, { invocationId: 't3.1', ...identity });
      expect(refused.status, JSON.stringify(identity)).to.equal(400);
    }

    const ok = await spawn(c, { invocationId: 't3.2', grants: { tools: ['jira@inst-42/search_issues'] } });
    const body = (await ok.json()) as { childRunId: string };
    expect(ok.status, JSON.stringify(body)).to.equal(200);
    const child = await store.get(admitted.run.orgId, body.childRunId);
    expect(child?.actorId).to.equal(admitted.run.actorId);
    expect(child?.attribution).to.deep.equal(admitted.run.attribution);
    expect(child?.attribution).to.deep.include({ principalKind: 'agent_service', onBehalfOf: 'agent:support-bot' });
  });

  it('NK-1 (K-L): a sub-agent definition is pinned in the child spec and part of the request', async () => {
    const c = await parent();
    const subAgent = { role: 'jira', domainAgent: 'jira_agent', toolNames: ['jira_agent'] };
    const first = await spawn(c, { subAgent, grants: { maxTokens: 250_000 } });
    const body = (await first.json()) as { childRunId: string; created: boolean };
    expect(first.status, JSON.stringify(body)).to.equal(200);
    const child = await fetch(`${base}/runs/${body.childRunId}/claim`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${credential}`,
      },
      body: JSON.stringify({
        orgId: c.run.orgId,
        workerId: 'worker-c',
        leaseDurationMs: LEASE_MS,
        workerCapabilities: CURRENT_WORKER_CAPABILITIES,
      }),
    });
    const claimed = (await child.json()) as Record<string, any>;
    expect(child.status, JSON.stringify(claimed)).to.equal(200);
    const spec = claimed['pinned']['spec'];
    expect(spec['extensions']['x.pipeshub']['subAgent']).to.deep.equal(subAgent);
    expect(spec['budgets']['maxTokens']).to.equal(250_000);

    const changed = await spawn(c, {
      subAgent: { ...subAgent, role: 'confluence' },
      grants: { maxTokens: 250_000 },
    });
    expect(changed.status).to.equal(409);
    expect(await code(changed)).to.equal('HTTP_AGENT_HARNESS_IDEMPOTENCY_CONFLICT');

    for (const invalid of [
      { toolNames: [] },
      { role: 'r'.repeat(129), toolNames: [] },
      { role: 'jira', toolNames: [], model: 'other' },
    ]) {
      const refused = await spawn(c, { invocationId: 't2.0', subAgent: invalid });
      expect(refused.status, JSON.stringify(invalid)).to.equal(400);
    }
  });

  it('CH-3: a parent reads the sealed content its child wrote; another session does not', async () => {
    const c = await parent();
    const spawned = (await (await spawn(c)).json()) as { childRunId: string };
    const child = await claimById(c.run.orgId, spawned.childRunId, 'worker-c');
    const query = (run: HarnessRun, extra = ''): string =>
      `attemptId=${String(run.attemptId)}&fence=${String(run.fence)}${extra}`;
    const put = await fetch(
      `${base}/runs/${child.run.runId}/content?${query(child.run)}`,
      {
        method: 'POST',
        headers: {
          'content-type': 'application/octet-stream',
          authorization: `Bearer ${child.token}`,
        },
        body: Buffer.from('child result'),
      },
    );
    const { ref } = (await put.json()) as { ref: string };
    expect(put.status).to.equal(200);
    const read = (reader: Claimed) =>
      fetch(
        `${base}/runs/${reader.run.runId}/content?${query(reader.run, `&ref=${encodeURIComponent(ref)}`)}`,
        { headers: { authorization: `Bearer ${reader.token}` } },
      );
    const byParent = await read(c);
    expect(byParent.status).to.equal(200);
    expect(Buffer.from(await byParent.arrayBuffer()).toString()).to.equal(
      'child result',
    );
    const stranger = await parent();
    expect((await read(stranger)).status).to.equal(409);
  });
});
