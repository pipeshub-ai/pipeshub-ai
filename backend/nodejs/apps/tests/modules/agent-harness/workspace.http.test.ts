/**
 * P3.2 over HTTP (NW-1, NW-2, NW-7 transport): the real internal router,
 * workspace controller, attempt-token authority, ExecutionService and
 * in-memory stores.
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
import {
  InMemoryWorkspaceKeyStore,
  InMemoryWorkspaceRepository,
  InMemoryWorkspaceWriterStore,
} from '../../../src/modules/agent-harness/adapters/in-memory-workspace-store';
import { SessionWorkspaceKeys } from '../../../src/modules/agent-harness/application/session-workspace-keys';
import { WorkspaceService } from '../../../src/modules/agent-harness/application/workspace-service';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_WORKSPACES,
  createHarnessAuthority,
} from '../../../src/modules/agent-harness/composition';
import {
  AttemptKeyring,
  PublishedAttemptKeys,
  WorkerCredentialSet,
} from '../../../src/modules/agent-harness/domain/authority';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { MAX_WORKSPACE_FILES } from '../../../src/modules/agent-harness/domain/workspace';
import { createAgentHarnessInternalRouter } from '../../../src/modules/agent-harness/transport/internal-routes';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import {
  contractClock,
  ContractHarness,
  createContractHarness,
} from './support/execution-store-contract';
import { dataKeyRing } from './support/data-protection';

interface Claimed {
  run: HarnessRun;
  token: string;
}

const entry = (path: string) => ({
  path,
  objectId: 'obj-1',
  contentHash: 'hmac-sha256:1',
  sizeBytes: 1,
  mimeType: 'text/plain',
});

describe('P3.2 workspace routes over HTTP', function () {
  this.timeout(20_000);
  let server: Server;
  let base: string;
  let h: ContractHarness;
  let credential: string;
  let keys: SessionWorkspaceKeys;

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
    const stores = {
      workerCredential: new InMemoryConfigDocumentStore<WorkerCredentialSet>(),
      attemptSigningKeys: new InMemoryConfigDocumentStore<AttemptKeyring>(),
      attemptPublicKeys:
        new InMemoryConfigDocumentStore<PublishedAttemptKeys>(),
    };
    const now = Date.UTC(2026, 9, 2);
    const authority = createHarnessAuthority(stores, h.service, {
      now: () => now,
    } as never);
    await authority.provision(null);
    credential = (await stores.workerCredential.read()).value!.current;
    const cipher = dataKeyRing().cipher;
    keys = new SessionWorkspaceKeys({ store: new InMemoryWorkspaceKeyStore(), cipher });
    const container = new Container();
    container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue(h.service);
    container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue(authority);
    container
      .bind(AGENT_HARNESS_WORKSPACES)
      .toConstantValue(
        new WorkspaceService({
          runs: h.service,
          repository: new InMemoryWorkspaceRepository(),
          cipher,
          keys,
          writers: new InMemoryWorkspaceWriterStore(),
        }),
      );
    const app = express();
    // The internal listener's own body limit (internal-listener.ts): a full workspace's commit fits it.
    app.use(express.json({ limit: '10mb' }));
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

  const claim = async (workerId = 'worker-a'): Promise<Claimed> => {
    const admitted = await h.admit();
    const response = await fetch(`${base}/runs/${admitted.run.runId}/claim`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${credential}`,
      },
      body: JSON.stringify({
        orgId: admitted.run.orgId,
        workerId,
        leaseDurationMs: 60_000,
      }),
    });
    const body = (await response.json()) as Record<string, any>;
    expect(response.status, JSON.stringify(body)).to.equal(200);
    return { run: body['run'], token: body['attemptToken']['token'] };
  };

  const ws = (c: Claimed, suffix = ''): string =>
    encodeURIComponent(`ws:${c.run.sessionId}${suffix}`);
  const attempt = (c: Claimed) => ({
    attemptId: c.run.attemptId,
    fence: c.run.fence,
  });
  const post = (
    c: Claimed,
    path: string,
    body: Record<string, unknown>,
    bearer: string | null = c.token,
  ) =>
    fetch(`${base}/runs/${c.run.runId}${path}`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(bearer === null ? {} : { authorization: `Bearer ${bearer}` }),
      },
      body: JSON.stringify({ ...attempt(c), ...body }),
    });
  const head = (c: Claimed, bearer: string | null = c.token) =>
    fetch(
      `${base}/runs/${c.run.runId}/workspaces/${ws(c)}?attemptId=${String(c.run.attemptId)}&fence=${String(c.run.fence)}`,
      bearer === null ? {} : { headers: { authorization: `Bearer ${bearer}` } },
    );
  const code = async (response: Response): Promise<string> =>
    ((await response.json()) as { error: { code: string } }).error.code;

  it('commits and reads the session workspace with the attempt token', async () => {
    const c = await claim();
    const committed = await post(c, `/workspaces/${ws(c)}/commit`, {
      baseRevision: 0,
      reason: 'turn_end',
      upserts: [entry('src/app.py')],
      deletes: [],
    });
    const body = (await committed.json()) as {
      revision: number;
      files: unknown[];
    };
    expect(committed.status, JSON.stringify(body)).to.equal(200);
    expect(body.revision).to.equal(1);
    const read = await head(c);
    expect(read.status).to.equal(200);
    expect(((await read.json()) as { files: unknown[] }).files).to.have.length(
      1,
    );
    const revision = await fetch(
      `${base}/runs/${c.run.runId}/workspaces/${ws(c)}/revisions/1?attemptId=${String(c.run.attemptId)}&fence=${String(c.run.fence)}`,
      { headers: { authorization: `Bearer ${c.token}` } },
    );
    expect(revision.status).to.equal(200);
    const stale = await post(c, `/workspaces/${ws(c)}/commit`, {
      baseRevision: 0,
      reason: 'autosave',
      upserts: [entry('b')],
      deletes: [],
    });
    expect(stale.status).to.equal(409);
    expect(await code(stale)).to.equal('HTTP_AGENT_HARNESS_WORKSPACE_CONFLICT');
  });

  it('NW-1: no token, the worker credential and another run’s token are refused', async () => {
    const c = await claim();
    const other = await claim('worker-b');
    expect((await head(c, null)).status).to.equal(401);
    expect((await head(c, credential)).status).to.equal(401);
    expect((await head(c, other.token)).status).to.equal(401);
    expect((await post(c, '/workspace-key', {}, other.token)).status).to.equal(
      401,
    );
  });

  it('NW-2: another session’s workspace is 403; a bad path is 422; a bad body is 400', async () => {
    const c = await claim();
    const other = await claim('worker-b');
    const foreign = await fetch(
      `${base}/runs/${c.run.runId}/workspaces/${ws(other)}?attemptId=${String(c.run.attemptId)}&fence=${String(c.run.fence)}`,
      { headers: { authorization: `Bearer ${c.token}` } },
    );
    expect(foreign.status).to.equal(403);
    expect(await code(foreign)).to.equal(
      'HTTP_AGENT_HARNESS_WORKSPACE_FORBIDDEN',
    );
    const traversal = await post(c, `/workspaces/${ws(c)}/commit`, {
      baseRevision: 0,
      reason: 'autosave',
      upserts: [entry('../etc/passwd')],
      deletes: [],
    });
    expect(traversal.status).to.equal(422);
    expect(await code(traversal)).to.equal(
      'HTTP_AGENT_HARNESS_WORKSPACE_INVALID_CHANGE',
    );
    const nodeReason = await post(c, `/workspaces/${ws(c)}/commit`, {
      baseRevision: 0,
      reason: 'merge',
      upserts: [],
      deletes: [],
    });
    expect(nodeReason.status).to.equal(400);
  });

  it('WP82-NODE-1: a commit listing more files than a workspace holds is WORKSPACE_TOO_LARGE, not a bare 400', async () => {
    const c = await claim();
    const files = Array.from({ length: MAX_WORKSPACE_FILES + 1 }, (_, i) =>
      entry(`repos/onyx/f${String(i)}.py`),
    );
    const refused = await post(c, `/workspaces/${ws(c)}/commit`, {
      baseRevision: 0,
      reason: 'autosave',
      upserts: files,
      deletes: [],
    });
    expect(refused.status).to.equal(413);
    expect(await code(refused)).to.equal(
      'HTTP_AGENT_HARNESS_WORKSPACE_TOO_LARGE',
    );
    const fits = await post(c, `/workspaces/${ws(c)}/commit`, {
      baseRevision: 0,
      reason: 'autosave',
      upserts: files.slice(0, MAX_WORKSPACE_FILES),
      deletes: [],
    });
    expect(fits.status).to.equal(200);
  });

  it('P6.4 NF-1/NF-2: keyed keepTarget merges and the writer lease over HTTP', async () => {
    const c = await claim();
    const commit = (workspace: string, base: number, path: string, hash: string) =>
      post(c, `/workspaces/${workspace}/commit`, {
        baseRevision: base,
        reason: 'autosave',
        upserts: [{ ...entry(path), contentHash: hash }],
        deletes: [],
      });
    expect((await commit(ws(c), 0, 'a', 'hmac-sha256:base')).status).to.equal(200);
    expect(
      (await post(c, `/workspaces/${ws(c)}/fork`, { atRevision: 1, forkId: `ws:${c.run.sessionId}:fork:c` })).status,
    ).to.equal(200);
    await commit(ws(c), 1, 'a', 'hmac-sha256:parent');
    await commit(ws(c, ':fork:c'), 1, 'a', 'hmac-sha256:child');
    const merge = {
      forkId: `ws:${c.run.sessionId}:fork:c`,
      mergeKey: 'child-c',
      onConflict: 'keepTarget',
    };
    const merged = await post(c, `/workspaces/${ws(c)}/merge`, merge);
    const body = (await merged.json()) as Record<string, unknown>;
    expect(merged.status, JSON.stringify(body)).to.equal(200);
    expect(body).to.deep.include({ status: 'partial', merged: [], forkRevision: 2 });
    const replay = await post(c, `/workspaces/${ws(c)}/merge`, merge);
    expect(await replay.json()).to.deep.equal(body);
    const unknownPolicy = await post(c, `/workspaces/${ws(c)}/merge`, {
      ...merge,
      onConflict: 'theirs',
    });
    expect(unknownPolicy.status).to.equal(400);

    const acquired = await post(c, `/workspaces/${ws(c)}/writer`, { ttlMs: 30_000 });
    expect(acquired.status).to.equal(200);
    expect(await acquired.json()).to.deep.include({
      workspaceId: `ws:${c.run.sessionId}`,
      acquired: true,
      holder: c.run.runId,
      renewed: false,
    });
    expect((await post(c, `/workspaces/${ws(c)}/writer`, { ttlMs: 50 })).status).to.equal(400);
    const released = await post(c, `/workspaces/${ws(c)}/writer/release`, {});
    expect(await released.json()).to.deep.equal({
      workspaceId: `ws:${c.run.sessionId}`,
      released: true,
    });
  });

  it('NW-7: the session key is returned once per request, never cached, and gone after a shred', async () => {
    const c = await claim();
    const issued = await post(c, '/workspace-key', {});
    expect(issued.status).to.equal(200);
    expect(issued.headers.get('cache-control')).to.equal('no-store');
    const body = (await issued.json()) as {
      keyId: string;
      keyDomain: string;
      key: string;
    };
    expect(body.keyDomain).to.equal(`ws:${c.run.sessionId}`);
    expect(Buffer.from(body.key, 'base64')).to.have.length(32);
    await keys.shred(c.run.orgId, c.run.sessionId);
    const gone = await post(c, '/workspace-key', {});
    expect(gone.status).to.equal(410);
    expect(await code(gone)).to.equal(
      'HTTP_AGENT_HARNESS_WORKSPACE_KEY_DESTROYED',
    );
  });
});
