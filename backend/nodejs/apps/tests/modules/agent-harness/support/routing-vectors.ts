/**
 * QA-8: `contracts/harness/vectors/routing.json` read in Node. The file states
 * how a chat turn is routed (an ordered first-match table plus named vectors);
 * `routingVectorSuite` holds a `RoutingDecider` adapter to it end to end,
 * through a real `RunAdmissionService`, so LAT1's rollout adapter and CPD-10's
 * resolved-configuration adapter answer the same table.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect } from 'chai';
import fc from 'fast-check';
import { InMemoryExecutionStore } from '../../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { HarnessRollout } from '../../../../src/modules/agent-harness/application/harness-rollout';
import { ResolvedRoutingDecider } from '../../../../src/modules/agent-harness/application/resolved-routing';
import { RolloutRoutingDecider } from '../../../../src/modules/agent-harness/application/rollout-routing';
import { RoutingFactsReader } from '../../../../src/modules/agent-harness/application/routing-facts';
import { policySnapshotLoader } from '../../../../src/modules/agent-harness/adapters/control-plane/policy-snapshot-loader';
import { configuredModels } from '../../../../src/modules/agent-harness/domain/configured-llms';
import { releaseLayers } from '../../../../src/modules/agent-harness/domain/pin-shadow';
import { PARENT_DIMENSIONS, PolicyResolver } from '../../../../src/modules/agent-harness/domain/policy';
import {
  CanonicalEffectiveSetResolver,
  PolicyRunConfigPinner,
  prefixSources,
  ResolvedSnapshotCache,
  runSources,
  type HeadRevisions,
  type RunConfigPinner,
} from '../../../../src/modules/control-plane';
import { InMemoryHeadRevisions } from '../../control-plane/support/head-revisions-contract';
import {
  RunAdmissionService,
  type RunAdmissionServiceOptions,
  SubmitDurableRun,
} from '../../../../src/modules/agent-harness/application/run-admission-service';
import {
  capabilitySnapshot,
  LEAST_CAPABILITIES,
} from '../../../../src/modules/agent-harness/domain/capabilities';
import { engineRef, RELEASE_ENGINE_PROFILES } from '../../../../src/modules/agent-harness/domain/engine-profiles';
import { HarnessUnavailableError } from '../../../../src/modules/agent-harness/domain/errors';
import { assistantModesWith, HarnessRolloutSettings } from '../../../../src/modules/agent-harness/domain/rollout';
import { AGENT_CODE_FEATURE, LIVENESS_FEATURE } from '../../../../src/modules/agent-harness/domain/worker-compat';
import { agentDefinition, grantingAccess } from './agent-definition';
import { testModelAccess } from './model-access';
import type { RoutingDeciderFactory, RoutingWorld } from './routing-decider-contract';

export const FACTORS = [
  'surface',
  'kind',
  'chatMode',
  'rollout',
  'modelFamilies',
  'project',
  'conversation',
  'worker',
  'workerFeatures',
  'engine',
  'engineProfile',
  'harnessRequest',
] as const;
export type RoutingFactor = (typeof FACTORS)[number];
export type RoutingInput = Record<RoutingFactor, string>;
type Selector = Partial<Record<RoutingFactor, string[]>>;

export interface RoutingAnswer {
  route: 'durable' | 'engine' | 'legacy' | 'refused';
  reason?: string;
  code?: string;
  agentCode?: boolean;
  codeTask?: boolean;
}

interface RoutingVectorFile {
  factors: Array<{ id: RoutingFactor; levels: string[] }>;
  constraints: Array<{ id: string; if: Selector; then: Selector }>;
  defaults: RoutingInput;
  rules: Array<{ id: string; when: Selector; then: RoutingAnswer }>;
  vectors: Array<{ id: string; input: Partial<RoutingInput>; expected: RoutingAnswer }>;
}

export const ROUTING_VECTORS: RoutingVectorFile = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../../contracts/harness/vectors/routing.json'), 'utf8'),
) as RoutingVectorFile;

export const levelsOf = (factor: RoutingFactor): string[] =>
  ROUTING_VECTORS.factors.find((f) => f.id === factor)?.levels ?? [];

const matches = (input: RoutingInput, selector: Selector): boolean =>
  Object.entries(selector).every(([factor, levels]) => levels?.includes(input[factor as RoutingFactor]));

export const inputOf = (partial: Partial<RoutingInput>): RoutingInput => ({ ...ROUTING_VECTORS.defaults, ...partial });

export const isValidInput = (input: RoutingInput): boolean =>
  FACTORS.every((factor) => levelsOf(factor).includes(input[factor])) &&
  ROUTING_VECTORS.constraints.every((c) => !matches(input, c.if) || matches(input, c.then));

/** The table's answer: the first rule that matches; null when none does. */
export function tableAnswer(input: RoutingInput): { rule: string; answer: RoutingAnswer } | null {
  const rule = ROUTING_VECTORS.rules.find((r) => matches(input, r.when));
  return rule === undefined ? null : { rule: rule.id, answer: rule.then };
}

