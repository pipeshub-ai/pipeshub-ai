/**
 * SLASH2 C1 `/usage`: GET …/conversations/:sessionId/usage sums the caller's own
 * root runs' ledger accounts, for a conversation the caller may watch, with the
 * scope taken from the verified credential.
 */
import 'reflect-metadata';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { expect } from 'chai';
import express from 'express';
import sinon from 'sinon';
import { Container } from 'inversify';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import { InMemoryBudgetLedger } from '../../../src/modules/agent-harness/adapters/in-memory-budget-ledger';
import { MongoSessionRunLookup } from '../../../src/libs/repositories/agent-harness/session-erasure/mongo';
import { AgentRunModel } from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import { RunTimelineService } from '../../../src/modules/agent-harness/application/run-timeline-service';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import { SessionExecutionScope } from '../../../src/modules/agent-harness/ports/chat-session-repository';
import {
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_RUN_TIMELINES,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
} from '../../../src/modules/agent-harness/composition';
import {
  createAgentHarnessSessionRouter,
  createChatHarnessSessionRouter,
} from '../../../src/modules/agent-harness/transport/session-routes';

const ORG = 'org-1';

async function usageWorld() {
  const ledger = new InMemoryBudgetLedger(() => 10_000);
  const cap = { usdMicros: 5_000_000, tokens: 1_000_000 };
  await ledger.openRoot(ORG, 'run-a', cap, { usdMicros: 110_000, tokens: 41_000 });
  await ledger.openRoot(ORG, 'run-b', cap, { usdMicros: 730_000, tokens: 271_000 });
  // A child's spend is already in its root's account; the lookup never names it.
  await ledger.openChild(ORG, 'run-b', 'run-b-child', { usdMicros: null, tokens: null });
  await ledger.openRoot(ORG, 'run-other', cap, { usdMicros: 999_000, tokens: 999 });
  const roots: Record<string, string[]> = {
    'org-1/s1/user-1': ['run-a', 'run-b', 'run-before-ledger'],
    'org-1/s1/user-2': ['run-other'],
  };
  const assertAccess = sinon.stub().callsFake(async (scope: SessionExecutionScope) => {
    if (scope.orgId !== ORG || scope.sessionId !== 's1' || !['user-1', 'user-2'].includes(scope.actorId)) {
      throw new HarnessNotFoundError();
    }
  });
  const ownRootRunsOf = sinon
    .stub()
    .callsFake(async (orgId: string, sessionId: string, actorId: string) => roots[`${orgId}/${sessionId}/${actorId}`] ?? []);
  const service = new RunTimelineService({
    runs: { streamAccess: sinon.stub().rejects(new Error('not used')) },
    journal: { readRun: async () => [] },
    journals: { resolve: async (events) => events },
    workspaces: null,
    ledger,
    usage: { access: { assertAccess }, runs: { ownRootRunsOf } },
  });
  return { service, assertAccess, ownRootRunsOf };
}

