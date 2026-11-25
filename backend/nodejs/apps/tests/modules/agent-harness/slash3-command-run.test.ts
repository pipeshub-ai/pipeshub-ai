/**
 * SLASH3 SB3a `/run`, Node's half: POST …/commands/run admits a run of kind
 * `command` on the chat's session, as a turn is admitted (owner only, org and
 * user from the credential, one active run, the quotas) but never steered or
 * queued behind a reply; its line rides in the sealed query; it requires the
 * worker feature `sandbox_command`; it never hands off to an engine (SD4); and
 * the history later turns are given leaves it out (SD9). Per person, 20 a minute.
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
import { HarnessConflictError, HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import { SANDBOX_COMMAND_PREFIX } from '../../../src/modules/agent-harness/domain/execution';
import { admittedRunKind } from '../../../src/modules/agent-harness/domain/run-kind';
import { admissionRequirements } from '../../../src/modules/agent-harness/domain/worker-compat';
import { withoutSandboxCommands } from '../../../src/modules/agent-harness/domain/session-audience';
import { EngineProfileStep } from '../../../src/modules/agent-harness/application/admission/policy-steps';
import {
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
} from '../../../src/modules/agent-harness/composition';
import {
  createAgentHarnessSessionRouter,
  createChatHarnessSessionRouter,
  SANDBOX_COMMANDS_PER_MINUTE,
} from '../../../src/modules/agent-harness/transport/session-routes';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { contractClock, createContractHarness } from './support/execution-store-contract/harness';
import { InMemoryCodingEnginePreferences } from '../../../src/modules/agent-harness/adapters/in-memory-coding-engine-preferences';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { CodingEngineService } from '../../../src/modules/agent-harness/application/coding-engine-service';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { runSpecSchema } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { profileExecutor, runSpecResolver } from '../../../src/modules/agent-harness/domain/run-spec';
import { agentDefinition } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { testModelAccess } from './support/model-access';
import { testControlPlane } from '../control-plane/support/in-memory';
import { HARNESS_ENABLED } from './support/rollout';
import { FakeEngineCeilings } from './support/org-policy-fakes';

const ORG = 'org-1';
const OWNER = 'user-owner';

describe('SLASH3 SB3a: a /run run, as admission sees it', () => {
  it('is kind command: run.admitted says so; a scheduled firing stays scheduled', () => {
    const command = { input: { sandboxCommand: { timeoutS: 60 } } } as never;
    expect(admittedRunKind(command, false)).to.equal('command');
    expect(admittedRunKind({ input: { query: 'q' } } as never, false)).to.equal('interactive');
    expect(admittedRunKind(command, true)).to.equal('scheduled');
  });

  it('requires the worker feature sandbox_command (an older worker would refuse the input)', () => {
    const run = {
      input: { query: '/run ls', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' }, sandboxCommand: { timeoutS: 60 } },
    } as never;
    expect(admissionRequirements(run).features).to.include('sandbox_command');
    expect(admissionRequirements({ input: { query: 'q', model: { chatMode: 'auto' } } } as never).features).to.not.include('sandbox_command');
  });

  it('never hands off to an engine, even in an engine agent\'s chat (SD4)', async () => {
    const step = new EngineProfileStep(
      { read: async () => ({}) } as never,
      { available: async () => true } as never,
      { info: () => undefined, warn: () => undefined } as never,
    );
    const context = {
      request: { orgId: ORG, input: { model: { chatMode: 'auto' }, sandboxCommand: { timeoutS: 60 } } },
      agentDefinition: { engineProfile: { engines: ['codex'] } },
    } as never as { engineProfile: unknown; codeTask: unknown };
    await step.run(context as never);
    expect(context.engineProfile).to.equal(null);
    expect(context.codeTask).to.equal(undefined);
  });

  it('later turns are given the history without /run turns, the command and its answer both', () => {
    const turns = [
      { role: 'user_query', content: 'hello' },
      { role: 'bot_response', content: 'hi' },
      { role: 'user_query', content: `${SANDBOX_COMMAND_PREFIX}cat secrets.txt` },
      { role: 'bot_response', content: 'Exit code 0.' },
      { role: 'user_query', content: 'and now?' },
    ];
    expect(withoutSandboxCommands(turns)).to.deep.equal([
      { role: 'user_query', content: 'hello' },
      { role: 'bot_response', content: 'hi' },
      { role: 'user_query', content: 'and now?' },
    ]);
  });

  it('the store: kind command on run.admitted; a second /run while one is active is RUN_ACTIVE, never steered', async () => {
    const clock = contractClock();
    const store = new InMemoryExecutionStore(clock);
    const h = createContractHarness(
      { executionStore: store, journal: store.journal, admissionStore: store, dispatches: async () => [], deletePayload: async () => undefined } as never,
      clock,
    );
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
    const input = { query: '/run ls', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' }, sandboxCommand: { timeoutS: 60 } };
    const first = await h.admit({ input } as never);
    const events = await store.journal.readRun(first.run.orgId, first.run.runId, 0, 100);
    const admitted = events.find((e) => e.type === 'run.admitted') as { data: { runKind: string } } | undefined;
    expect(admitted?.data.runKind).to.equal('command');
    expect(first.run.requires?.features).to.include('sandbox_command');
    let refused: unknown;
    try {
      await h.admit({ input, sessionId: first.run.sessionId } as never);
    } catch (error) {
      refused = error;
    }
    expect(refused).to.be.instanceOf(HarnessConflictError);
    expect((refused as HarnessConflictError).code).to.equal('RUN_ACTIVE');
  });
});

describe('SLASH3 SB3a: POST …/commands/run', () => {
  let server: Server;
  let base: string;
  const submitted: unknown[] = [];
  let originalLogger: unknown;
  let outcome: unknown;

  before(async () => {
    const container = new Container();
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
      authenticate: (req: any, _res: unknown, next: () => void) => {
        req.user = { orgId: ORG, userId: req.headers['x-test-user'] ?? OWNER };
        next();
      },
    } as never);
    container.bind('Logger').toConstantValue({ warn: () => undefined, info: () => undefined, error: () => undefined, debug: () => undefined });
    container.bind(AGENT_HARNESS_SESSION_RUN_SERVICE).toConstantValue({
      assertOwner: async (scope: { sessionId: string; actorId: string }) => {
        if (scope.sessionId === 'shared' || ![OWNER, 'rate-limited-owner'].includes(scope.actorId)) throw new HarnessNotFoundError();
      },
    });
    container.bind(AGENT_HARNESS_RUN_ADMISSION_SERVICE).toConstantValue({
      submit: async (request: unknown) => {
        submitted.push(request);
        return outcome;
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

  beforeEach(() => {
    outcome = {
      route: 'durable',
      admission: {
        run: { runId: 'run-9', sessionId: 's1', harnessMajor: 2, profile: 'dispatched', status: 'queued', outcome: null, revision: 1, absoluteDeadlineAt: 9, createdAt: 1, updatedAt: 1 },
      },
    };
  });

  after(async () => {
    (ErrorMiddleware as any).logger = originalLogger;
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  const post = (path: string, body: unknown, user = OWNER) =>
    fetch(`${base}${path}`, { method: 'POST', headers: { 'content-type': 'application/json', 'x-test-user': user }, body: JSON.stringify(body) });

  it('admits the line as the query of a command run, with the chat\'s mode and model; org and user from the credential', async () => {
    const res = await post('/agents/agent-1/conversations/s1/commands/run', {
      command: 'pytest -q',
      idempotencyKey: 'run-key-1',
      chatMode: 'quick',
      modelKey: 'model-1',
    });
    expect(res.status).to.equal(202);
    expect(((await res.json()) as { admission: { run: { runId: string } } }).admission.run.runId).to.equal('run-9');
    expect(submitted.at(-1)).to.deep.include({ orgId: ORG, actorId: OWNER, agentKey: 'agent-1', sessionId: 's1', idempotencyKey: 'run-key-1' });
    expect((submitted.at(-1) as { input: unknown }).input).to.deep.equal({
      query: '/run pytest -q',
      recordIds: [],
      filters: {},
      attachments: [],
      model: { chatMode: 'quick', modelKey: 'model-1' },
      sandboxCommand: { timeoutS: 60 },
    });
  });

  it('refuses a bad body before admission: empty, over 8 KiB, a timeout past 600 s, an org or user named', async () => {
    const count = submitted.length;
    for (const body of [
      { command: '   ', idempotencyKey: 'k' },
      { command: 'x'.repeat(8 * 1024 + 1), idempotencyKey: 'k' },
      { command: 'ls', timeoutS: 601, idempotencyKey: 'k' },
      { command: 'ls', idempotencyKey: 'k', orgId: 'org-2' },
      { command: 'ls' },
    ]) {
      expect((await post('/conversations/s1/commands/run', body)).status, JSON.stringify(body).slice(0, 60)).to.equal(400);
    }
    expect(submitted.length).to.equal(count);
  });

  it('a participant of a shared chat, or anyone not its owner: 404, nothing admitted', async () => {
    const count = submitted.length;
    expect((await post('/conversations/shared/commands/run', { command: 'ls', idempotencyKey: 'k' })).status).to.equal(404);
    expect((await post('/conversations/s1/commands/run', { command: 'ls', idempotencyKey: 'k' }, 'someone-else')).status).to.equal(404);
    expect(submitted.length).to.equal(count);
  });

  it('a chat on the classic path is refused: nothing is written there', async () => {
    outcome = { route: 'legacy', rollout: { reason: 'kill_switch' } };
    const res = await post('/conversations/s1/commands/run', { command: 'ls', idempotencyKey: 'k-legacy' });
    expect(res.status).to.be.within(400, 499);
  });

  it(`the ${String(SANDBOX_COMMANDS_PER_MINUTE + 1)}th /run in a minute is refused 429 with Retry-After`, async () => {
    const user = 'rate-limited-owner';
    let last: Response | undefined;
    for (let i = 0; i <= SANDBOX_COMMANDS_PER_MINUTE; i += 1) {
      last = await post('/conversations/s1/commands/run', { command: 'ls', idempotencyKey: `k-${String(i)}` }, user);
    }
    expect(last?.status).to.equal(429);
    expect(last?.headers.get('retry-after')).to.match(/^\d+$/);
  });
});

/**
 * The plan's cross-executor case, Node's half: a /run in a loop chat, an opencode, a Codex and a Claude Code
 * agent's chat, and the assistant's Agent mode with a chosen engine is the same run: kind command, the loop
 * executor, no engine hand-off (no codeTask), the same requirements. The worker's half (the same journal and no
 * model call) is `tests/unit/harness/test_slash3_command_run.py`.
 */