/** A chat mode's answer on the default world: `durable` or the legacy reason (LAT1's contract lists). */
export const modeExpectation = (kind: 'agent' | 'assistant', chatMode: string): string => {
  const answer = tableAnswer(inputOf({ kind, chatMode }))?.answer;
  return answer?.route === 'durable' ? 'durable' : String(answer?.reason ?? answer?.route);
};

/** Every valid input, drawn uniformly over the factors and filtered by the constraints. */
export const arbitraryInput: fc.Arbitrary<RoutingInput> = fc
  .record(
    Object.fromEntries(FACTORS.map((factor) => [factor, fc.constantFrom(...levelsOf(factor))])) as Record<
      RoutingFactor,
      fc.Arbitrary<string>
    >,
  )
  .filter(isValidInput);

const MODEL_FAMILY = 'gpt';
const FAMILY_LISTS: Record<string, { list: string[] | null; resolved: string | null }> = {
  any: { list: null, resolved: MODEL_FAMILY },
  allowed: { list: [MODEL_FAMILY], resolved: MODEL_FAMILY },
  not_allowed: { list: ['claude'], resolved: MODEL_FAMILY },
  unresolved: { list: [MODEL_FAMILY], resolved: null },
  none: { list: [], resolved: MODEL_FAMILY },
};

export const PROJECT = '64b7f0c2a1b2c3d4e5f60799';
const ORG = 'org-1';
const ACTOR = 'user-1';
const AGENT = 'agent-1';

/** The world LAT1's `RoutingDeciderFactory` builds a decider from, for one input. */
export function routingWorld(input: RoutingInput): RoutingWorld {
  const families = FAMILY_LISTS[input.modelFamilies] as { list: string[] | null; resolved: string | null };
  const settings: HarnessRolloutSettings = {
    infrastructureReady: input.rollout !== 'infrastructure_unavailable',
    killSwitch: input.rollout === 'kill_switch',
    orgEnabled: input.rollout !== 'org_disabled',
    modelFamilies: families.list,
    // Agent mode comes with the harness (CTL-5).
    assistantModes: assistantModesWith(true),
  };
  return {
    settings,
    modelFamily: families.resolved,
    workerLive: input.worker === 'live',
    ...(input.conversation !== 'new'
      ? {
          conversation: {
            durable: input.conversation === 'durable',
            ...(input.project === 'record' ? { projectId: PROJECT } : {}),
          },
        }
      : {}),
  };
}

/** LAT1's adapter (today's rollout and the conversation's placement) over a world. */
export const rolloutRoutingFromWorld: RoutingDeciderFactory = (world) =>
  new RolloutRoutingDecider({
    rollout: new HarnessRollout({ read: async () => world.settings }, { resolve: async () => world.modelFamily ?? null }),
    sessions: {
      placement: async () =>
        world.conversation === undefined
          ? null
          : {
              inProject: world.conversation.projectId !== undefined,
              ...(world.conversation.projectId !== undefined ? { projectId: world.conversation.projectId } : {}),
              durable: world.conversation.durable,
            },
    },
    liveness: { live: async () => world.workerLive },
  });

/** The configured models of a world: its one LLM, the default, served by the family the turn's model resolves to. */
const aiModelsOf = (world: RoutingWorld): unknown => ({
  llm: world.modelFamily == null ? [] : [{ modelKey: 'model-1', provider: world.modelFamily, isDefault: true, configuration: { model: 'm' } }],
});

/** The control plane over a world: the production pinner, snapshot cache and loader on in-memory heads. */
export function controlPlaneOfWorld(world: RoutingWorld, heads: HeadRevisions = new InMemoryHeadRevisions()) {
  const snapshots = new ResolvedSnapshotCache({
    load: policySnapshotLoader({
      heads,
      rollout: { read: async () => world.settings },
      models: { read: async () => configuredModels(aiModelsOf(world)) },
    }),
    heads,
  });
  const pinner: RunConfigPinner = new PolicyRunConfigPinner({
    snapshots,
    engine: new PolicyResolver(),
    effectiveSet: new CanonicalEffectiveSetResolver(),
    releases: releaseLayers(),
    prefix: prefixSources(),
    run: runSources(PARENT_DIMENSIONS),
    parent: PARENT_DIMENSIONS,
  });
  return { heads, snapshots, pinner };
}

