/**
 * CPD6-40: `GET /api/v1/agent-harness/internal/control-plane/orgs/{orgId}/live`
 * over HTTP with the real internal router, the real worker-credential guard
 * and the real live-layer service; the head and the stored layers in memory.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import jwt from 'jsonwebtoken';
import { Container } from 'inversify';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { InMemoryConfigDocumentStore } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import { LiveLayerService } from '../../../src/modules/agent-harness/application/live-layer-service';
import { AGENT_HARNESS_LIVE_LAYERS } from '../../../src/modules/agent-harness/binders/change-notifier';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_EXECUTION_SERVICE,
  createHarnessAuthority,
} from '../../../src/modules/agent-harness/composition';
import type { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import {
  AGENT_HARNESS_INTERNAL_PREFIX,
  createAgentHarnessInternalRouter,
} from '../../../src/modules/agent-harness/transport/internal-routes';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { liveLayerSchema, type PolicyLayer } from '../../../src/modules/control-plane';
import { InMemoryHeadRevisions } from '../control-plane/support/head-revisions-contract';
import { aLayer } from '../control-plane/support/resolution-fixtures';
import { contractClock, createContractHarness } from './support/execution-store-contract';

const ORG = 'org-live-1';

describe('CPD6-40 the internal live route', function () {
  this.timeout(20_000);
  let server: Server;
  let base: string;
  let credential: string;
  let heads: InMemoryHeadRevisions;
  let stored: Map<string, PolicyLayer>;
  let killSwitch: boolean;
  let layersDown: boolean;
  let lines: Array<[string, Record<string, unknown> | undefined]>;

  beforeEach(async () => {
    const clock = contractClock();
    const store = new InMemoryExecutionStore(clock);
    const h = createContractHarness(
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
    const stores = {
      workerCredential: new InMemoryConfigDocumentStore<never>(),
      attemptSigningKeys: new InMemoryConfigDocumentStore<never>(),
      attemptPublicKeys: new InMemoryConfigDocumentStore<never>(),
    };
    const authority = createHarnessAuthority(stores as never, h.service, { now: () => Date.UTC(2026, 9, 11) });
    await authority.provision(null);
    credential = ((await stores.workerCredential.read()).value as unknown as { current: string }).current;

    heads = new InMemoryHeadRevisions();
    stored = new Map();
    killSwitch = false;
    layersDown = false;
    lines = [];
    const logger: HarnessLogger = {
      debug: () => undefined,
      info: () => undefined,
      warn: (message, meta) => void lines.push([message, meta]),
      error: () => undefined,
    };
    const container = new Container();
    container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue(h.service);
    container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue(authority);
    container.bind('Logger').toConstantValue(logger);
    container.bind(AGENT_HARNESS_LIVE_LAYERS).toConstantValue(
      new LiveLayerService({
        heads,
        layers: async () => {
          if (layersDown) throw new Error('the policy store is unreachable: secret-connection-string');
          return stored;
        },
        killSwitch: async () => killSwitch,
      }),
    );
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
    await new Promise((resolve) => server.close(resolve));
  });

  const read = async (path: string, bearer: string | null = credential) => {
    const response = await fetch(`${base}${path}`, {
      headers: bearer === null ? {} : { authorization: `Bearer ${bearer}` },
    });
    const text = await response.text();
    return {
      status: response.status,
      cache: response.headers.get('cache-control'),
      text,
      body: text === '' ? null : (JSON.parse(text) as Record<string, unknown>),
    };
  };

  it("answers the org's live layer at its head to the worker credential, never cached", async () => {
    heads.revisions.set(ORG, 41);
    stored.set(`org:${ORG}`, aLayer('org', { approvals: { writes: 'deny' }, approvers: { users: ['u-approver'] } }, ORG));
    stored.set('agent:a7', aLayer('agent', { approvals: { classes: { execute: 'ask' } } }, 'a7'));
    killSwitch = true;

    const reply = await read(`/control-plane/orgs/${ORG}/live`);

    expect(reply.status).to.equal(200);
    expect(reply.cache).to.equal('no-store');
    expect(liveLayerSchema.parse(reply.body)).to.deep.equal({
      schemaVersion: 1,
      orgId: ORG,
      revision: 41,
      layers: [
        { layer: { schemaVersion: 1, scope: { kind: 'live' }, rollout: { AGENT_HARNESS_KILL_SWITCH: true } } },
        { agentKey: 'a7', layer: { schemaVersion: 1, scope: { kind: 'live' }, approvals: { classes: { execute: 'ask' } } } },
        { layer: { schemaVersion: 1, scope: { kind: 'live' }, approvals: { writes: 'deny' } } },
      ],
      revokedBindings: [],
    });
    expect(reply.text).to.not.contain('u-approver');
  });

  it('`since` equal to the head answers 204 with no body and reads no layer; behind the head answers the layer', async () => {
    heads.revisions.set(ORG, 9);
    layersDown = true;
    const unchanged = await read(`/control-plane/orgs/${ORG}/live?since=9`);
    expect([unchanged.status, unchanged.text]).to.deep.equal([204, '']);

    layersDown = false;
    const behind = await read(`/control-plane/orgs/${ORG}/live?since=8`);
    expect([behind.status, behind.body?.['revision']]).to.deep.equal([200, 9]);
  });

  it('an org that never wrote is at revision 0, and `since=0` is unchanged', async () => {
    const first = await read(`/control-plane/orgs/${ORG}/live`);
    expect([first.status, first.body?.['revision']]).to.deep.equal([200, 0]);
    expect((await read(`/control-plane/orgs/${ORG}/live?since=0`)).status).to.equal(204);
  });

  for (const [what, bearer] of [
    ['no credential', null],
    ['a wrong credential', 'not-the-worker-credential'],
    ['a user JWT', jwt.sign({ userId: 'u1', orgId: ORG }, 'scoped-secret-held-by-every-service-0123456789')],
  ] as const) {
    it(`refuses ${what} with 401 and reads nothing`, async () => {
      let reads = 0;
      const head = heads.head.bind(heads);
      heads.head = async (orgId) => {
        reads += 1;
        return head(orgId);
      };
      const reply = await read(`/control-plane/orgs/${ORG}/live`, bearer);
      expect(reply.status).to.equal(401);
      expect(reads).to.equal(0);
    });
  }

  for (const [what, path] of [
    ['a negative `since`', `/control-plane/orgs/${ORG}/live?since=-1`],
    ['a `since` that is not a revision', `/control-plane/orgs/${ORG}/live?since=latest`],
    ['an unknown query member', `/control-plane/orgs/${ORG}/live?agentKey=a7`],
    ['an org id that is not an id', `/control-plane/orgs/${'x'.repeat(400)}/live`],
  ] as const) {
    it(`refuses ${what} with 400`, async () => {
      expect((await read(path)).status).to.equal(400);
    });
  }

  for (const [what, breakIt] of [
    ['the head', () => void (heads.unreadable = true)],
    ['the stored layers', () => void (layersDown = true)],
  ] as const) {
    it(`${what} unreadable: 503, never an empty layer, logged without the cause's text`, async () => {
      breakIt();
      const reply = await read(`/control-plane/orgs/${ORG}/live`);
      expect(reply.status).to.equal(503);
      expect(reply.text).to.not.contain('secret-connection-string');
      expect(lines).to.deep.equal([
        ['cp.live.unavailable', { event: 'cp.live.unavailable', orgId: ORG, errorType: 'Error' }],
      ]);
    });
  }

  it('LiveLayerService without stored layers or Labs settings answers the switch off and nothing else', async () => {
    const service = new LiveLayerService({ heads });
    heads.revisions.set(ORG, 3);
    expect(await service.read(ORG)).to.deep.equal({
      schemaVersion: 1,
      orgId: ORG,
      revision: 3,
      layers: [{ layer: { schemaVersion: 1, scope: { kind: 'live' }, rollout: { AGENT_HARNESS_KILL_SWITCH: false } } }],
      revokedBindings: [],
    });
    expect(await service.read(ORG, 3)).to.equal(null);
  });
});
