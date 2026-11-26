/**
 * Journal authority end to end over HTTP (A-1..A-10): the real internal
 * router, controller, ExecutionService and in-memory store, with the
 * authority's documents in memory.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { CURRENT_WORKER_CAPABILITIES } from './support/worker-capabilities';
import { advertisedNodeCapabilities } from '../../../src/modules/agent-harness/domain/node-capabilities';
import { nodeCapabilitiesSchema } from '../../../src/modules/agent-harness/contracts/generated/core/node-capabilities';
import express from 'express';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import jwt from 'jsonwebtoken';
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
  ATTEMPT_TOKEN_SLACK_MS,
  AttemptKeyring,
  PublishedAttemptKeys,
  WorkerCredentialSet,
} from '../../../src/modules/agent-harness/domain/authority';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import {
  AGENT_HARNESS_INTERNAL_PREFIX,
  createAgentHarnessInternalRouter,
} from '../../../src/modules/agent-harness/transport/internal-routes';
import { xssSanitizationMiddleware } from '../../../src/libs/middlewares/xss-sanitization.middleware';
import {
  contractClock,
  ContractHarness,
  createContractHarness,
} from './support/execution-store-contract';
import { HARNESS_ENABLED } from './support/rollout';

const SCOPED_SECRET = 'scoped-secret-held-by-every-service-0123456789';
const LEASE_MS = 2_000;

interface Reply {
  status: number;
  body: Record<string, any>;
}

describe('agent harness journal authority over HTTP', function () {
  this.timeout(20_000);
  let server: Server;
  let base: string;
  let h: ContractHarness;
  let authClock: { now(): number; advance(ms: number): void };
  let stores: {
    workerCredential: InMemoryConfigDocumentStore<WorkerCredentialSet>;
    attemptSigningKeys: InMemoryConfigDocumentStore<AttemptKeyring>;
    attemptPublicKeys: InMemoryConfigDocumentStore<PublishedAttemptKeys>;
  };
  let authority: ReturnType<typeof createHarnessAuthority>;
  let credential: string;

  beforeEach(async () => {
    const storeClock = contractClock();
    const store = new InMemoryExecutionStore(storeClock);
    h = createContractHarness(
      {
        executionStore: store,
        journal: store.journal,
        payloads: new InMemoryPayloadStore(),
        admissionStore: store,
        dispatches: async () => [],
        deletePayload: async () => undefined,
      },
      storeClock,
    );
    let now = Date.UTC(2026, 8, 26);
    authClock = {
      now: () => now,
      advance: (ms) => {
        now += ms;
      },
    };
    stores = {
      workerCredential: new InMemoryConfigDocumentStore(),
      attemptSigningKeys: new InMemoryConfigDocumentStore(),
      attemptPublicKeys: new InMemoryConfigDocumentStore(),
    };
    authority = createHarnessAuthority(stores, h.service, authClock);
    await authority.provision(null);
    credential = (await stores.workerCredential.read()).value!.current;

    const container = new Container();
    container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue(h.service);
    container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue(authority);
    const app = express();
    // The app's order: body parsing, the global XSS middleware, then the router at its real prefix.
    app.use(express.json());
    app.use(xssSanitizationMiddleware);
    app.use(AGENT_HARNESS_INTERNAL_PREFIX, createAgentHarnessInternalRouter(container));
    app.use(
      (
        error: { statusCode?: number; code?: string; message: string },
        _req: express.Request,
        res: express.Response,
        // eslint-disable-next-line @typescript-eslint/no-unused-vars -- Express treats only a four-argument handler as error middleware
        _next: express.NextFunction,
      ) => {
        res
          .status(error.statusCode ?? 500)
          .json({ error: { code: error.code, message: error.message } });
      },
    );
    server = await new Promise<Server>((resolve) => {
      const listening = app.listen(0, () => resolve(listening));
    });
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}${AGENT_HARNESS_INTERNAL_PREFIX}`;
  });

  afterEach(async () => {
    await new Promise((resolve) => server.close(resolve));
  });

  const call = async (
    method: 'GET' | 'POST',
    path: string,
    bearer: string | null,
    body?: unknown,
  ): Promise<Reply> => {
    const headers: Record<string, string> = {
      'content-type': 'application/json',
    };
    if (bearer !== null) headers['authorization'] = `Bearer ${bearer}`;
    const response = await fetch(`${base}${path}`, {
      method,
      headers,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    return {
      status: response.status,
      body: (await response.json()) as Record<string, any>,
    };
  };

  const claim = (run: HarnessRun, bearer: string | null, workerId = 'worker-a') =>
    call('POST', `/runs/${run.runId}/claim`, bearer, {
      orgId: run.orgId,
      workerId,
      leaseDurationMs: LEASE_MS,
    });

  const renew = (runId: string, token: string, attemptId: string, fence: number) =>
    call('POST', `/runs/${runId}/renew`, token, {
      attemptId,
      fence,
      leaseDurationMs: LEASE_MS,
    });

  const commit = (
    runId: string,
    token: string,
    run: HarnessRun,
    extra: Record<string, unknown> = {},
  ) =>
    call('POST', `/runs/${runId}/commit`, token, {
      commandId: `${String(run.attemptId)}:turn:1`,
      attemptId: run.attemptId,
      fence: run.fence,
      events: [
        {
          type: 'turn.started',
          schemaVersion: 1,
          critical: true,
          visibility: 'owner',
          data: { turn: 1 },
        },
      ],
      legacyEvents: [],
      outbox: [],
      transition: { expectedRevision: run.revision, status: 'running' },
      ...extra,
    });

  const events = (runId: string, token: string, attemptId: string, fence: number) =>
    call(
      'GET',
      `/runs/${runId}/events?attemptId=${attemptId}&fence=${String(fence)}`,
      token,
    );

  const claimed = async (workerId = 'worker-a') => {
    const admitted = await h.admit();
    const reply = await claim(admitted.run, credential, workerId);
    expect(reply.status, JSON.stringify(reply.body)).to.equal(200);
    return {
      run: reply.body['run'] as HarnessRun,
      token: reply.body['attemptToken']['token'] as string,
      expiresAt: reply.body['attemptToken']['expiresAt'] as number,
    };
  };

  const kid = (token: string): string =>
    (jwt.decode(token, { complete: true })?.header as { kid: string }).kid;

  it('B-1: model and tool output with HTML reaches the journal verbatim behind the app middleware', async () => {
    const { run, token } = await claimed();
    const html = '<p>Totals</p><table><tr><td>42</td></tr></table><script>alert(1)</script>';
    const reply = await call('POST', `/runs/${run.runId}/commit`, token, {
      commandId: `${String(run.attemptId)}:html`,
      attemptId: run.attemptId,
      fence: run.fence,
      events: [
        {
          type: 'tool.outcome',
          schemaVersion: 1,
          critical: true,
          visibility: 'owner',
          data: {
            invocationId: 'inv-1',
            status: 'ok',
            receipt: { body: html },
            labels: { integrity: 'external' },
          },
        },
      ],
      legacyEvents: [],
      outbox: [],
      transition: { expectedRevision: run.revision, status: 'running' },
    });
    expect(reply.status, JSON.stringify(reply.body)).to.equal(200);
    const read = await events(run.runId, token, String(run.attemptId), run.fence);
    const outcome = (read.body['events'] as Array<{ type: string; data: { receipt: { body: string } } }>).find(
      (e) => e.type === 'tool.outcome',
    );
    expect(outcome?.data.receipt.body).to.equal(html);
  });

  it('claims with the worker credential and acts with the attempt token', async () => {
    const { run, token, expiresAt } = await claimed();
    expect(run.status).to.equal('running');
    expect(expiresAt).to.equal(authClock.now() + LEASE_MS + ATTEMPT_TOKEN_SLACK_MS);

    const renewed = await renew(run.runId, token, run.attemptId!, run.fence);
    expect(renewed.status).to.equal(200);
    expect(renewed.body['attemptToken']['token']).to.be.a('string');

    const read = await events(run.runId, token, run.attemptId!, run.fence);
    expect(read.status).to.equal(200);

    const committed = await commit(run.runId, token, renewed.body['run']);
    expect(committed.status, JSON.stringify(committed.body)).to.equal(200);
  });

  it('A-1: a scoped JWT any service can mint cannot claim or act for an attempt', async () => {
    const admitted = await h.admit();
    const forged = jwt.sign(
      {
        aud: 'pipeshub-agent-harness',
        tokenType: 'agent-worker',
        orgId: admitted.run.orgId,
        workerId: 'worker-evil',
        scopes: ['agent-run:claim', 'agent-run:renew', 'agent-run:commit'],
      },
      SCOPED_SECRET,
      { expiresIn: '5m' },
    );
    expect((await claim(admitted.run, forged)).status).to.equal(401);
    const { run } = await claimed();
    expect((await renew(run.runId, forged, run.attemptId!, run.fence)).status).to.equal(401);
    expect((await commit(run.runId, forged, run)).status).to.equal(401);
    expect((await events(run.runId, forged, run.attemptId!, run.fence)).status).to.equal(401);
    expect((await h.service.get(admitted.run.orgId, admitted.run.runId))?.status).to.equal('queued');
  });

  it('A-5: refuses a claim without, or with a wrong, credential, and with an attempt token', async () => {
    const admitted = await h.admit();
    expect((await claim(admitted.run, null)).status).to.equal(401);
    expect((await claim(admitted.run, 'phw_wrong')).status).to.equal(401);
    const { token } = await claimed();
    expect((await claim(admitted.run, token)).status).to.equal(401);
    expect((await h.service.get(admitted.run.orgId, admitted.run.runId))?.status).to.equal('queued');
  });

  it('A-8: the worker credential alone is refused on every post-claim route', async () => {
    const { run } = await claimed();
    expect((await renew(run.runId, credential, run.attemptId!, run.fence)).status).to.equal(401);
    expect((await commit(run.runId, credential, run)).status).to.equal(401);
    expect((await events(run.runId, credential, run.attemptId!, run.fence)).status).to.equal(401);
  });

  it('A-2: a token reused for another run or attempt is refused', async () => {
    const first = await claimed('worker-a');
    const second = await claimed('worker-b');
    expect(
      (await commit(second.run.runId, first.token, second.run)).status,
    ).to.equal(401);
    expect(
      (await renew(first.run.runId, first.token, second.run.attemptId!, first.run.fence)).status,
    ).to.equal(401);
    expect(
      (await events(second.run.runId, first.token, second.run.attemptId!, second.run.fence)).status,
    ).to.equal(401);
  });

  it('A-9: org and worker authority come from the token, never the body', async () => {
    const { run, token } = await claimed();
    const asserted = await commit(run.runId, token, run, {
      orgId: 'attacker-org',
      workerId: 'attacker-worker',
    });
    expect(asserted.status).to.equal(400);
    const reply = await commit(run.runId, token, run);
    expect(reply.status, JSON.stringify(reply.body)).to.equal(200);
    expect(reply.body['receipt']['run']).to.include({
      orgId: run.orgId,
      workerId: 'worker-a',
    });
  });

  it('A-3: after a re-claim the old attempt token is refused (401), even naming the new fence', async () => {
    const { run, token } = await claimed();
    h.setNow((run.leaseExpiresAt ?? 0) + 1);
    const reclaim = await claim(run, credential, 'worker-b');
    expect(reclaim.status).to.equal(200);
    const current = reclaim.body['run'] as HarnessRun;
    expect(current.fence).to.equal(run.fence + 1);

    const stale = await commit(run.runId, token, run);
    expect(stale.status).to.equal(401);
    expect(stale.body['error']['code']).to.equal('HTTP_AGENT_HARNESS_ATTEMPT_SUPERSEDED');
    expect((await commit(run.runId, token, current)).status).to.equal(401);
    expect((await renew(run.runId, token, run.attemptId!, run.fence)).status).to.equal(401);

    const fresh = reclaim.body['attemptToken']['token'] as string;
    expect((await commit(run.runId, fresh, current)).status).to.equal(200);
  });

  it('A-4: after key rotation old tokens verify until expiry and new tokens use the new key', async () => {
    const { run, token } = await claimed();
    await authority.rotateKeys();
    const renewed = await renew(run.runId, token, run.attemptId!, run.fence);
    expect(renewed.status).to.equal(200);
    const next = renewed.body['attemptToken']['token'] as string;
    expect(kid(next)).to.not.equal(kid(token));
    expect((await events(run.runId, token, run.attemptId!, run.fence)).status).to.equal(200);
    authClock.advance(LEASE_MS + ATTEMPT_TOKEN_SLACK_MS);
    expect((await events(run.runId, token, run.attemptId!, run.fence)).status).to.equal(401);
  });

  it('A-6: a rotated worker credential claims; the previous one claims only in its grace window', async () => {
    const admitted = await h.admit();
    await authority.rotateWorkerCredential();
    const next = (await stores.workerCredential.read()).value!.current;
    expect(next).to.not.equal(credential);
    expect((await claim(admitted.run, next)).status).to.equal(200);
    const other = await h.admit();
    expect((await claim(other.run, credential)).status).to.equal(200);
  });

  it('A-10: an expired token is refused; a valid token cannot outlive the fenced lease', async () => {
    const first = await claimed();
    authClock.advance(LEASE_MS + ATTEMPT_TOKEN_SLACK_MS);
    const expired = await commit(first.run.runId, first.token, first.run);
    expect(expired.status).to.equal(401);
    expect(expired.body['error']['code']).to.equal('HTTP_AGENT_HARNESS_ATTEMPT_EXPIRED');

    const second = await claimed('worker-b');
    h.setNow((second.run.leaseExpiresAt ?? 0) + 1);
    const lapsed = await commit(second.run.runId, second.token, second.run);
    expect(lapsed.status).to.equal(409);
  });

  it('D-11: a claim whose attempt token cannot be issued is released at once', async () => {
    const admitted = await h.admit();
    const issueFor = authority.issueFor.bind(authority);
    let failures = 1;
    authority.issueFor = (run, lease) => {
      if (failures > 0) {
        failures -= 1;
        return Promise.reject(new Error('keyring unavailable'));
      }
      return issueFor(run, lease);
    };
    const refused = await claim(admitted.run, credential);
    expect(refused.status).to.equal(503);
    const current = await h.service.get(admitted.run.orgId, admitted.run.runId);
    expect([current?.status, current?.attemptId, current?.fence]).to.deep.equal([
      'queued',
      null,
      1,
    ]);
    // Claimable again straight away, not only after the lease expires.
    const again = await claim(admitted.run, credential, 'worker-b');
    expect(again.status, JSON.stringify(again.body)).to.equal(200);
    expect(again.body['run']['fence']).to.equal(2);
  });

  it('O-5a: claim-next takes the worker credential and answers 204 when the queue is empty', async () => {
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
    const next = (bearer: string | null, advertised = true) =>
      call('POST', '/runs/claim', bearer, {
        workerId: 'worker-a',
        leaseDurationMs: LEASE_MS,
        role: 'dispatched',
        ...(advertised ? { workerCapabilities: CURRENT_WORKER_CAPABILITIES } : {}),
      });
    const response = await fetch(`${base}/runs/claim`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${credential}`,
      },
      body: JSON.stringify({
        workerId: 'worker-a',
        leaseDurationMs: LEASE_MS,
        role: 'dispatched',
      }),
    });
    expect(response.status).to.equal(204);
    const admitted = await h.admit();
    expect((await next(null)).status).to.equal(401);
    // RU-2: a worker that advertises nothing is older than the rule and gets no v2 run.
    const unadvertised = await fetch(`${base}/runs/claim`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${credential}`,
      },
      body: JSON.stringify({ workerId: 'worker-old', leaseDurationMs: LEASE_MS, role: 'dispatched' }),
    });
    expect(unadvertised.status).to.equal(204);
    const hint = await call('POST', `/runs/${admitted.run.runId}/claim`, credential, {
      orgId: admitted.run.orgId,
      workerId: 'worker-old',
      leaseDurationMs: LEASE_MS,
    });
    expect(hint.status).to.equal(409);
    expect(hint.body['error']['code']).to.equal('HTTP_AGENT_HARNESS_WORKER_INCOMPATIBLE');
    const claimedNext = await next(credential);
    expect(claimedNext.status, JSON.stringify(claimedNext.body)).to.equal(200);
    expect(claimedNext.body['run']['runId']).to.equal(admitted.run.runId);
    expect(claimedNext.body['pinned']['specDigest']).to.match(/^sha256:/);
    expect(claimedNext.body['attemptToken']['token']).to.be.a('string');
    // UX2 VS-1: what this Node accepts travels with the claim, so a newer worker writes nothing it would refuse.
    // A Node with no edition names its own features only.
    expect(claimedNext.body['nodeCapabilities']).to.deep.equal(advertisedNodeCapabilities());
    expect(nodeCapabilitiesSchema.safeParse(claimedNext.body['nodeCapabilities']).success).to.equal(true);
  });

  it('UX2 VS-1: a claim by id carries what this Node accepts', async () => {
    const admitted = await h.admit();
    const reply = await claim(admitted.run, credential, 'worker-a');
    expect(reply.status, JSON.stringify(reply.body)).to.equal(200);
    expect(reply.body['nodeCapabilities']).to.deep.equal({
      harnessMajor: 2,
      features: ['approval_reasons', 'approval_rule_layers', 'child_labels', 'child_grants', 'command_activity', 'followups', 'task_asks_first', 'intent_allowed_by', 'context_compacted', 'approval_rule_grants', 'context_compacted_auto', 'skill_labels', 'native_events', 'approval_edits', 'external_policy_asks', 'knowledge_exports', 'priority_preemption', 'repository_fetches', 'connect_pauses', 'site_upload_asks', 'approval_edit_order', 'approval_args_digest', 'outcome_ledger', 'provenance_records', 'outcome_words', 'child_tasks', 'child_mcp_pins', 'attachment_limits', 'repository_not_cloned', 'skipped_inputs', 'attachment_too_large', 'attachment_count'],
    });
    // G-45: the run's class rides beside the run, which the worker reads strictly.
    expect(reply.body['priorityClass']).to.equal('interactive');
    expect(reply.body['run']).not.to.have.property('priorityClass');
  });

  it('UX5 EV-3: both claims advertise the activity record and the follow-up suggestions', async () => {
    const byId = await claim((await h.admit()).run, credential, 'worker-a');
    expect(byId.status, JSON.stringify(byId.body)).to.equal(200);
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
    await h.admit();
    const next = await call('POST', '/runs/claim', credential, {
      workerId: 'worker-a',
      leaseDurationMs: LEASE_MS,
      role: 'dispatched',
      workerCapabilities: CURRENT_WORKER_CAPABILITIES,
    });
    expect(next.status, JSON.stringify(next.body)).to.equal(200);
    for (const reply of [byId, next]) {
      expect(reply.body['nodeCapabilities']['features']).to.include.members(['command_activity', 'followups']);
    }
  });

  it('releases with the attempt token only, then fences the released attempt out', async () => {
    const { run, token } = await claimed();
    const release = (bearer: string) =>
      call('POST', `/runs/${run.runId}/release`, bearer, {
        attemptId: run.attemptId,
        fence: run.fence,
        reason: 'fold_incompatible',
      });
    expect((await release(credential)).status).to.equal(401);
    const released = await release(token);
    expect(released.status, JSON.stringify(released.body)).to.equal(200);
    expect(released.body['run']['status']).to.equal('queued');
    expect((await release(token)).status).to.equal(409);
  });

  it('P9: the grant auditor reads a run\'s evidence with the worker credential only; another org\'s run is 404', async () => {
    const { run, token } = await claimed();
    const audit = (bearer: string, orgId = run.orgId) =>
      call('GET', `/runs/${run.runId}/audit?orgId=${orgId}`, bearer);
    const evidence = await audit(credential);
    expect(evidence.status, JSON.stringify(evidence.body)).to.equal(200);
    expect(evidence.body).to.include({ orgId: run.orgId, runId: run.runId });
    expect(evidence.body['pinned']).to.be.a('boolean');
    expect(evidence.body['intents']).to.deep.equal([]);
    expect(evidence.body['toolIds']).to.be.an('array');
    // C9.01: what the usage reconciliation reads; a running run with no model call yet.
    expect(evidence.body).to.include({ ended: false });
    expect(evidence.body['usage']).to.deep.equal([]);
    expect(evidence.body).to.have.property('ledger');
    expect((await audit(token)).status).to.equal(401);
    expect((await audit(credential, 'another-org')).status).to.equal(404);
  });
});