/** What the server knows of a world's turn beside policy: this process, the conversation's record, the workers. */
export const factsOfWorld = (world: RoutingWorld): RoutingFactsReader =>
  new RoutingFactsReader({
    infrastructureReady: () => world.settings.infrastructureReady,
    sessions: {
      placement: async () =>
        world.conversation === undefined
          ? null
          : {
              inProject: world.conversation.projectId !== undefined,
              ...(world.conversation.projectId !== undefined ? { projectId: world.conversation.projectId } : {}),
              durable: world.conversation.durable,
            },
    },
    liveness: { live: async () => world.workerLive },
  });

/** CPD-10's adapter (the resolved run configuration) over a world. */
export const resolvedRoutingFromWorld: RoutingDeciderFactory = (world) =>
  new ResolvedRoutingDecider({ pinner: controlPlaneOfWorld(world).pinner, facts: factsOfWorld(world) });

const PROVED = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true, 'sandbox.isolation': 'container' }, 1);
const releaseEngine = (kind: string) => {
  const profile = RELEASE_ENGINE_PROFILES.find((p) => p.engine.kind === kind);
  if (profile === undefined) throw new Error(`no release engine ${kind}`);
  return profile.engine;
};
const otherEngine = (kind: string): string =>
  RELEASE_ENGINE_PROFILES.map((p) => p.engine.kind).find((k) => k !== kind) as string;

const SURFACES: Record<string, Pick<SubmitDurableRun, 'surface' | 'via' | 'trigger'>> = {
  web: { surface: 'web', via: 'session' },
  api_pat: { surface: 'web', via: 'pat' },
  api_oauth: { surface: 'web', via: 'oauth_app' },
  slack: { surface: 'slack', via: 'service' },
  mcp: { surface: 'mcp', via: 'pat' },
  schedule: { via: 'trigger', trigger: { scheduleId: 's-1', firingId: 'f-1', slotKey: 'slot-1' } },
};

let turns = 0;

/** The turn an input describes, as the chat routes or the scheduler submit it. */
const turnOf = (input: RoutingInput, sessionId: string | undefined): SubmitDurableRun => ({
  orgId: ORG,
  actorId: ACTOR,
  ...(input.kind === 'agent' ? { agentKey: AGENT } : {}),
  authorization: 'Bearer caller',
  ...(sessionId !== undefined ? { sessionId } : {}),
  ...(input.project === 'request' ? { projectId: PROJECT } : {}),
  ...(input.harnessRequest === 'durable' ? { durableRequested: true } : {}),
  ...(input.harnessRequest === 'legacy' ? { legacyRequested: true } : {}),
  ...SURFACES[input.surface],
  idempotencyKey: `qa8-${String((turns += 1))}`,
  firstSubmission: true,
  deadlineMs: 60_000,
  input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: input.chatMode } },
});

/** An existing conversation in the store, its first run ended, so the next turn starts a run of its own. */
async function existingSession(store: InMemoryExecutionStore, input: RoutingInput): Promise<string> {
  const seeded = await new RunAdmissionService({
    store,
    rollout: { decide: async () => ({ useHarness: true, harnessMajor: 2, reason: 'enabled' }), decideForOrg: async () => ({ useHarness: true, harnessMajor: 2, reason: 'enabled', harnessOn: true, resumeInFlightRuns: true }) },
    clock: { now: () => 1_000 },
    agentAccess: grantingAccess((agentKey) => agentDefinition({ agentKey })),
  }).submit(turnOf({ ...input, chatMode: input.kind === 'agent' ? 'quick' : 'internal_search', project: 'none', surface: 'web', harnessRequest: 'none' }, undefined));
  if (seeded.route !== 'durable') throw new Error('the seed turn was not admitted');
  const run = seeded.admission.run;
  await store.control({
    orgId: ORG,
    runId: run.runId,
    action: 'cancel',
    commandId: `end-${run.runId}`,
    dispatchCommandId: `end-dispatch-${run.runId}`,
    expectedRevision: run.revision,
  });
  return seeded.admission.sessionId;
}

/**
 * What admission answers for `input` with the decider `make` builds: the route
 * of the run it wrote, today's loop with its reason, or the refusal's code.
 * Anything else (an error without a code) is thrown: no answer.
 */
