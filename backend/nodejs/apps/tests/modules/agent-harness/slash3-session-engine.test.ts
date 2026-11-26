/**
 * SLASH3 SB4 `/engine`: a chat's own coding engine (`session.codingEngine`).
 * The turn's engine resolves conversation → person → org → none (ENGPICK's
 * Agent-mode layer gains the chat's); an engine agent whose profile lists
 * several runs the one the chat chose; a running reply keeps its pin; a
 * saved choice is checked as a person's save and is one audited change; the
 * PUT route is the owner's, org and user from the credential.
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
import { InMemoryCodingEnginePreferences } from '../../../src/modules/agent-harness/adapters/in-memory-coding-engine-preferences';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { CodingEngineService } from '../../../src/modules/agent-harness/application/coding-engine-service';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { runSpecSchema } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { resolveCodingEngine } from '../../../src/modules/agent-harness/domain/coding-engines';
import { HarnessConflictError, HarnessEngineProfileRefusedError, HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { principalRefFor, profileExecutor, runSpecResolver } from '../../../src/modules/agent-harness/domain/run-spec';
import {
  AGENT_HARNESS_AGENT_ACCESS,
  AGENT_HARNESS_CODING_ENGINES,
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
} from '../../../src/modules/agent-harness/composition';
import {
  createAgentHarnessSessionRouter,
  createChatHarnessSessionRouter,
} from '../../../src/modules/agent-harness/transport/session-routes';
import { agentDefinition } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { testModelAccess } from './support/model-access';
import { HARNESS_ENABLED } from './support/rollout';
import { testControlPlane } from '../control-plane/support/in-memory';
import { FakeEngineCeilings } from './support/org-policy-fakes';

const ORG = 'org-1';
const USER = 'user-1';
const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);

const codeTaskKind = (spec: unknown): string | undefined =>
  (
    (spec as { extensions?: Record<string, Record<string, unknown>> }).extensions?.['x.pipeshub']?.['codeTask'] as
      | { profile?: { engine?: { kind?: string } } }
      | undefined
  )?.profile?.engine?.kind;

describe('SLASH3 SB4: the chat\'s coding engine', () => {
  it('resolves conversation → person → org → none; the chat\'s none is a setting, not a fallback', () => {
    expect(resolveCodingEngine({ session: 'opencode', user: 'codex', org: 'claude_code' })).to.deep.equal({ engine: 'opencode', source: 'session' });
    expect(resolveCodingEngine({ session: 'none', user: 'codex', org: 'claude_code' })).to.deep.equal({ engine: null, source: 'session' });
    expect(resolveCodingEngine({ session: null, user: 'codex', org: 'claude_code' })).to.deep.equal({ engine: 'codex', source: 'user' });
    expect(resolveCodingEngine({ user: null, org: 'claude_code' })).to.deep.equal({ engine: 'claude_code', source: 'org' });
  });

  describe('admission', () => {
    let now: number;
    let store: InMemoryExecutionStore;
    let execution: ExecutionService;
    let preferences: InMemoryCodingEnginePreferences;
    let settings: FakeEngineCeilings;
    const engines = () =>
      new CodingEngineService({
        ceilings: settings,
        access: testModelAccess(),
        preferences,
        transaction: testControlPlane(),
        availability: { unavailable: async () => new Set<string>() },
      });

    const admit = async (options: { sessionId?: string; agentKey?: string; chatMode?: string; agentEngines?: string[] } = {}): Promise<HarnessRun> => {
      now += 1;
      const outcome = await new RunAdmissionService({
        store,
        rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
        clock: { now: () => now },
        agentAccess: {
          authorizeRun: async (request) =>
            agentDefinition({
              agentKey: request.agentKey,
              ...(options.agentEngines ? { engineProfile: { engines: options.agentEngines } } : {}),
            }),
        },
        executors: profileExecutor,
        // The person owns every chat here: engine work in a conversation is the owner's (SS1).
        roles: { roleOf: async () => 'owner' } as never,
        engineProfiles: settings,
        modelAccess: testModelAccess(),
        codingEngine: engines(),
        workerFeatures: { live: async () => true, metrics: { withheld: () => undefined } } as never,
      }).submit({
        orgId: ORG,
        actorId: USER,
        ...(options.agentKey !== undefined ? { agentKey: options.agentKey } : {}),
        ...(options.sessionId !== undefined ? { sessionId: options.sessionId } : {}),
        authorization: 'Bearer t',
        durableRequested: true,
        idempotencyKey: `k-${String(now)}`,
        deadlineMs: 60_000,
        input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: options.chatMode ?? 'agent' } },
      });
      if (outcome.route !== 'durable') return expect.fail(`expected a durable run, got ${JSON.stringify(outcome)}`);
      return outcome.admission.run;
    };

    const spec = async (run: HarnessRun) => {
      const claimed = await execution.claim({ orgId: run.orgId, runId: run.runId, workerId: 'w', attemptId: `a-${run.runId}`, leaseDurationMs: 1_000 });
      return runSpecSchema.parse(claimed.pinned?.spec);
    };

    const end = async (run: HarnessRun) => {
      const current = (await store.get(ORG, run.runId))!;
      await execution.control({
        orgId: ORG,
        runId: run.runId,
        expectedRevision: current.revision,
        action: 'cancel',
        by: principalRefFor(USER),
        commandId: `end-${run.runId}`,
        dispatchCommandId: `end-${run.runId}:dispatch`,
      });
    };

    beforeEach(() => {
      now = 1_000;
      store = new InMemoryExecutionStore({ now: () => now });
      preferences = new InMemoryCodingEnginePreferences();
      settings = new FakeEngineCeilings();
      execution = new ExecutionService({
        store,
        journal: store.journal,
        journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
        capabilities: { current: () => DURABLE },
        resolveSpec: runSpecResolver(profileExecutor),
      });
    });

    // A chat is made by its first turn; that turn is cancelled before a worker claims it, so the next one starts.
    const chat = async (options: Parameters<typeof admit>[0] = {}): Promise<string> => {
      const first = await admit(options);
      await end(first);
      return first.sessionId;
    };

    it('the chat\'s choice wins over the person\'s for its next turn, and only in that chat', async () => {
      await preferences.writeUser(ORG, USER, 'codex');
      const sessionId = await chat();
      await preferences.writeSession(ORG, sessionId, 'opencode');
      expect(codeTaskKind(await spec(await admit({ sessionId })))).to.equal('opencode');
      expect(codeTaskKind(await spec(await admit()))).to.equal('codex');
    });

    it('the chat\'s none: no engine for its turns, whatever the person and the org chose', async () => {
      await preferences.writeOrg(ORG, 'codex');
      const sessionId = await chat();
      await preferences.writeSession(ORG, sessionId, 'none');
      expect(codeTaskKind(await spec(await admit({ sessionId })))).to.equal(undefined);
    });

    it('an engine agent whose profile lists several runs the one the chat chose; a choice it does not list is ignored', async () => {
      const agent = { agentKey: 'agent-1', chatMode: 'quick', agentEngines: ['codex', 'opencode'] };
      const byDefault = codeTaskKind(await spec(await admit(agent)));
      expect(agent.agentEngines).to.include(byDefault);
      const other = agent.agentEngines.find((kind) => kind !== byDefault)!;
      const chosen = await chat(agent);
      await preferences.writeSession(ORG, chosen, other);
      expect(codeTaskKind(await spec(await admit({ ...agent, sessionId: chosen })))).to.equal(other);
      const unlisted = await chat(agent);
      await preferences.writeSession(ORG, unlisted, 'claude_code');
      expect(codeTaskKind(await spec(await admit({ ...agent, sessionId: unlisted })))).to.equal(byDefault);
    });

    it('saving: an engine the org cannot use is refused with its code and nothing is written; none and null always save', async () => {
      const service = engines();
      await settings.write(ORG, { engines: ['codex'] }, 'p_admin');
      let refused: unknown;
      try {
        await service.setSessionChoice(ORG, USER, 's-1', 'opencode');
      } catch (error) {
        refused = error;
      }
      expect(refused).to.be.instanceOf(HarnessEngineProfileRefusedError);
      expect(await preferences.readSession(ORG, 's-1')).to.equal(null);
      expect(await service.setSessionChoice(ORG, USER, 's-1', 'codex')).to.deep.equal({ engine: 'codex' });
      expect(await service.setSessionChoice(ORG, USER, 's-1', 'none')).to.deep.equal({ engine: 'none' });
      expect(await service.setSessionChoice(ORG, USER, 's-1', null)).to.deep.equal({ engine: null });
      expect(await preferences.readSession(ORG, 's-1')).to.equal(null);
      let unlisted: unknown;
      try {
        await service.setSessionChoice(ORG, USER, 's-1', 'codex', ['opencode']);
      } catch (error) {
        unlisted = error;
      }
      expect(unlisted).to.be.instanceOf(HarnessConflictError);
    });

    it('a save is one audited control-plane change of coding-engine session/<id>', async () => {
      const changes: unknown[] = [];
      const service = new CodingEngineService({
        ceilings: settings,
        access: testModelAccess(),
        preferences,
        transaction: {
          run: async (_ctx: unknown, work: (tx: unknown) => Promise<{ change: unknown }>) => {
            const writes: Array<() => Promise<void>> = [];
            const done = await work({ external: (write: () => Promise<void>) => writes.push(write) });
            for (const write of writes) await write();
            changes.push(done.change);
            return done;
          },
        } as never,
      });
      await service.setSessionChoice(ORG, USER, 's-9', 'codex');
      expect(changes).to.have.length(1);
      expect(changes[0]).to.deep.include({ object: { type: 'coding-engine', id: 'session/s-9' }, changed: ['codingEngine'] });
      expect(await preferences.readSession(ORG, 's-9')).to.equal('codex');
    });
  });
});

describe('SLASH3 SB4: PUT …/coding-engine', () => {
  let server: Server;
  let base: string;
  const calls: unknown[][] = [];
  let originalLogger: unknown;

  before(async () => {
    const container = new Container();
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
      authenticate: (req: any, _res: unknown, next: () => void) => {
        req.user = { orgId: ORG, userId: USER };
        next();
      },
    } as never);
    container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({
      assertOwner: async (scope: { sessionId: string }) => {
        if (scope.sessionId === 'shared') throw new HarnessNotFoundError();
      },
    });
    container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({});
    container.bind(AGENT_HARNESS_CODING_ENGINES).toConstantValue({
      setSessionChoice: async (...args: unknown[]) => {
        calls.push(args);
        return { engine: args[3] };
      },
    });
    const app = express();
    app.use(express.json());
    app.use('/api/v1/conversations', createChatHarnessSessionRouter(container));
    app.use('/api/v1/agents', createAgentHarnessSessionRouter(container));
    app.use(ErrorMiddleware.handleError());
    originalLogger = (ErrorMiddleware as any).logger;
    (ErrorMiddleware as any).logger = { error: sinon.stub(), warn: sinon.stub(), info: sinon.stub(), debug: sinon.stub() };
    server = await new Promise<Server>((resolve) => {
      const listening: Server = app.listen(0, () => resolve(listening));
    });
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}/api/v1`;
  });

  after(async () => {
    (ErrorMiddleware as any).logger = originalLogger;
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  const put = (path: string, body: unknown) =>
    fetch(`${base}${path}`, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });

  it('saves the chat\'s engine, none or null for the owner, with org and user from the credential', async () => {
    for (const engine of ['codex', 'none', null]) {
      const res = await put('/agents/agent-1/conversations/s1/coding-engine', { engine });
      expect(res.status).to.equal(200);
      expect(calls.at(-1)).to.deep.equal([ORG, USER, 's1', engine]);
    }
  });

  it('refuses another chat\'s owner (404), a malformed engine and an org named in the body (400)', async () => {
    const count = calls.length;
    expect((await put('/conversations/shared/coding-engine', { engine: 'codex' })).status).to.equal(404);
    expect((await put('/conversations/s1/coding-engine', { engine: 'Codex!' })).status).to.equal(400);
    expect((await put('/conversations/s1/coding-engine', { engine: 'codex', orgId: 'org-2' })).status).to.equal(400);
    expect(calls.length).to.equal(count);
  });
});

describe("CMDUI: an agent chat's /engine saves only an engine its owner listed", () => {
  let server: Server;
  let base: string;
  const calls: unknown[][] = [];
  const asked: unknown[] = [];
  let originalLogger: unknown;
  const engines: Record<string, unknown> = {
    'agent-two': { engines: ['codex@0.160.0', 'opencode'] },
    'agent-loop': undefined,
  };

  before(async () => {
    const container = new Container();
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
      authenticate: (req: any, _res: unknown, next: () => void) => {
        req.user = { orgId: ORG, userId: USER };
        next();
      },
    } as never);
    container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({ assertOwner: async () => undefined });
    container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({});
    container.bind(AGENT_HARNESS_CODING_ENGINES).toConstantValue({
      setSessionChoice: async (...args: unknown[]) => {
        calls.push(args);
        return { engine: args[3] };
      },
    });
    container.bind(AGENT_HARNESS_AGENT_ACCESS).toConstantValue({
      authorizeRun: async (request: { agentKey: string }) => {
        asked.push(request);
        if (request.agentKey === 'not-mine') return null;
        return { ...agentDefinition(request.agentKey), engineProfile: engines[request.agentKey] };
      },
    });
    const app = express();
    app.use(express.json());
    app.use('/api/v1/conversations', createChatHarnessSessionRouter(container));
    app.use('/api/v1/agents', createAgentHarnessSessionRouter(container));
    app.use(ErrorMiddleware.handleError());
    originalLogger = (ErrorMiddleware as any).logger;
    (ErrorMiddleware as any).logger = { error: sinon.stub(), warn: sinon.stub(), info: sinon.stub(), debug: sinon.stub() };
    server = await new Promise<Server>((resolve) => {
      const listening: Server = app.listen(0, () => resolve(listening));
    });
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}/api/v1`;
  });

  after(async () => {
    (ErrorMiddleware as any).logger = originalLogger;
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  const put = (path: string, body: unknown) =>
    fetch(`${base}${path}`, {
      method: 'PUT',
      headers: { 'content-type': 'application/json', authorization: 'Bearer caller' },
      body: JSON.stringify(body),
    });

  it("hands the save the engines the agent's profile lists, read with the caller's own credential", async () => {
    expect((await put('/agents/agent-two/conversations/s1/coding-engine', { engine: 'codex' })).status).to.equal(200);
    expect(calls.at(-1)).to.deep.equal([ORG, USER, 's1', 'codex', ['codex@0.160.0', 'opencode']]);
    expect(asked.at(-1)).to.deep.equal({ orgId: ORG, actorId: USER, agentKey: 'agent-two', authorization: 'Bearer caller' });
    expect((await put('/agents/agent-loop/conversations/s1/coding-engine', { engine: 'none' })).status).to.equal(200);
    expect(calls.at(-1)).to.deep.equal([ORG, USER, 's1', 'none', []]);
  });

  it('going back to the default needs no agent read; the default chat never reads one', async () => {
    const before = asked.length;
    expect((await put('/agents/agent-two/conversations/s1/coding-engine', { engine: null })).status).to.equal(200);
    expect(calls.at(-1)).to.deep.equal([ORG, USER, 's1', null]);
    expect((await put('/conversations/s1/coding-engine', { engine: 'codex' })).status).to.equal(200);
    expect(calls.at(-1)).to.deep.equal([ORG, USER, 's1', 'codex']);
    expect(asked.length).to.equal(before);
  });

  it('an agent the caller may not run saves nothing', async () => {
    const count = calls.length;
    expect((await put('/agents/not-mine/conversations/s1/coding-engine', { engine: 'codex' })).status).to.equal(403);
    expect(calls.length).to.equal(count);
  });

  it("the service refuses None in an agent's chat too: the agent's own profile decides there", async () => {
    const service = new CodingEngineService({
      ceilings: new FakeEngineCeilings(),
      access: testModelAccess(),
      preferences: new InMemoryCodingEnginePreferences(),
      transaction: testControlPlane(),
    } as never);
    let refused: unknown;
    try {
      await service.setSessionChoice(ORG, USER, 's-1', 'none', ['codex', 'opencode']);
    } catch (error) {
      refused = error;
    }
    expect(refused).to.be.instanceOf(HarnessConflictError);
  });
});
