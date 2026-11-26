/**
 * CMDUI2: `/engine` before a chat's first message. The new-chat request names the engine
 * (`codingEngine`); admission checks it exactly as `PUT …/coding-engine` would (the agent's listed
 * engines, then the org's ceiling, images and model access), runs the first turn on it, and saves it
 * on the session the turn creates before `submit` returns, so the stream never opens before the save.
 * A refused choice is logged, never saved, and the turn runs on the default engine. Only a turn that
 * creates its chat reads the field. `GET …/runs/{runId}/resolved` answers a run without a pin as an
 * empty view (200), not a 404.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { InMemoryCodingEnginePreferences } from '../../../src/modules/agent-harness/adapters/in-memory-coding-engine-preferences';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { CodingEngineService } from '../../../src/modules/agent-harness/application/coding-engine-service';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { SessionRunService } from '../../../src/modules/agent-harness/application/session-run-service';
import { runSpecSchema } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { principalRefFor, profileExecutor, runSpecResolver } from '../../../src/modules/agent-harness/domain/run-spec';
import { chatTurnInput } from '../../../src/modules/agent-harness/transport/chat-turn-route';
import { AgentHarnessSessionController } from '../../../src/modules/agent-harness/transport/session-controller';
import {
  agentStreamCreateSchema,
  enterpriseSearchStreamCreateSchema,
} from '../../../src/modules/enterprise_search/validators/es_validators';
import { agentDefinition } from './support/agent-definition';
import { startChatTurnHarness, ChatTurnHarness } from './support/chat-turn-harness';
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

describe('CMDUI2: the coding engine chosen before a chat\'s first message', () => {
  let now: number;
  let store: InMemoryExecutionStore;
  let execution: ExecutionService;
  let preferences: InMemoryCodingEnginePreferences;
  let settings: FakeEngineCeilings;
  let lines: Array<[string, string, Record<string, unknown> | undefined]>;

  const logger: HarnessLogger = {
    debug: (event, meta) => void lines.push(['debug', event, meta]),
    info: (event, meta) => void lines.push(['info', event, meta]),
    warn: (event, meta) => void lines.push(['warn', event, meta]),
    error: (event, meta) => void lines.push(['error', event, meta]),
  };
  const logged = (event: string) => lines.filter(([, e]) => e === event).map(([level, , meta]) => ({ level, ...meta }));

  const engines = () =>
    new CodingEngineService({
      ceilings: settings,
      access: testModelAccess(),
      preferences,
      transaction: testControlPlane(),
      availability: { unavailable: async () => new Set<string>() },
    });

  const admit = async (
    options: { sessionId?: string; agentKey?: string; chatMode?: string; agentEngines?: string[]; codingEngine?: string } = {},
  ): Promise<HarnessRun> => {
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
      roles: { roleOf: async () => 'owner' } as never,
      engineProfiles: settings,
      modelAccess: testModelAccess(),
      codingEngine: engines(),
      logger,
      workerFeatures: { live: async () => true, metrics: { withheld: () => undefined } } as never,
    }).submit({
      orgId: ORG,
      actorId: USER,
      ...(options.agentKey !== undefined ? { agentKey: options.agentKey } : {}),
      ...(options.sessionId !== undefined ? { sessionId: options.sessionId } : {}),
      ...(options.codingEngine !== undefined ? { codingEngine: options.codingEngine } : {}),
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

  beforeEach(() => {
    now = 1_000;
    lines = [];
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

  it('CMDUI2-1: the first run is pinned to the chosen engine, and the chat holds it once admission returns', async () => {
    await preferences.writeUser(ORG, USER, 'codex');
    const run = await admit({ codingEngine: 'opencode' });
    expect(await preferences.readSession(ORG, run.sessionId), 'saved before submit returned').to.equal('opencode');
    expect(codeTaskKind(await spec(run))).to.equal('opencode');
    expect(logged('run.coding_engine_chosen')[0]).to.include({ engine: 'opencode', source: 'session' });
  });

  it('CMDUI2-2: "none" for the first message: no engine for the run, and the chat keeps none', async () => {
    await preferences.writeUser(ORG, USER, 'codex');
    const run = await admit({ codingEngine: 'none' });
    expect(codeTaskKind(await spec(run))).to.equal(undefined);
    expect(await preferences.readSession(ORG, run.sessionId)).to.equal('none');
  });

  it('CMDUI2-3: a choice the org cannot use is refused as /engine refuses it; the turn runs on the default and nothing is saved', async () => {
    await settings.write(ORG, { engines: ['codex'] }, 'p_admin');
    await preferences.writeUser(ORG, USER, 'codex');
    const run = await admit({ codingEngine: 'opencode' });
    expect(codeTaskKind(await spec(run))).to.equal('codex');
    expect(await preferences.readSession(ORG, run.sessionId)).to.equal(null);
    expect(logged('run.coding_engine_choice_refused')[0]).to.include({ level: 'info', engine: 'opencode', reason: 'engine_profile_widening' });
  });

  it('CMDUI2-4: an agent chat takes only an engine its agent lists, never none', async () => {
    const agent = { agentKey: 'agent-1', chatMode: 'quick', agentEngines: ['codex', 'opencode'] };
    const listed = await admit({ ...agent, codingEngine: 'opencode' });
    expect(codeTaskKind(await spec(listed))).to.equal('opencode');
    expect(await preferences.readSession(ORG, listed.sessionId)).to.equal('opencode');
    for (const unlisted of ['claude_code', 'none']) {
      const run = await admit({ ...agent, codingEngine: unlisted });
      expect(await preferences.readSession(ORG, run.sessionId), unlisted).to.equal(null);
      expect(agent.agentEngines).to.include(codeTaskKind(await spec(run)));
    }
    expect(logged('run.coding_engine_choice_refused').map((line) => line['reason'])).to.deep.equal(['engine_unavailable', 'engine_unavailable']);
  });

  it('CMDUI2-5: a turn in an existing chat never reads the field: /engine is how that chat changes', async () => {
    await preferences.writeUser(ORG, USER, 'codex');
    const first = await admit({ codingEngine: 'opencode' });
    await execution.control({
      orgId: ORG,
      runId: first.runId,
      expectedRevision: (await store.get(ORG, first.runId))!.revision,
      action: 'cancel',
      by: principalRefFor(USER),
      commandId: `end-${first.runId}`,
      dispatchCommandId: `end-${first.runId}:dispatch`,
    });
    const next = await admit({ sessionId: first.sessionId, codingEngine: 'codex' });
    expect(await preferences.readSession(ORG, first.sessionId)).to.equal('opencode');
    expect(codeTaskKind(await spec(next))).to.equal('opencode');
  });

  it('CMDUI2-6: a save that fails after a checked choice is logged; the run already holds its engine', async () => {
    sinon.stub(preferences, 'writeSession').rejects(new Error('mongo down'));
    try {
      const run = await admit({ codingEngine: 'opencode' });
      expect(codeTaskKind(await spec(run))).to.equal('opencode');
      expect(logged('run.coding_engine_choice_not_saved')[0]).to.include({ level: 'warn', engine: 'opencode', sessionId: run.sessionId });
    } finally {
      sinon.restore();
    }
  });

  describe('the new-chat request', () => {
    let http: ChatTurnHarness | undefined;

    afterEach(async () => {
      await http?.close();
      http = undefined;
      sinon.restore();
    });

    it('CMDUI2-7: the create routes accept `codingEngine` in the /engine format; a run input never carries it', () => {
      const ok = (schema: typeof enterpriseSearchStreamCreateSchema, codingEngine: unknown, chatMode: string) =>
        schema.safeParse({ body: { query: 'q', chatMode, codingEngine } }).success;
      expect(ok(enterpriseSearchStreamCreateSchema, 'codex', 'agent')).to.equal(true);
      expect(ok(enterpriseSearchStreamCreateSchema, 'none', 'agent')).to.equal(true);
      expect(ok(enterpriseSearchStreamCreateSchema, 'Claude Code', 'agent')).to.equal(false);
      expect(ok(enterpriseSearchStreamCreateSchema, null, 'agent')).to.equal(false);
      const agent = (codingEngine: unknown) =>
        agentStreamCreateSchema.safeParse({ params: { agentKey: 'agent-1' }, body: { query: 'q', chatMode: 'quick', codingEngine } }).success;
      expect(agent('opencode')).to.equal(true);
      expect(agent('Open Code')).to.equal(false);
      const input = chatTurnInput({ query: 'q', chatMode: 'agent', codingEngine: 'codex' }, 'chat');
      expect(input).to.deep.equal(chatTurnInput({ query: 'q', chatMode: 'agent' }, 'chat'));
    });

    it('CMDUI2-8: the turn that creates a chat hands the engine to admission; a follow-up and a legacy turn do not', async () => {
      http = await startChatTurnHarness({ publicSurface: 'web' });
      await (await http.post('/agents/agent-1/conversations/stream', 'session', { query: 'q', chatMode: 'quick', codingEngine: 'codex' })).text();
      expect(http.submit.firstCall.args[0]).to.include({ codingEngine: 'codex' });
      const sessionId = (await http.store.get(ORG_OF(http), runIdOf(http))).sessionId;
      await (
        await http.post(`/agents/agent-1/conversations/${sessionId}/messages/stream`, 'session', { query: 'q', chatMode: 'quick', codingEngine: 'opencode' })
      ).text();
      expect(http.submit.secondCall.args[0]).not.to.have.property('codingEngine');
      await http.close();
      http = await startChatTurnHarness({ publicSurface: 'web', enabled: false });
      expect(await (await http.post('/conversations/stream', 'session', { query: 'q', chatMode: 'agent', codingEngine: 'codex' })).text()).to.equal('legacy');
      expect(http.legacy.firstCall.args[0].body).not.to.have.property('codingEngine');
    });
  });

  it('CMDUI2-9: a run the control plane did not pin answers an empty view, logged at debug, never an error', async () => {
    const run = { orgId: ORG, runId: 'run-1', sessionId: 's-1' };
    const service = new SessionRunService({ resolvedView: async () => null } as never, {} as never);
    expect(await service.resolved(run as never)).to.deep.equal({ resolved: null, liveApplied: [], heldRequests: [], blockedRequests: [] });
    const res = { status: sinon.stub(), json: sinon.stub() };
    res.status.returns(res);
    const next = sinon.stub();
    const runs = { get: sinon.stub().resolves(run), resolved: sinon.stub().resolves({ resolved: null, liveApplied: [], heldRequests: [], blockedRequests: [] }) };
    await new AgentHarnessSessionController(runs as never, undefined, 'chat', undefined, { logger }).resolved(
      { user: { orgId: ORG, userId: USER }, params: { sessionId: 's-1', runId: 'run-1' } } as never,
      res as never,
      next as never,
    );
    expect(next.called).to.equal(false);
    expect(res.status.firstCall.args[0]).to.equal(200);
    expect(logged('run.resolved_unpinned')).to.deep.equal([{ level: 'debug', event: 'run.resolved_unpinned', orgId: ORG, sessionId: 's-1', runId: 'run-1' }]);
  });
});

const ORG_OF = (http: ChatTurnHarness): string => String(http.submit.firstCall.args[0].orgId);
const runIdOf = (http: ChatTurnHarness): string => {
  const accepted = http.lines.find(([, message]) => message === 'admission.accepted');
  return String(accepted?.[2]['runId']);
};
