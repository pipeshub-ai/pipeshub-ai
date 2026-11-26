/**
 * DP-1 over HTTP: the real internal router, content controller, attempt-token
 * authority, ExecutionService and in-memory store (CS-1..CS-6 negatives).
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
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { RunContentService } from '../../../src/modules/agent-harness/application/run-content-service';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_RUN_CONTENT,
  createHarnessAuthority,
} from '../../../src/modules/agent-harness/composition';
import {
  ATTEMPT_TOKEN_SLACK_MS,
  AttemptKeyring,
  PublishedAttemptKeys,
  WorkerCredentialSet,
} from '../../../src/modules/agent-harness/domain/authority';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { CONTENT_MAX_BYTES } from '../../../src/modules/agent-harness/domain/run-content';
import { createAgentHarnessInternalRouter } from '../../../src/modules/agent-harness/transport/internal-routes';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import {
  contractClock,
  ContractHarness,
  createContractHarness,
} from './support/execution-store-contract';
import { dataKeyRing } from './support/data-protection';

const LEASE_MS = 2_000;
const SENTINEL = 'SENTINEL-http-content-51d0';

interface Claimed {
  run: HarnessRun;
  token: string;
}

describe('DP-1 sealed content over HTTP', function () {
  this.timeout(20_000);
  let server: Server;
  let base: string;
  let h: ContractHarness;
  let authClock: { now(): number; advance(ms: number): void };
  let credential: string;
  let payloads: InMemoryPayloadStore;
  let lines: unknown[][];

  beforeEach(async () => {
    const storeClock = contractClock();
    const store = new InMemoryExecutionStore(storeClock);
    payloads = new InMemoryPayloadStore();
    h = createContractHarness(
      {
        executionStore: store,
        journal: store.journal,
        payloads,
        admissionStore: store,
        dispatches: async () => [],
        deletePayload: async () => undefined,
      },
      storeClock,
    );
    let now = Date.UTC(2026, 8, 27);
    authClock = {
      now: () => now,
      advance: (ms) => {
        now += ms;
      },
    };
    const stores = {
      workerCredential: new InMemoryConfigDocumentStore<WorkerCredentialSet>(),
      attemptSigningKeys: new InMemoryConfigDocumentStore<AttemptKeyring>(),
      attemptPublicKeys:
        new InMemoryConfigDocumentStore<PublishedAttemptKeys>(),
    };
    const authority = createHarnessAuthority(stores, h.service, authClock);
    await authority.provision(null);
    credential = (await stores.workerCredential.read()).value!.current;
    lines = [];
    const at =
      (level: string) =>
      (message: string, meta: Record<string, unknown> = {}) => {
        lines.push([level, message, meta]);
      };
    const logger: HarnessLogger = {
      debug: at('debug'),
      info: at('info'),
      warn: at('warn'),
      error: at('error'),
    };

    const container = new Container();
    container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue(h.service);
    container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue(authority);
    container.bind('Logger').toConstantValue(logger);
    container
      .bind(AGENT_HARNESS_RUN_CONTENT)
      .toConstantValue(
        new RunContentService(
          h.service,
          dataKeyRing().cipher,
          payloads,
          logger,
        ),
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

  const claim = async (
    overrides: Parameters<ContractHarness['admit']>[0] = {},
    workerId = 'worker-a',
  ): Promise<Claimed> => {
    const admitted = await h.admit(overrides);
    const response = await fetch(`${base}/runs/${admitted.run.runId}/claim`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${credential}`,
      },
      body: JSON.stringify({
        orgId: admitted.run.orgId,
        workerId,
        leaseDurationMs: LEASE_MS,
      }),
    });
    const body = (await response.json()) as Record<string, any>;
    expect(response.status, JSON.stringify(body)).to.equal(200);
    return { run: body['run'], token: body['attemptToken']['token'] };
  };

  const query = (run: HarnessRun, extra = ''): string =>
    `attemptId=${String(run.attemptId)}&fence=${String(run.fence)}${extra}`;

  const put = (
    c: Claimed,
    bytes: Buffer,
    options: { bearer?: string | null; runId?: string; type?: string } = {},
  ) => {
    const headers: Record<string, string> = {
      'content-type': options.type ?? 'application/octet-stream',
    };
    const bearer = options.bearer === undefined ? c.token : options.bearer;
    if (bearer !== null) headers['authorization'] = `Bearer ${bearer}`;
    return fetch(
      `${base}/runs/${options.runId ?? c.run.runId}/content?${query(c.run)}`,
      { method: 'POST', headers, body: bytes },
    );
  };

  const get = (c: Claimed, ref: string, bearer: string | null = c.token) =>
    fetch(
      `${base}/runs/${c.run.runId}/content?${query(c.run, `&ref=${encodeURIComponent(ref)}`)}`,
      bearer === null ? {} : { headers: { authorization: `Bearer ${bearer}` } },
    );

  const code = async (response: Response): Promise<string> =>
    ((await response.json()) as { error: { code: string } }).error.code;

  const stored = async (c: Claimed, text = SENTINEL): Promise<string> => {
    const response = await put(c, Buffer.from(text));
    const body = (await response.json()) as { ref: string; size: number };
    expect(response.status, JSON.stringify(body)).to.equal(200);
    expect(body.size).to.equal(Buffer.byteLength(text));
    return body.ref;
  };

  it('CS-1: writes and reads sealed content with the attempt token', async () => {
    const c = await claim();
    const ref = await stored(c);
    expect(ref).to.match(new RegExp(`^sealed:${c.run.runId}/`));
    expect(
      [...payloads.objects.values()].some((bytes) =>
        bytes.toString('latin1').includes(SENTINEL),
      ),
    ).to.equal(false);
    const response = await get(c, ref);
    expect(response.status).to.equal(200);
    expect(response.headers.get('content-type')).to.contain(
      'application/octet-stream',
    );
    expect(Buffer.from(await response.arrayBuffer()).toString()).to.equal(
      SENTINEL,
    );
  });

  it('CS-2: another session and another org read nothing (409 missing, no existence leak)', async () => {
    const writer = await claim();
    const ref = await stored(writer);
    const otherSession = await claim({}, 'worker-b');
    const cross = await get(otherSession, ref);
    expect(cross.status).to.equal(409);
    expect(await code(cross)).to.equal(
      'HTTP_AGENT_HARNESS_JOURNAL_PAYLOAD_MISSING',
    );
    const otherOrg = await claim({ ...h.newTenant() }, 'worker-c');
    expect((await get(otherOrg, ref)).status).to.equal(409);
  });

  it('CS-3: no token, the worker credential, a token for another run, an expired and a superseded token are refused', async () => {
    const c = await claim();
    const ref = await stored(c);
    expect((await put(c, Buffer.from('x'), { bearer: null })).status).to.equal(
      401,
    );
    expect((await get(c, ref, null)).status).to.equal(401);
    expect(
      (await put(c, Buffer.from('x'), { bearer: credential })).status,
    ).to.equal(401);
    expect((await get(c, ref, credential)).status).to.equal(401);

    const other = await claim({}, 'worker-b');
    const wrongRun = await put(other, Buffer.from('x'), { bearer: c.token });
    expect(wrongRun.status).to.equal(401);
    expect(await code(wrongRun)).to.equal('HTTP_AGENT_HARNESS_ATTEMPT_CLAIMS');

    h.setNow((c.run.leaseExpiresAt ?? 0) + 1);
    const reclaimed = await fetch(`${base}/runs/${c.run.runId}/claim`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${credential}`,
      },
      body: JSON.stringify({
        orgId: c.run.orgId,
        workerId: 'worker-z',
        leaseDurationMs: LEASE_MS,
      }),
    });
    expect(reclaimed.status).to.equal(200);
    const superseded = await get(c, ref);
    expect(superseded.status).to.equal(401);
    expect(await code(superseded)).to.equal(
      'HTTP_AGENT_HARNESS_ATTEMPT_SUPERSEDED',
    );
    expect((await put(c, Buffer.from('x'))).status).to.equal(401);

    authClock.advance(LEASE_MS + ATTEMPT_TOKEN_SLACK_MS);
    const expired = await get(other, ref);
    expect(expired.status).to.equal(401);
    expect(await code(expired)).to.equal('HTTP_AGENT_HARNESS_ATTEMPT_EXPIRED');
    expect(
      lines.filter(([, message]) => message === 'authority.refused').length,
    ).to.be.at.least(6);
  });

  it('CS-6: a body over the limit is refused (413) and nothing is stored; so is another media type (415)', async () => {
    const c = await claim();
    const huge = await put(c, Buffer.alloc(CONTENT_MAX_BYTES + 1));
    expect(huge.status).to.equal(413);
    expect(await code(huge)).to.equal('HTTP_AGENT_HARNESS_CONTENT_TOO_LARGE');
    const json = await put(c, Buffer.from('{"a":1}'), {
      type: 'application/json',
    });
    expect(json.status).to.equal(415);
    expect(payloads.objects.size).to.equal(0);
    expect(
      lines
        .filter(([, message]) => message === 'content.refused')
        .map(([, , meta]) => (meta as { reason: string }).reason),
    ).to.deep.equal(['too_large', 'unsupported_media']);
  });

  it('refuses a read without a ref (400)', async () => {
    const c = await claim();
    const response = await fetch(
      `${base}/runs/${c.run.runId}/content?${query(c.run)}`,
      { headers: { authorization: `Bearer ${c.token}` } },
    );
    expect(response.status).to.equal(400);
  });
});