export async function admissionAnswer(
  input: RoutingInput,
  make: RoutingDeciderFactory,
  /** Steps after admission's policy steps, given the world (CPD-5's shadow pinning). */
  policySteps?: (world: RoutingWorld, input: RoutingInput) => RunAdmissionServiceOptions['policySteps'],
): Promise<RoutingAnswer> {
  const store = new InMemoryExecutionStore({ now: () => 1_000 });
  const world = routingWorld(input);
  const sessionId = input.conversation === 'new' ? undefined : await existingSession(store, input);
  const engine = input.engine === 'none' ? null : input.engine;
  const featureLive = async (feature: string) =>
    input.worker === 'live' && (feature !== AGENT_CODE_FEATURE || input.workerFeatures === 'all');
  const admissions = new RunAdmissionService({
    store,
    rollout: new HarnessRollout({ read: async () => world.settings }, { resolve: async () => world.modelFamily ?? null }),
    routing: make(world),
    clock: { now: () => 1_000 },
    // An agent's engine is its definition's; the assistant's Agent mode takes the person's or the org's (ENGPICK).
    agentAccess: grantingAccess((agentKey) =>
      agentDefinition({
        agentKey,
        ...(engine !== null && input.kind === 'agent' ? { engineProfile: { engines: [engine] } } : {}),
      }),
    ),
    // The actor seeded the conversation: an engine hand-off in it is the owner's.
    roles: { roleOf: async () => 'owner' as const },
    codingEngine: {
      forTurn: async () =>
        engine !== null && input.kind === 'assistant'
          ? { engine, source: 'org' as const }
          : { engine: null, source: 'none' as const },
    },
    inline: { hint: async () => undefined },
    engineProfiles: {
      read: async () => {
        if (input.engineProfile === 'unreadable') throw new Error('config store down');
        return { engines: [input.engineProfile === 'widens' && engine !== null ? otherEngine(engine) : (engine ?? 'opencode')] };
      },
    },
    engineAvailability: {
      unavailable: async () =>
        new Set(input.engineProfile === 'image_missing' && engine !== null ? [engineRef(releaseEngine(engine))] : []),
    },
    modelAccess: testModelAccess(),
    capabilities: { current: () => PROVED },
    workerFeatures: {
      live: async (feature) => featureLive(feature),
      metrics: { live: () => undefined, withheld: () => undefined, stranded: () => undefined },
    },
    liveness: { live: async () => featureLive(LIVENESS_FEATURE) },
    ...(policySteps !== undefined ? { policySteps: policySteps(world, input) } : {}),
  });
  let outcome;
  try {
    outcome = await admissions.submit(turnOf(input, sessionId));
  } catch (error) {
    if (error instanceof HarnessUnavailableError) return { route: 'refused', code: 'UNAVAILABLE' };
    const code = (error as { code?: unknown }).code;
    if (typeof code === 'string') return { route: 'refused', code };
    throw error;
  }
  if (outcome.route === 'legacy') return { route: 'legacy', reason: outcome.rollout.reason };
  const run = outcome.admission.run;
  // The inline claimer has no engine kernel: an engine run, and only one, is dispatched.
  if (run.profile === 'dispatched') return { route: 'engine' };
  const features = run.requires?.features ?? [];
  return {
    route: 'durable',
    agentCode: features.includes(AGENT_CODE_FEATURE),
    // CTL-6: the loop may hand off to an engine child (`x.pipeshub.codeTask`).
    ...(features.includes('code_task_tool' as never) ? { codeTask: true } : {}),
  };
}

/** QA8-1..QA8-3 for one adapter. */
export function routingVectorSuite(name: string, make: RoutingDeciderFactory, numRuns = 200): void {
  describe(`routing vectors: ${name}`, () => {
    for (const vector of ROUTING_VECTORS.vectors) {
      const input = inputOf(vector.input);
      it(`QA8-2 ${vector.id} ${JSON.stringify(vector.input)} → ${JSON.stringify(vector.expected)}`, async () => {
        expect(isValidInput(input), 'a valid input').to.equal(true);
        expect(await admissionAnswer(input, make)).to.deep.equal(vector.expected);
      });
    }

    it('QA8-3: every valid input has one answer, the same twice, and it is the table’s', async () => {
      await fc.assert(
        fc.asyncProperty(arbitraryInput, async (input) => {
          const first = await admissionAnswer(input, make);
          const second = await admissionAnswer(input, make);
          expect(second, 'deterministic').to.deep.equal(first);
          expect(first).to.deep.equal(tableAnswer(input)?.answer);
        }),
        { numRuns, seed: 20261010 },
      );
    });
  });
}
