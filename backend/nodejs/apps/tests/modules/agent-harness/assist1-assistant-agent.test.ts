/**
 * ASSIST1: the built-in assistant's Agent mode on the durable harness. Routing
 * and admission through a real RunAdmissionService (AS1-6, AS1-7), the pin of
 * the system assistant definition (AS1-8) and its replay (AS1-11).
 */
import { expect } from 'chai';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { HarnessRollout } from '../../../src/modules/agent-harness/application/harness-rollout';
import { RolloutGate } from '../../../src/modules/agent-harness/application/admission/rollout-gate';
import { RolloutRoutingDecider } from '../../../src/modules/agent-harness/application/rollout-routing';
import type { RoutingSubject } from '../../../src/modules/agent-harness/ports/routing-decider';
import {
  RunAdmissionService,
  SubmitDurableRun,
} from '../../../src/modules/agent-harness/application/run-admission-service';
import { runSpecSchema } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { canonicalDigest } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { AgentRunDefinition } from '../../../src/modules/agent-harness/domain/agent-definition';
import {
  capabilitySnapshot,
  LEAST_CAPABILITIES,
} from '../../../src/modules/agent-harness/domain/capabilities';
import { HarnessRun, RunInputSnapshot } from '../../../src/modules/agent-harness/domain/execution';
import {
  ASSISTANT_AGENT_DEFINITION_KEY,
  assistantModesWith,
  HarnessRolloutSettings,
} from '../../../src/modules/agent-harness/domain/rollout';
import { resolveRunSpec, SpecSubject } from '../../../src/modules/agent-harness/domain/run-spec';
import { ASSISTANT_AGENT_FEATURE } from '../../../src/modules/agent-harness/domain/worker-compat';
import { AgentUseRequest } from '../../../src/modules/agent-harness/ports/agent-access';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { agentDefinition } from './support/agent-definition';

const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);

/** What Python answers for the system definition: the assistant's authenticated tools, its knowledge, no models. */
const systemDefinition = (overrides: Partial<AgentRunDefinition> = {}): AgentRunDefinition =>
  agentDefinition({
    agentKey: ASSISTANT_AGENT_DEFINITION_KEY,
    definitionVersion: 1,
    models: [],
    defaultReasoningEffort: null,
    skills: [],
    webSearch: 'duckduckgo',
    ...overrides,
  });

const settings = (agentModeOn: boolean): HarnessRolloutSettings => ({
  infrastructureReady: true,
  killSwitch: false,
  orgEnabled: true,
  modelFamilies: null,
  assistantModes: assistantModesWith(agentModeOn),
});

const input = (chatMode = 'agent', overrides: Partial<RunInputSnapshot> = {}): RunInputSnapshot => ({
  query: 'q',
  recordIds: [],
  filters: {},
  attachments: [],
  model: { chatMode },
  ...overrides,
});

let turns = 0;
const turn = (overrides: Partial<SubmitDurableRun> = {}): SubmitDurableRun => ({
  orgId: 'org-1',
  actorId: 'user-1',
  idempotencyKey: `as1-${String((turns += 1))}`,
  firstSubmission: true,
  deadlineMs: 60_000,
  authorization: 'Bearer caller',
  input: input(),
  ...overrides,
});

interface World {
  admissions: RunAdmissionService;
  asked: AgentUseRequest[];
  logged: Array<{ event: string; meta: Record<string, unknown> }>;
}

const world = (agentModeOn: boolean, definition = systemDefinition()): World => {
  const asked: AgentUseRequest[] = [];
  const logged: World['logged'] = [];
  const logger: HarnessLogger = {
    debug: () => undefined,
    info: (event: string, meta: Record<string, unknown> = {}) => {
      logged.push({ event, meta });
    },
    warn: () => undefined,
    error: () => undefined,
  };
  const admissions = new RunAdmissionService({
    store: new InMemoryExecutionStore({ now: () => 1_000 }),
    rollout: new HarnessRollout({ read: async () => settings(agentModeOn) }, { resolve: async () => null }),
    clock: { now: () => 1_000 },
    agentAccess: {
      authorizeRun: async (request) => {
        asked.push(request);
        return request.agentKey === ASSISTANT_AGENT_DEFINITION_KEY ? definition : null;
      },
    },
    logger,
  });
  return { admissions, asked, logged };
};