describe('SLASH2 C1: the conversation usage read', () => {
  it("sums the caller's own root runs; runs without an account are not counted", async () => {
    const { service } = await usageWorld();
    expect(await service.usage({ orgId: ORG, actorId: 'user-1', sessionId: 's1' })).to.deep.equal({
      runs: 2,
      committed: { usdMicros: 840_000, tokens: 312_000 },
    });
    expect(await service.usage({ orgId: ORG, actorId: 'user-2', sessionId: 's1' })).to.deep.equal({
      runs: 1,
      committed: { usdMicros: 999_000, tokens: 999 },
    });
  });

  it('refuses a conversation the caller may not watch before reading any run', async () => {
    const { service, ownRootRunsOf } = await usageWorld();
    for (const scope of [
      { orgId: ORG, actorId: 'stranger', sessionId: 's1' },
      { orgId: 'org-2', actorId: 'user-1', sessionId: 's1' },
    ]) {
      let refused: unknown;
      try {
        await service.usage(scope);
      } catch (error) {
        refused = error;
      }
      expect(refused).to.be.instanceOf(HarnessNotFoundError);
    }
    expect(ownRootRunsOf.callCount).to.equal(0);
  });

  it('reads only root harness-v2 runs of the caller, in the org and session', async () => {
    const lean = sinon.stub().resolves([{ runId: 'run-a' }]);
    const find = sinon.stub(AgentRunModel, 'find').returns({ read: () => ({ lean }) } as never);
    try {
      expect(await new MongoSessionRunLookup().ownRootRunsOf(ORG, 's1', 'user-1')).to.deep.equal(['run-a']);
      expect((find.firstCall.args as unknown[])[0]).to.deep.equal({
        orgId: ORG,
        sessionId: 's1',
        actorId: 'user-1',
        harnessMajor: 2,
        parent: null,
      });
    } finally {
      find.restore();
    }
  });

  it('serves both routes with the credential\'s scope and the read scope; a stranger gets 404', async () => {
    const tokens: Record<string, Record<string, unknown>> = {
      owner: { orgId: ORG, userId: 'user-1' },
      reader: { orgId: ORG, userId: 'user-1', isOAuth: true, oauthScopes: ['conversation:read', 'agent:read'] },
      chatOnly: { orgId: ORG, userId: 'user-1', isOAuth: true, oauthScopes: ['conversation:chat', 'agent:execute'] },
      stranger: { orgId: ORG, userId: 'stranger' },
    };
    const { service, assertAccess } = await usageWorld();
    const container = new Container();
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
      authenticate: (req: any, _res: unknown, next: () => void) => {
        req.user = tokens[String(req.headers['x-test-token'])];
        next();
      },
    } as never);
    container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({});
    container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({});
    container.bind(AGENT_HARNESS_RUN_TIMELINES).toConstantValue(service);
    const app = express();
    app.use('/api/v1/conversations', createChatHarnessSessionRouter(container));
    app.use('/api/v1/agents', createAgentHarnessSessionRouter(container));
    app.use(ErrorMiddleware.handleError());
    const originalLogger = (ErrorMiddleware as any).logger;
    (ErrorMiddleware as any).logger = { error: sinon.stub(), warn: sinon.stub(), info: sinon.stub(), debug: sinon.stub() };
    const server = await new Promise<Server>((resolve) => {
      const listening: Server = app.listen(0, () => resolve(listening));
    });
    const base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}/api/v1`;
    const get = (path: string, token: string) => fetch(`${base}${path}?orgId=org-2&userId=user-2`, { headers: { 'x-test-token': token } });
    try {
      for (const path of ['/conversations/s1/usage', '/agents/agent-1/conversations/s1/usage']) {
        for (const token of ['owner', 'reader']) {
          const reply = await get(path, token);
          expect(reply.status, `${path} ${token}`).to.equal(200);
          expect(await reply.json()).to.deep.equal({ runs: 2, committed: { usdMicros: 840_000, tokens: 312_000 } });
        }
        expect((await get(path, 'chatOnly')).status, `${path} chatOnly`).to.equal(403);
        expect((await get(path, 'stranger')).status, `${path} stranger`).to.equal(404);
      }
    } finally {
      (ErrorMiddleware as any).logger = originalLogger;
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
    const scopes = assertAccess.args.map(([scope]) => scope);
    expect(scopes[0]).to.deep.equal({ orgId: ORG, actorId: 'user-1', sessionId: 's1' });
    expect(scopes[3]).to.deep.equal({ orgId: ORG, actorId: 'user-1', sessionId: 's1', agentKey: 'agent-1' });
  });
});

describe('QAFIX-7: a run\'s timeline says what /status shows without a control-plane pin', () => {
  const pinned = (spec: Record<string, unknown>) => [
    { orgId: ORG, sessionId: 's1', streamId: 's1', seq: 1, runId: 'run-a', type: 'run.spec_pinned', schemaVersion: 1, critical: true, ts: '2026-10-18T00:00:00.000Z', writer: 'node', visibility: 'owner', data: { spec } },
  ];
  const timelineOf = (spec: Record<string, unknown>) =>
    new RunTimelineService({
      runs: {
        streamAccess: async () => ({
          run: { runId: 'run-a', orgId: ORG, sessionId: 's1', status: 'completed' },
          access: { audience: 'owner' },
        }),
      },
      journal: { readRun: async (_o: string, _r: string, after: number) => (after === 0 ? pinned(spec) : []) },
      journals: { resolve: async (events: unknown) => events },
      workspaces: null,
      ledger: null,
    } as never).timeline({ orgId: ORG, actorId: 'user-1', sessionId: 's1' }, 'run-a');

  it('a loop run: executor loop and its pinned effort, or null for the model\'s default', async () => {
    expect(await timelineOf({ model: { reasoningEffort: 'high' }, executor: { kind: 'loop' } })).to.include({ executor: 'loop', reasoningEffort: 'high' });
    expect(await timelineOf({ model: {}, executor: { kind: 'loop' } })).to.include({ executor: 'loop', reasoningEffort: null, toolsGranted: 0 });
    expect(await timelineOf({ model: {}, grants: { tools: [{ toolId: 'a' }, { toolId: 'b' }] } })).to.include({ executor: 'loop', toolsGranted: 2 });
  });

  it('an engine run names its engine', async () => {
    expect(await timelineOf({ model: {}, executor: { kind: 'engine', engine: { kind: 'codex' } } })).to.include({ executor: 'codex' });
  });

  it("CMDUI: a loop run names the coding engine its code tasks go to, or null for none", async () => {
    const codeTask = { extensions: { 'x.pipeshub': { codeTask: { profile: { engine: { kind: 'opencode' } } } } } };
    expect(await timelineOf({ model: {}, executor: { kind: 'loop' }, ...codeTask })).to.include({ executor: 'loop', codingEngine: 'opencode' });
    expect(await timelineOf({ model: {}, executor: { kind: 'loop' } })).to.include({ executor: 'loop', codingEngine: null });
  });
});
