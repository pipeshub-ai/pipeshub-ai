/**
 * SEC2 GR-1, GR-3, GR-5, GR-7 over HTTP: a gateway's refusal of a run's request,
 * reported with the run's gateway token to the real internal router, journaled
 * as `gateway.refused` on the run's stream, and shown in the run's blocked view.
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
  GatewayRefusalJournal,
  MAX_EGRESS_ALLOWED_PER_RUN,
  MAX_EGRESS_VOLUME_ALERTS_PER_RUN,
  MAX_GATEWAY_REFUSALS_PER_RUN,
  MAX_MODEL_FALLBACKS_PER_RUN,
  MAX_SECRETS_HIDDEN_PER_RUN,
} from '../../../src/modules/agent-harness/application/gateway-refusal-journal';
import { AUDIT_EVENT_FIELDS } from '../../../src/modules/agent-harness/domain/audit';
import { validateJournalAppend } from '../../../src/modules/agent-harness/domain/journal';
import { validatingPreparer } from '../../../src/modules/agent-harness/ports/journal-store';
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
import { proofFor, testBindingKeys } from './support/binding-proof';
import { without } from './support/without';

describe('SEC2: gateway refusals over HTTP', function () {
  this.timeout(20_000);
  let server: Server;
  let base: string;
  let h: ContractHarness;
  let clock: ContractClock;
  let credential: string;
  // SEC-C-10: a gateway token is minted with the egress proxy's own credential.
  let proxyCredential: string;
  let gateway: GatewayTokenAuthority;
  let journal: InMemoryExecutionStore['journal'];

  beforeEach(async () => {
    clock = contractClock();
    const store = new InMemoryExecutionStore(clock);
    journal = store.journal;
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
      .toConstantValue(
        new GatewayTokenService({
          tokens: gateway,
          runs: h.service,
          clock,
          bindingKeys: testBindingKeys,
          refusals: new GatewayRefusalJournal(
            store.journal,
            { ...validatingPreparer, resolve: async (events) => events },
            async (work) => work(undefined),
          ) as GatewayRefusalJournal<unknown>,
        }),
      );
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

  const ask = (
    run: HarnessRun,
    bearer: string | null,
    body: Record<string, unknown> = {},
  ) =>
    fetch(`${base}/internal/runs/${run.runId}/gateway-token`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(bearer === null ? {} : { authorization: `Bearer ${bearer}` }),
      },
      body: JSON.stringify({
        orgId: run.orgId,
        attemptId: run.attemptId,
        bindingProof: proofFor(run.orgId, run.runId, run.attemptId!),
        ...body,
      }),
    });

  const runToken = async (run: HarnessRun): Promise<string> => {
    const response = await ask(run, proxyCredential);
    expect(response.status).to.equal(200);
    return ((await response.json()) as { token: string }).token;
  };

  const report = (
    runId: string,
    bearer: string | null,
    body: Record<string, unknown>,
  ) =>
    fetch(`${base}/internal/runs/${runId}/refusals`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(bearer === null ? {} : { authorization: `Bearer ${bearer}` }),
      },
      body: JSON.stringify(body),
    });

  const refusals = async (run: HarnessRun) =>
    (await journal.readRun(run.orgId, run.runId, 0, 500)).filter(
      (event) => event.type === 'gateway.refused',
    );

  const MODEL = { gateway: 'model', reason: 'unsupported_parameter', status: 400, param: 'tools.type' };
  const EGRESS = { gateway: 'egress', reason: 'destination_not_allowed', status: 403, host: 'attacker.example' };

  it('GR-1: a refusal with the run’s token is journaled once on the run’s stream, ids and codes only', async () => {
    const { run } = await claimed();
    const token = await runToken(run);
    const first = await report(run.runId, token, MODEL);
    expect(first.status).to.equal(200);
    expect(await first.json()).to.deep.equal({ outcome: 'recorded' });
    const [event, ...rest] = await refusals(run);
    expect(rest).to.deep.equal([]);
    expect(event).to.include({ writer: 'node', runId: run.runId, critical: false, visibility: 'audit', attemptId: run.attemptId });
    expect(event!.data).to.deep.equal({ ...MODEL, attemptId: run.attemptId });
    expect(AUDIT_EVENT_FIELDS['gateway.refused']!(event!.data!)).to.deep.equal({
      gateway: 'model',
      reason: 'unsupported_parameter',
      status: 400,
      param: 'tools.type',
    });
  });

  it('GR-3: the same reason again is a duplicate; past the cap nothing is appended', async () => {
    const { run } = await claimed();
    const token = await runToken(run);
    expect(await (await report(run.runId, token, MODEL)).json()).to.deep.equal({ outcome: 'recorded' });
    expect(await (await report(run.runId, token, MODEL)).json()).to.deep.equal({ outcome: 'duplicate' });
    for (let i = 1; i < MAX_GATEWAY_REFUSALS_PER_RUN; i += 1) {
      await report(run.runId, token, { ...EGRESS, reason: `reason_${String(i)}` });
    }
    expect(await (await report(run.runId, token, EGRESS)).json()).to.deep.equal({ outcome: 'capped' });
    expect(await refusals(run)).to.have.length(MAX_GATEWAY_REFUSALS_PER_RUN);
  });

  it('GR-5: another run’s token, no token, a worker credential or a malformed report appends nothing', async () => {
    const { run } = await claimed();
    const { run: other } = await claimed();
    const otherToken = await runToken(other);
    const token = await runToken(run);
    expect((await report(run.runId, otherToken, MODEL)).status).to.equal(401);
    expect((await report(run.runId, null, MODEL)).status).to.equal(401);
    expect((await report(run.runId, credential, MODEL)).status).to.equal(401);
    for (const body of [
      { ...MODEL, message: 'free text' },
      { ...MODEL, reason: 'Has Spaces' },
      { ...MODEL, status: 200 },
      { ...EGRESS, host: 'attacker.example/p?d=1' },
      { ...MODEL, param: 'tools.type=web_search' },
      { ...MODEL, gateway: 'tool' },
      { ...MODEL, orgId: 'org-other' },
    ]) {
      expect((await report(run.runId, token, body)).status, JSON.stringify(body)).to.equal(400);
    }
    expect(await refusals(run)).to.deep.equal([]);
    expect(await refusals(other)).to.deep.equal([]);
  });

  const allow = (runId: string, bearer: string | null, body: Record<string, unknown>) =>
    fetch(`${base}/internal/runs/${runId}/egress`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(bearer === null ? {} : { authorization: `Bearer ${bearer}` }),
      },
      body: JSON.stringify(body),
    });

  const allowed = async (run: HarnessRun) =>
    (await journal.readRun(run.orgId, run.runId, 0, 500)).filter(
      (event) => event.type === 'egress.allowed',
    );

  it('OB-03: each refused destination is its own record, not only the first of its reason', async () => {
    const { run } = await claimed();
    const token = await runToken(run);
    expect(await (await report(run.runId, token, EGRESS)).json()).to.deep.equal({ outcome: 'recorded' });
    expect(await (await report(run.runId, token, { ...EGRESS, host: 'other.example' })).json()).to.deep.equal({ outcome: 'recorded' });
    expect(await (await report(run.runId, token, EGRESS)).json()).to.deep.equal({ outcome: 'duplicate' });
    expect((await refusals(run)).map((event) => event.data?.['host'])).to.deep.equal(['attacker.example', 'other.example']);
  });

  it('OB-03: a destination the proxy let through is an audit record once per host and route; bounded; own token only', async () => {
    const { run } = await claimed();
    const token = await runToken(run);
    const API = { host: 'api.github.com', via: 'intercepted' };
    expect(await (await allow(run.runId, token, API)).json()).to.deep.equal({ outcome: 'recorded' });
    expect(await (await allow(run.runId, token, API)).json()).to.deep.equal({ outcome: 'duplicate' });
    const [event, ...rest] = await allowed(run);
    expect(rest).to.deep.equal([]);
    expect(event).to.include({ writer: 'node', critical: false, visibility: 'audit', attemptId: run.attemptId });
    expect(event!.data).to.deep.equal({ ...API, attemptId: run.attemptId });
    expect(AUDIT_EVENT_FIELDS['egress.allowed']!(event!.data!)).to.deep.equal(API);
    for (let i = 1; i < MAX_EGRESS_ALLOWED_PER_RUN; i += 1) {
      await allow(run.runId, token, { host: `h${String(i)}.example`, via: 'http' });
    }
    expect(await (await allow(run.runId, token, { host: 'late.example', via: 'http' })).json()).to.deep.equal({ outcome: 'capped' });
    expect(await allowed(run)).to.have.length(MAX_EGRESS_ALLOWED_PER_RUN);
    const { run: other } = await claimed();
    expect((await allow(other.runId, token, API)).status).to.equal(401);
    expect((await allow(other.runId, credential, API)).status).to.equal(401);
    for (const body of [
      { ...API, host: 'api.github.com/p?d=1' },
      { ...API, via: 'smtp' },
      { ...API, path: '/repos' },
    ]) {
      expect((await allow(other.runId, await runToken(other), body)).status, JSON.stringify(body)).to.equal(400);
    }
    expect(await allowed(other)).to.deep.equal([]);
  });

  const volume = (runId: string, bearer: string | null, body: Record<string, unknown>) =>
    fetch(`${base}/internal/runs/${runId}/egress-volume`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(bearer === null ? {} : { authorization: `Bearer ${bearer}` }),
      },
      body: JSON.stringify(body),
    });

  const volumeAlerts = async (run: HarnessRun) =>
    (await journal.readRun(run.orgId, run.runId, 0, 500)).filter(
      (event) => event.type === 'egress.volume_alert',
    );

  it('SEC-EG-14 (C6.12): an unusual egress volume is an audit record once per kind and direction; bounded; own token only', async () => {
    const { run } = await claimed();
    const token = await runToken(run);
    const BASELINE = {
      kind: 'org_baseline',
      direction: 'up',
      bytes: 734003200,
      thresholdBytes: 104857600,
      baselineBytes: 10485760,
      alias: 'tunnel',
    };
    expect(await (await volume(run.runId, token, BASELINE)).json()).to.deep.equal({ outcome: 'recorded' });
    expect(
      await (await volume(run.runId, token, { ...BASELINE, bytes: 900000000 })).json(),
    ).to.deep.equal({ outcome: 'duplicate' });
    const [event, ...rest] = await volumeAlerts(run);
    expect(rest).to.deep.equal([]);
    expect(event).to.include({ writer: 'node', critical: false, visibility: 'audit', attemptId: run.attemptId });
    expect(event!.data).to.deep.equal({ ...BASELINE, attemptId: run.attemptId });
    expect(AUDIT_EVENT_FIELDS['egress.volume_alert']!(event!.data!)).to.deep.equal(BASELINE);
    const CAP = { kind: 'run_cap', direction: 'total', bytes: 2048, thresholdBytes: 1024, alias: 'model-gateway' };
    expect(await (await volume(run.runId, token, CAP)).json()).to.deep.equal({ outcome: 'recorded' });
    for (const direction of ['up', 'down']) {
      await volume(run.runId, token, { ...CAP, kind: 'run_threshold', direction });
    }
    expect(await volumeAlerts(run)).to.have.length(4);
    expect(MAX_EGRESS_VOLUME_ALERTS_PER_RUN).to.be.at.least(4);
    const { run: other } = await claimed();
    expect((await volume(other.runId, token, CAP)).status).to.equal(401);
    expect((await volume(other.runId, null, CAP)).status).to.equal(401);
    expect((await volume(other.runId, credential, CAP)).status).to.equal(401);
    const otherToken = await runToken(other);
    for (const body of [
      { ...CAP, kind: 'dns_entropy' },
      { ...CAP, alias: 'attacker.example' },
      { ...CAP, host: 'attacker.example' },
      { ...CAP, bytes: -1 },
      { ...CAP, bytes: 1.5 },
      { ...CAP, orgId: 'org-other' },
    ]) {
      expect((await volume(other.runId, otherToken, body)).status, JSON.stringify(body)).to.equal(400);
    }
    expect(await volumeAlerts(other)).to.deep.equal([]);
  });

  it('GITF-25 (C6.12, WP-63): bytes the worker fetched for a run alert under their own alias, repository', async () => {
    const { run } = await claimed();
    const token = await runToken(run);
    const CLONE = { kind: 'run_threshold', direction: 'down', bytes: 3221225472, thresholdBytes: 2147483648, alias: 'repository' };
    expect(await (await volume(run.runId, token, CLONE)).json()).to.deep.equal({ outcome: 'recorded' });
    const [event] = await volumeAlerts(run);
    expect(AUDIT_EVENT_FIELDS['egress.volume_alert']!(event!.data!)).to.deep.equal(CLONE);
  });

  it('WP-63: an allowed website’s requests are recorded once per path and use, with the path the activity shows', async () => {
    const { run } = await claimed();
    const token = await runToken(run);
    const read = { host: 'docs.example.com', via: 'intercepted', site: { path: '/api/v2', access: 'read', use: 'read' } };
    const fetched = { ...read, site: { ...read.site, path: '/api/v3' } };
    expect(await (await allow(run.runId, token, read)).json()).to.deep.equal({ outcome: 'recorded' });
    expect(await (await allow(run.runId, token, fetched)).json()).to.deep.equal({ outcome: 'recorded' });
    expect(await (await allow(run.runId, token, read)).json()).to.deep.equal({ outcome: 'duplicate' });
    const events = await allowed(run);
    expect(events.map((event) => event.data?.['site'])).to.deep.equal([read.site, fetched.site]);
    expect(AUDIT_EVENT_FIELDS['egress.allowed']!(events[0]!.data!)).to.deep.equal({
      host: 'docs.example.com',
      via: 'intercepted',
      sitePath: '/api/v2',
      siteAccess: 'read',
      siteUse: 'read',
    });
    const push = { gateway: 'egress', reason: 'site_read_only', status: 403, host: 'docs.example.com', site: { path: '/api/v2', access: 'read', use: 'write' } };
    expect(await (await report(run.runId, token, push)).json()).to.deep.equal({ outcome: 'recorded' });
    expect(await (await report(run.runId, token, { ...push, site: { ...push.site, path: '/other/repo' } })).json()).to.deep.equal({ outcome: 'recorded' });
    for (const body of [
      { ...read, site: { ...read.site, path: '/x?token=1' } },
      { ...read, site: { ...read.site, path: 'no-slash' } },
      { ...read, site: { ...read.site, access: 'write' } },
      { ...read, site: { ...read.site, use: 'fetch' } },
      { ...read, site: { ...read.site, extra: 1 } },
    ]) {
      expect((await allow(run.runId, token, body)).status, JSON.stringify(body)).to.equal(400);
    }
    expect((await report(run.runId, token, { ...push, host: undefined })).status).to.equal(400);
  });

  it('GR-7: the run’s effective policy lists what its gateways refused', async () => {
    const { run } = await claimed();
    expect((await h.service.effectivePolicy(run.orgId, run.runId))?.blockedRequests).to.equal(undefined);
    const token = await runToken(run);
    await report(run.runId, token, EGRESS);
    await report(run.runId, token, MODEL);
    const blocked = (await h.service.effectivePolicy(run.orgId, run.runId))?.blockedRequests ?? [];
    expect(blocked.map((entry) => without(entry, 'ts'))).to.deep.equal([EGRESS, MODEL]);
  });

  const fallback = (runId: string, bearer: string | null, body: Record<string, unknown>) =>
    fetch(`${base}/internal/runs/${runId}/model-fallback`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(bearer === null ? {} : { authorization: `Bearer ${bearer}` }),
      },
      body: JSON.stringify(body),
    });
  const MOVED = {
    from: { provider: 'anthropic', model: 'claude-x' },
    to: { provider: 'openai', model: 'gpt-x' },
    reason: '5xx',
  };

  it("WP100-FB-N5 (G-48): the model gateway's move to an alternate is journaled as the run's model.fallback, once", async () => {
    const { run } = await claimed();
    const token = await runToken(run);
    expect(await (await fallback(run.runId, token, MOVED)).json()).to.deep.equal({ outcome: 'recorded' });
    expect(await (await fallback(run.runId, token, MOVED)).json()).to.deep.equal({ outcome: 'duplicate' });
    const moved = (await journal.readRun(run.orgId, run.runId, 0, 500)).filter((e) => e.type === 'model.fallback');
    expect(moved).to.have.length(1);
    expect(moved[0]).to.include({ writer: 'node', critical: false, visibility: 'owner', runId: run.runId });
    expect(moved[0]!.data).to.deep.equal(MOVED);
    // Node may write it now (the gateway has no journal of its own); the contract's writer allowlist says so.
    expect(() =>
      validateJournalAppend({
        orgId: run.orgId,
        sessionId: run.sessionId,
        streamId: moved[0]!.streamId,
        runId: run.runId,
        writer: 'node',
        events: [{ type: 'model.fallback', schemaVersion: 1, critical: false, visibility: 'owner', data: MOVED }],
      }),
    ).to.not.throw();
  });

  it('WP100-FB-N6 (G-48): a malformed or foreign report is refused; past the cap nothing is appended', async () => {
    const { run } = await claimed();
    const token = await runToken(run);
    expect((await fallback(run.runId, null, MOVED)).status).to.equal(401);
    expect((await fallback(run.runId, token, { ...MOVED, reason: 'budget' })).status).to.equal(400);
    expect((await fallback(run.runId, token, { ...MOVED, to: { provider: 'openai', model: 'a b' } })).status).to.equal(400);
    for (let i = 0; i < MAX_MODEL_FALLBACKS_PER_RUN; i += 1) {
      await fallback(run.runId, token, { ...MOVED, to: { provider: 'openai', model: `m-${String(i)}` } });
    }
    expect(await (await fallback(run.runId, token, MOVED)).json()).to.deep.equal({ outcome: 'capped' });
  });

  const hidden = (runId: string, bearer: string | null, body: Record<string, unknown>) =>
    fetch(`${base}/internal/runs/${runId}/secret-hidden`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(bearer === null ? {} : { authorization: `Bearer ${bearer}` }),
      },
      body: JSON.stringify(body),
    });
  const PAT = { gateway: 'egress', kind: 'pipeshub_pat' };

  it('WP103-EGFP-N1: a credential shape a gateway hid from a model call is an audit record once per gateway and kind', async () => {
    const { run } = await claimed();
    const token = await runToken(run);
    expect(await (await hidden(run.runId, token, PAT)).json()).to.deep.equal({ outcome: 'recorded' });
    expect(await (await hidden(run.runId, token, PAT)).json()).to.deep.equal({ outcome: 'duplicate' });
    expect(await (await hidden(run.runId, token, { ...PAT, gateway: 'model' })).json()).to.deep.equal({
      outcome: 'recorded',
    });
    const events = (await journal.readRun(run.orgId, run.runId, 0, 500)).filter(
      (e) => e.type === 'gateway.secret_hidden',
    );
    expect(events).to.have.length(2);
    expect(events[0]).to.include({ writer: 'node', critical: false, visibility: 'audit', attemptId: run.attemptId });
    expect(events[0]!.data).to.deep.equal({ ...PAT, attemptId: run.attemptId });
    expect(AUDIT_EVENT_FIELDS['gateway.secret_hidden']!(events[0]!.data!)).to.deep.equal(PAT);
  });

  it('WP103-EGFP-N2: a foreign token, a value or an unknown gateway is refused; past the cap nothing is appended', async () => {
    const { run } = await claimed();
    const token = await runToken(run);
    const { run: other } = await claimed();
    expect((await hidden(other.runId, token, PAT)).status).to.equal(401);
    expect((await hidden(run.runId, null, PAT)).status).to.equal(401);
    expect((await hidden(run.runId, credential, PAT)).status).to.equal(401);
    for (const body of [
      { ...PAT, value: 'phpat_x' },
      { ...PAT, gateway: 'tool' },
      { ...PAT, kind: 'Not A Kind' },
    ]) {
      expect((await hidden(run.runId, token, body)).status, JSON.stringify(body)).to.equal(400);
    }
    for (let i = 0; i < MAX_SECRETS_HIDDEN_PER_RUN; i += 1) {
      await hidden(run.runId, token, { ...PAT, kind: `kind_${String(i)}` });
    }
    expect(await (await hidden(run.runId, token, PAT)).json()).to.deep.equal({ outcome: 'capped' });
  });
});
