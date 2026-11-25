/**
 * MEMSW: a person turns their own memory off and on, from their Memories page, whatever the org's policy. Off, their
 * runs are given none of their memories and keep none (a running run's later proposals are refused and its pending
 * ones discarded at its end); what they already have stays listed for them to delete. A switch that cannot be read
 * is off. The switch is stored with the person's other harness choices, and their erasure removes it.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import express from 'express';
import { Container } from 'inversify';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import sinon from 'sinon';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { Logger } from '../../../src/libs/services/logger.service';
import { InMemoryCodingEnginePreferences } from '../../../src/modules/agent-harness/adapters/in-memory-coding-engine-preferences';
import { InMemoryMemoryStore } from '../../../src/modules/agent-harness/adapters/in-memory-memory-store';
import { InMemoryRecordReadAccess } from '../../../src/modules/agent-harness/adapters/in-memory-record-access';
import { MemoryService } from '../../../src/modules/agent-harness/application/memory-service';
import { SharedCitationService } from '../../../src/modules/agent-harness/application/shared-citations';
import { AGENT_HARNESS_MEMORIES } from '../../../src/modules/agent-harness/composition';
import { HarnessMemoryRefusedError } from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { PERSONAL_MEMORY } from '../../../src/modules/agent-harness/domain/memory';
import { ExecutionStore } from '../../../src/modules/agent-harness/ports/execution-store';
import { MEMORIES_PREFIX, createMemoryRouter } from '../../../src/modules/agent-harness/transport/memory-routes';
import { continueAs } from '../../helpers/request-org-context';
import { memoryFixture } from './support/memory-store-contract';

const ORG = 'org-sw';
const RUN = { orgId: ORG, runId: 'run-1', actorId: 'alice', sessionId: 'ses-1' } as HarnessRun;
const clean = { complete: true, sources: [], integrity: 'first_party' as const };
const alice = { orgId: ORG, userId: 'alice', authorization: 'Bearer alice' };

describe('MEMSW: a person\'s own memory switch', () => {
  let store: InMemoryMemoryStore;
  let choices: InMemoryCodingEnginePreferences;
  let subject: Awaited<ReturnType<ExecutionStore['readClaimSubject']>>;
  let service: MemoryService;

  const make = (overrides: Partial<ConstructorParameters<typeof MemoryService>[0]> = {}): MemoryService =>
    new MemoryService({
      store,
      settings: { read: async () => PERSONAL_MEMORY },
      access: new SharedCitationService(new InMemoryRecordReadAccess()),
      runs: { readClaimSubject: async () => subject, readEnding: async () => null },
      choices,
      now: () => 5_000,
      ...overrides,
    });

  beforeEach(() => {
    store = new InMemoryMemoryStore();
    choices = new InMemoryCodingEnginePreferences();
    const kept = memoryFixture({ orgId: ORG, ownerId: 'alice', status: 'accepted' });
    void store.propose(kept);
    subject = {
      run: RUN,
      agentDefinition: null,
      via: 'session',
      pinned: { spec: { extensions: { 'x.pipeshub': { memory: { recalled: [kept.memoryId], writeScopes: ['user', 'session'] } } } } },
    } as unknown as typeof subject;
    service = make();
  });

  it('MEMSW-1: on by default; off, a new run is given none and may keep none', async () => {
    expect(await service.ownChoice(alice)).to.deep.equal({ remember: true });
    const on = await service.admission({ orgId: ORG, actorId: 'alice', sessionId: 'ses-1' });
    expect(on?.writeScopes).to.deep.equal(['user', 'session']);
    expect(await service.setOwnChoice(alice, false)).to.deep.equal({ remember: false });
    expect(await service.ownChoice(alice)).to.deep.equal({ remember: false });
    expect(await service.admission({ orgId: ORG, actorId: 'alice', sessionId: 'ses-1' })).to.equal(undefined);
    // Another person's memory is theirs to switch.
    expect((await service.admission({ orgId: ORG, actorId: 'bob', sessionId: 'ses-2' }))?.writeScopes).to.deep.equal([
      'user',
      'session',
    ]);
  });

  it('MEMSW-2: a running run serves no memory once it is off, refuses new ones and keeps none of its pending ones', async () => {
    expect(await service.recallForRun(ORG, 'run-1')).to.have.length(1);
    const pending = await service.propose(ORG, 'run-1', { content: 'Likes tables.', kind: 'preference', scope: 'user' });
    await service.setOwnChoice(alice, false);
    expect(await service.recallForRun(ORG, 'run-1')).to.deep.equal([]);
    let refused: unknown;
    try {
      await service.propose(ORG, 'run-1', { content: 'Likes charts.', kind: 'preference', scope: 'user' });
    } catch (error) {
      refused = error;
    }
    expect(refused).to.be.instanceOf(HarnessMemoryRefusedError);
    expect((refused as HarnessMemoryRefusedError).code).to.equal('MEMORY_WRITE_NOT_GRANTED');
    await service.settleRun(RUN, clean);
    expect(await store.get(ORG, pending.memoryId)).to.equal(null);
    // What the person already has stays on their page for them to see and delete.
    expect(await service.listOwn(alice)).to.have.length(1);
    await service.setOwnChoice(alice, true);
    expect(await service.recallForRun(ORG, 'run-1')).to.have.length(1);
  });

  it('MEMSW-3: a switch that cannot be read is off; a deployment without one keeps memory on and cannot switch it', async () => {
    const failing = make({
      choices: {
        memoryOff: async () => {
          throw new Error('store down');
        },
        writeMemoryOff: async () => undefined,
      },
    });
    expect(await failing.admission({ orgId: ORG, actorId: 'alice' })).to.equal(undefined);
    expect(await failing.ownChoice(alice)).to.deep.equal({ remember: false });
    const without = make({ choices: undefined });
    expect(await without.ownChoice(alice)).to.deep.equal({ remember: true });
    let refused: unknown;
    try {
      await without.setOwnChoice(alice, false);
    } catch (error) {
      refused = error;
    }
    expect((refused as HarnessMemoryRefusedError).code).to.equal('MEMORY_WRITE_NOT_GRANTED');
  });

  it('MEMSW-4: the person\'s erasure removes the switch with their other choices', async () => {
    await choices.writeMemoryOff(ORG, 'alice', true);
    expect(await choices.eraseUser(ORG, 'alice')).to.equal(1);
    expect(await choices.memoryOff(ORG, 'alice')).to.equal(false);
  });

  describe('over HTTP', () => {
    let server: Server;
    let base: string;
    let caller: { userId: string; orgId: string; isOAuth?: boolean; oauthScopes?: string[] };
    let sandbox: sinon.SinonSandbox;

    const call = async (method: string, body?: unknown) => {
      const response = await fetch(`${base}${MEMORIES_PREFIX}/preference`, {
        method,
        headers: { 'content-type': 'application/json', authorization: `Bearer ${caller.userId}` },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
      const text = await response.text();
      return { status: response.status, json: text ? JSON.parse(text) : null };
    };

    beforeEach(async () => {
      caller = { userId: 'alice', orgId: ORG };
      sandbox = sinon.createSandbox();
      for (const level of ['error', 'warn', 'info', 'debug'] as const) {
        sandbox.stub(Logger.prototype, level).callsFake(() => undefined as never);
      }
      const container = new Container();
      container.bind('AuthMiddleware').toConstantValue({
        authenticate: (req: any, _res: unknown, next: () => void) => {
          req.user = { ...caller };
          req.tokenPayload = { ...caller };
          continueAs(req.user, next);
        },
      });
      container.bind(AGENT_HARNESS_MEMORIES).toConstantValue(service);
      const app = express();
      app.use(express.json());
      app.use(MEMORIES_PREFIX, createMemoryRouter(container));
      app.use(ErrorMiddleware.handleError());
      server = app.listen(0);
      await new Promise((resolve) => server.once('listening', resolve));
      base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
    });

    afterEach(() => {
      sandbox.restore();
      server.close();
    });

    it('MEMSW-5: the person reads and sets their own switch; a connected app cannot; a bad body is refused', async () => {
      expect(await call('GET')).to.deep.equal({ status: 200, json: { remember: true } });
      expect(await call('PUT', { remember: false })).to.deep.equal({ status: 200, json: { remember: false } });
      expect((await call('GET')).json).to.deep.equal({ remember: false });
      expect(await choices.memoryOff(ORG, 'alice')).to.equal(true);
      expect((await call('PUT', { remember: 'no' })).status).to.equal(400);
      expect((await call('PUT', { remember: true, orgId: 'other' })).status).to.equal(400);
      caller = { userId: 'bob', orgId: ORG };
      expect((await call('GET')).json).to.deep.equal({ remember: true });
      caller = { userId: 'alice', orgId: ORG, isOAuth: true, oauthScopes: ['user:read', 'user:write'] };
      expect((await call('PUT', { remember: true })).status).to.equal(403);
      expect(await choices.memoryOff(ORG, 'alice')).to.equal(true);
    });
  });
});