describe('ASSIST1: the assistant\'s Agent mode on the durable harness', () => {
  describe('AS1-6: the option on, the org on', () => {
    it('admits a durable run with the system definition, asked with the caller\'s own credential', async () => {
      const { admissions, asked } = world(true);
      const outcome = await admissions.submit(turn({ durableRequested: true }));
      expect(outcome.route).to.equal('durable');
      if (outcome.route !== 'durable') return;
      expect(asked).to.have.length(1);
      expect(asked[0]).to.include({
        agentKey: ASSISTANT_AGENT_DEFINITION_KEY,
        authorization: 'Bearer caller',
        actorId: 'user-1',
      });
      const run = outcome.admission.run;
      expect(run.agentKey, 'the conversation stays the assistant\'s').to.equal(undefined);
      expect(run.requires?.features).to.include(ASSISTANT_AGENT_FEATURE);
      expect(run.requires?.features).to.include('chat');
    });

    it('MCPLIVE: a new conversation with an MCP server whose tools were discovered is admitted durable with them', async () => {
      const digest = `sha256:${'a'.repeat(64)}`;
      const named = systemDefinition({
        mcpServers: [{
          instanceId: 'mcp-1', name: 'notes', typeId: 'notes',
          tools: [{ name: 'lookup_note', fullName: 'mcp_notes_lookup_note', descriptorDigest: digest }],
        }],
      });
      const { admissions, logged } = world(true, named);
      const outcome = await admissions.submit(turn({ durableRequested: true }));
      expect(outcome.route).to.equal('durable');
      expect(logged.map((entry) => entry.event)).not.to.include('run.assistant_mcp_unavailable');
    });

    it('MCPLIVE: a new conversation whose MCP server could not be named is admitted durable with the notice, never legacy', async () => {
      const unlisted = systemDefinition({
        mcpServers: [{ instanceId: 'mcp-1', name: 'notes', typeId: 'notes', tools: [] }],
      });
      const { admissions, logged } = world(true, unlisted);
      const outcome = await admissions.submit(turn({ durableRequested: true }));
      expect(outcome.route).to.equal('durable');
      const line = logged.find((entry) => entry.event === 'run.assistant_mcp_unavailable');
      expect(line?.meta).to.include({ reason: 'mcp_tools_unnamed', servers: 1, orgId: 'org-1' });
    });

    it('logs nothing about MCP when every server names its approved tools', async () => {
      const { admissions, logged } = world(true);
      await admissions.submit(turn({ durableRequested: true }));
      expect(logged.map((entry) => entry.event)).not.to.include('run.assistant_mcp_unavailable');
    });
  });

  describe('AS1-7: anything else stays on today\'s loop and asks no authority', () => {
    const cases: Array<[string, boolean, Partial<SubmitDurableRun>]> = [
      ['option off, org on', true, {}],
      ['option on, org off', false, { durableRequested: true }],
      ['option off, org off', false, {}],
      ['an API PAT client without the field, org on', true, { via: 'pat' }],
      ['an OAuth client without the field, org on', true, { via: 'oauth_app' }],
    ];
    for (const [name, orgOn, overrides] of cases) {
      it(name, async () => {
        const { admissions, asked } = world(orgOn);
        const outcome = await admissions.submit(turn(overrides));
        expect(outcome).to.deep.include({ route: 'legacy' });
        if (outcome.route === 'legacy') expect(outcome.rollout.reason).to.equal('chat_mode_not_supported');
        expect(asked).to.deep.equal([]);
      });
    }

    it('an API PAT client that asks is treated like the web app', async () => {
      const { admissions } = world(true);
      const outcome = await admissions.submit(turn({ via: 'pat', durableRequested: true }));
      expect(outcome.route).to.equal('durable');
    });

    it('the search modes are unchanged by the option and authorize nothing', async () => {
      for (const durableRequested of [false, true]) {
        const { admissions, asked } = world(true);
        const outcome = await admissions.submit(
          turn({ input: input('internal_search'), ...(durableRequested ? { durableRequested } : {}) }),
        );
        expect(outcome.route).to.equal('durable');
        if (outcome.route === 'durable') {
          expect(outcome.admission.run.requires?.features).not.to.include(ASSISTANT_AGENT_FEATURE);
        }
        expect(asked).to.deep.equal([]);
      }
    });
  });

  describe('AS1-8: the pin names the system definition and only narrows it', () => {
    const run = (snapshot: RunInputSnapshot): HarnessRun =>
      ({
        runId: 'run-1',
        orgId: 'org-1',
        sessionId: 'session-1',
        actorId: 'user-1',
        harnessMajor: 2,
        status: 'queued',
        revision: 0,
        fence: 0,
        absoluteDeadlineAt: Date.UTC(2026, 9, 1, 12),
        createdAt: Date.UTC(2026, 9, 1, 11),
        updatedAt: Date.UTC(2026, 9, 1, 11),
        input: snapshot,
      }) as HarnessRun;
    const subject = (snapshot: RunInputSnapshot): SpecSubject => ({
      run: run(snapshot),
      agentDefinition: systemDefinition(),
      via: 'session',
    });
    const pipeshub = (spec: ReturnType<typeof resolveRunSpec>) =>
      spec.extensions?.['x.pipeshub'] as Record<string, any>;

    it('records the definition like an agent\'s, in the loop mode legacy runs Agent mode in', () => {
      const spec = resolveRunSpec(subject(input()), DURABLE);
      expect(runSpecSchema.safeParse(spec).success).to.equal(true);
      expect(pipeshub(spec)['agent']).to.deep.equal({
        agentKey: ASSISTANT_AGENT_DEFINITION_KEY,
        definitionVersion: 1,
        mode: 'quick',
      });
      expect(pipeshub(spec)['chat']).to.equal(undefined);
      expect(spec.budgets.maxChildren).to.equal(0);
      expect(spec.model.policy).to.deep.equal([{ provider: 'pipeshub', model: 'org-default' }]);
      expect(spec.grants.tools.map((grant) => grant.toolId)).to.deep.equal([
        'jira@inst-42/create_issue',
        'jira@inst-42/search_issues',
        'mcp@mcp-9/list_prs',
        'web@duckduckgo/web_search',
      ]);
      expect(pipeshub(spec)['knowledge']).to.deep.equal({ apps: ['app-drive'], kb: ['kb-1'], strictScope: true });
    });

    it('narrows by the client\'s tools, capabilities and filters, never widens', () => {
      const spec = resolveRunSpec(
        subject(
          input('agent', {
            tools: ['jira.search_issues', 'jira.not_granted'],
            agentCapabilities: { internalSearch: true, webSearch: false },
            filters: { apps: ['app-drive', 'app-other'], kb: [] },
          }),
        ),
        DURABLE,
      );
      expect(spec.grants.tools.map((grant) => grant.toolId)).to.deep.equal(['jira@inst-42/search_issues']);
      expect(pipeshub(spec)['knowledge']).to.deep.equal({ apps: ['app-drive'], kb: [], strictScope: true });
    });

    it('takes the request\'s model, the assistant having none of its own', () => {
      const spec = resolveRunSpec(
        subject(input('agent', { model: { chatMode: 'agent', modelKey: 'k1', modelName: 'gpt-x', modelProvider: 'openAI' } })),
        DURABLE,
      );
      expect(spec.model.policy).to.deep.equal([{ provider: 'openai', model: 'gpt-x' }]);
    });

    it('AS1-11: resolving the same subject again pins the same spec (a resumed attempt reads the same grants)', () => {
      const first = resolveRunSpec(subject(input()), DURABLE);
      const again = resolveRunSpec(subject(input()), DURABLE);
      expect(canonicalDigest(again)).to.equal(canonicalDigest(first));
    });
  });
});