describe('SLASH3 SB3a conformance: a /run is the same run whatever executor the chat uses', () => {
  const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
  const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };

  const pinned = async (chat: { agentKey?: string; engines?: string[]; chatMode: string; userEngine?: string }) => {
    let now = 1_000;
    const store = new InMemoryExecutionStore({ now: () => now });
    const preferences = new InMemoryCodingEnginePreferences();
    if (chat.userEngine !== undefined) await preferences.writeUser(ORG, OWNER, chat.userEngine);
    const settings = new FakeEngineCeilings();
    const admit = (sandboxCommand: boolean) => {
      now += 1;
      return new RunAdmissionService({
        store,
        rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
        clock: { now: () => now },
        agentAccess: {
          authorizeRun: async (request) =>
            agentDefinition({ agentKey: request.agentKey, ...(chat.engines ? { engineProfile: { engines: chat.engines } } : {}) }),
        },
        executors: profileExecutor,
        roles: { roleOf: async () => 'owner' } as never,
        engineProfiles: settings,
        modelAccess: testModelAccess(),
        codingEngine: new CodingEngineService({
          ceilings: settings,
          access: testModelAccess(),
          preferences,
          transaction: testControlPlane(),
          availability: { unavailable: async () => new Set<string>() },
        }),
        workerFeatures: { live: async () => true, metrics: { withheld: () => undefined } } as never,
      }).submit({
        orgId: ORG,
        actorId: OWNER,
        ...(chat.agentKey !== undefined ? { agentKey: chat.agentKey } : {}),
        authorization: 'Bearer t',
        durableRequested: true,
        idempotencyKey: `k-${String(now)}`,
        deadlineMs: 60_000,
        input: {
          query: sandboxCommand ? '/run echo hi > out.txt && cat out.txt' : 'q',
          recordIds: [],
          filters: {},
          attachments: [],
          model: { chatMode: chat.chatMode },
          ...(sandboxCommand ? { sandboxCommand: { timeoutS: 60 } } : {}),
        },
      });
    };
    const execution = new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      capabilities: { current: () => DURABLE },
      resolveSpec: runSpecResolver(profileExecutor),
    });
    const specOf = async (sandboxCommand: boolean) => {
      const outcome = await admit(sandboxCommand);
      if (outcome.route !== 'durable') return expect.fail(JSON.stringify(outcome));
      const run = outcome.admission.run;
      const claimed = await execution.claim({ orgId: ORG, runId: run.runId, workerId: 'w', attemptId: `a-${run.runId}`, leaseDurationMs: 1_000 });
      const spec = runSpecSchema.parse(claimed.pinned?.spec);
      const ext = (spec.extensions?.['x.pipeshub'] ?? {}) as Record<string, unknown>;
      return { runKind: spec.runKind, executor: spec.executor, codeTask: ext['codeTask'] !== undefined, features: run.requires?.features ?? [] };
    };
    return { turn: await specOf(false), command: await specOf(true) };
  };

  const CHATS = [
    { name: 'loop (an agent without an engine)', chat: { agentKey: 'agent-1', chatMode: 'quick' }, engine: false },
    { name: 'opencode', chat: { agentKey: 'agent-1', chatMode: 'quick', engines: ['opencode'] }, engine: true },
    { name: 'Codex', chat: { agentKey: 'agent-1', chatMode: 'quick', engines: ['codex'] }, engine: true },
    { name: 'Claude Code', chat: { agentKey: 'agent-1', chatMode: 'quick', engines: ['claude_code'] }, engine: true },
    { name: 'the assistant in Agent mode with Codex chosen', chat: { chatMode: 'agent', userEngine: 'codex' }, engine: true },
  ];

  for (const { name, chat, engine } of CHATS) {
    it(`${name}: the turn ${engine ? 'hands off to its engine' : 'runs on the loop'}; the /run is kind command on the loop, never an engine`, async () => {
      const { turn, command } = await pinned(chat);
      expect(turn.codeTask).to.equal(engine);
      expect(command).to.deep.include({ runKind: 'command', codeTask: false });
      expect(command.executor ?? { kind: 'loop' }).to.deep.equal({ kind: 'loop' });
      expect(command.features).to.include('sandbox_command');
      expect(command.features).to.not.include('code_task_tool');
    });
  }
});