describe('AS1-17: a conversation keeps one executor for every participant', () => {
  const decider = (agentModeOn: boolean, durable: boolean | null) =>
    new RolloutRoutingDecider({
      rollout: new HarnessRollout({ read: async () => settings(agentModeOn) }, { resolve: async () => null }),
      sessions: {
        placement: async () => (durable === null ? null : { inProject: false, durable }),
      },
    });
  const subject = (overrides: Partial<RoutingSubject> = {}): RoutingSubject => ({
    orgId: 'org-1',
    actorId: 'collaborator-1',
    sessionId: 'session-1',
    model: { chatMode: 'agent' },
    ...overrides,
  });

  it('a collaborator\'s reply without the field in a durable conversation is durable', async () => {
    const { routing } = await decider(true, true).decide(subject());
    expect(routing).to.include({ useHarness: true, reason: 'enabled' });
  });

  it('the queued next run, drained through the admission gate without the field, is durable', async () => {
    const gate = new RolloutGate(decider(true, true));
    const decided = await gate.decide({ orgId: 'org-1', actorId: 'collaborator-1', sessionId: 'session-1', input: input() });
    expect(decided.routing).to.include({ useHarness: true });
  });

  it('an old-loop conversation stays on today\'s loop even when a turn asks', async () => {
    const { routing } = await decider(true, false).decide(subject({ actorId: 'user-1', durableRequested: true }));
    expect(routing).to.include({ useHarness: false, reason: 'chat_mode_not_supported' });
  });

  it('a conversation the sender holds no place in is never moved by what the turn asks', async () => {
    const { routing } = await decider(true, null).decide(subject({ durableRequested: true }));
    expect(routing).to.include({ useHarness: false, reason: 'chat_mode_not_supported' });
  });

  it('with the org setting off, even a durable conversation\'s Agent-mode turn is today\'s loop', async () => {
    const { routing } = await decider(false, true).decide(subject());
    expect(routing).to.include({ useHarness: false, reason: 'chat_mode_not_supported' });
  });

  it('a new conversation still follows the turn\'s own field', async () => {
    const asked = await decider(true, null).decide(subject({ sessionId: undefined, durableRequested: true }));
    const notAsked = await decider(true, null).decide(subject({ sessionId: undefined }));
    expect(asked.routing.useHarness).to.equal(true);
    expect(notAsked.routing.useHarness).to.equal(false);
  });
});
