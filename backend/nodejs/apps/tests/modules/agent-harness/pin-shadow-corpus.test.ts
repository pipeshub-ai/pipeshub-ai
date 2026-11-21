/**
 * IX-ADM-PIN-5 at component level (CPD-5): the shadow over the routing
 * corpus. Every named routing vector (QA-8) and random valid inputs over its
 * 11 factors (kind, mode, engine, engine ceiling, surface, conversation,
 * project, rollout, families, worker) go through the real admission service
 * with the production pinner in `shadow`, its snapshot the same world today's
 * readers see: the mismatch total must be 0.
 */
import { expect } from 'chai';
import { InMemoryConfigDocumentStore } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { workerApprovals } from '../../../src/modules/agent-harness/adapters/control-plane/org-policy-layers';
import { orgPolicyLayers } from './support/org-policy-layers';
import { ChildPinning } from '../../../src/modules/agent-harness/application/admission/child-pinning';
import { childEngineAuthority, ChildRunService } from '../../../src/modules/agent-harness/application/child-run-service';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import type { FlowRule } from '../../../src/modules/agent-harness/domain/approvals';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { configuredModels } from '../../../src/modules/agent-harness/domain/configured-llms';
import { profileExecutor, runSpecResolver } from '../../../src/modules/agent-harness/domain/run-spec';
import type { ControlPlanePin, ResolvedRunConfig } from '../../../src/modules/control-plane';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { testModelAccess } from './support/model-access';
import { HARNESS_ENABLED } from './support/rollout';
import { releaseLayers } from '../../../src/modules/agent-harness/domain/pin-shadow';
import fc from 'fast-check';
import { readdirSync, readFileSync } from 'fs';
import { join } from 'path';
import { policySnapshotLoader } from '../../../src/modules/agent-harness/adapters/control-plane/policy-snapshot-loader';
import { PolicyPinning } from '../../../src/modules/agent-harness/application/admission/policy-pinning';
import { canonicalDigest } from '../../../src/modules/agent-harness/contracts/canonical-json';
import type { PinShadowDimension } from '../../../src/modules/agent-harness/domain/pin-shadow';
import { PARENT_DIMENSIONS, PolicyResolver } from '../../../src/modules/agent-harness/domain/policy';
import { PolicyRunConfigPinner, prefixSources, ResolvedSnapshotCache, runSources } from '../../../src/modules/control-plane';
import { InMemoryHeadRevisions } from '../control-plane/support/head-revisions-contract';
import { ShadowRoutingDecider } from '../../../src/modules/agent-harness/application/resolved-routing';
import type { RoutingDeciderFactory, RoutingWorld } from './support/routing-decider-contract';
import {
  admissionAnswer,
  arbitraryInput,
  inputOf,
  isValidInput,
  resolvedRoutingFromWorld,
  rolloutRoutingFromWorld,
  ROUTING_VECTORS,
  type RoutingInput,
} from './support/routing-vectors';
import { FakeEngineCeilings } from './support/org-policy-fakes';

const REPLAY_SCENARIOS = join(__dirname, '../../../../../python/tests/harness_replay/scenarios');

describe('IX-ADM-PIN-5: the shadow over the routing corpus counts no mismatch', () => {
  const found: Array<[string, PinShadowDimension]> = [];
  const steps: PolicyPinning[] = [];

  const shadow = (label: string, planted: Record<string, unknown> = {}) => (world: RoutingWorld, input: RoutingInput) => {
    const heads = new InMemoryHeadRevisions();
    const engine = input.engine === 'none' ? null : input.engine;
    // What today's engine ceiling read answers in this world, as the org's stored layer: `widens` admits another
    // released engine only (CTL-6: the turn then runs on the loop without code_task, and pins as a loop turn does).
    const other = engine === 'codex' ? 'opencode' : 'codex';
    const ceiling = { engines: [input.engineProfile === 'widens' ? other : (engine ?? 'opencode')], ...planted };
    const step = new PolicyPinning({
      mode: 'shadow',
      pinner: new PolicyRunConfigPinner({
        snapshots: new ResolvedSnapshotCache({
          load: policySnapshotLoader({
            heads,
            layers: async (orgId) =>
              new Map([[`org:${orgId}`, { schemaVersion: 1 as const, scope: { kind: 'org' as const }, engineProfile: ceiling }]]),
            rollout: { read: async () => world.settings },
          }),
          heads,
        }),
        engine: new PolicyResolver(),
        effectiveSet: { resolve: () => ({ items: [], digest: canonicalDigest([]) }) },
        releases: releaseLayers(),
        prefix: prefixSources(),
          run: runSources(PARENT_DIMENSIONS),
        parent: PARENT_DIMENSIONS,
      }),
      metrics: { mismatch: (dimension) => void found.push([label, dimension]) },
      logger: { debug: () => undefined, info: () => undefined, warn: () => undefined, error: () => undefined },
    });
    steps.push(step);
    return [step];
  };

  const settle = async (): Promise<void> => {
    await Promise.all(steps.map((step) => step.idle()));
  };

  it('every named routing vector', async () => {
    for (const vector of ROUTING_VECTORS.vectors) {
      await admissionAnswer(inputOf(vector.input), rolloutRoutingFromWorld, shadow(vector.id));
    }
    await settle();
    expect(found).to.deep.equal([]);
  });

  it('300 random valid inputs', async () => {
    await fc.assert(
      fc.asyncProperty(arbitraryInput.filter(isValidInput), async (input) => {
        await admissionAnswer(input, rolloutRoutingFromWorld, shadow(JSON.stringify(input)));
      }),
      { numRuns: 300, seed: 20261011 },
    );
    await settle();
    expect(found).to.deep.equal([]);
  });

  it('every scenario of the replay corpus (backend/python/tests/harness_replay/scenarios), as its turn is admitted', async () => {
    const scenarios = readdirSync(REPLAY_SCENARIOS).filter((file) => file.endsWith('.yaml'));
    expect(scenarios.length).to.be.at.least(7);
    let durable = 0;
    for (const file of scenarios) {
      const text = readFileSync(join(REPLAY_SCENARIOS, file), 'utf8');
      const chatMode = /^chat_mode:\s*(\S+)/m.exec(text)?.[1];
      const kind = /^route:\s*chat\b/m.test(text) ? 'assistant' : 'agent';
      expect(chatMode, file).to.be.a('string');
      const answer = await admissionAnswer(inputOf({ kind, chatMode: chatMode as string }), rolloutRoutingFromWorld, shadow(file));
      if (answer.route === 'durable') durable += 1;
    }
    await settle();
    // planExecute stays on today's loop and is never pinned; every other scenario is a durable turn.
    expect(durable).to.equal(scenarios.length - 1);
    expect(found).to.deep.equal([]);
  });

  it('the corpus sees a planted difference: an engine ceiling only the pin knows of', async () => {
    for (const vector of ROUTING_VECTORS.vectors) {
      await admissionAnswer(inputOf(vector.input), rolloutRoutingFromWorld, shadow(vector.id, { limits: { maxSteps: 7 } }));
    }
    await settle();
    expect(found.length).to.be.greaterThan(0);
    expect(new Set(found.map(([, dimension]) => dimension))).to.deep.equal(new Set(['engine_profile']));
    found.length = 0;
  });
});

describe('PSC-C6 (CPD10-6): routing in shadow over the routing vectors and the replay corpus counts no mismatch', () => {
  const found: Array<[string, PinShadowDimension]> = [];
  const deciders: ShadowRoutingDecider[] = [];

  /** Today's decider answering, the resolved one compared after; `seen`: the world the control plane reads. */
  const shadowed =
    (label: string, seen: (world: RoutingWorld) => RoutingWorld = (world) => world): RoutingDeciderFactory =>
    (world) => {
      const decider = new ShadowRoutingDecider({
        today: rolloutRoutingFromWorld(world),
        resolved: resolvedRoutingFromWorld(seen(world)),
        metrics: { mismatch: (dimension) => void found.push([label, dimension]) },
        logger: { debug: () => undefined, info: () => undefined, warn: () => undefined, error: () => undefined },
      });
      deciders.push(decider);
      return decider;
    };

  const settle = async (): Promise<void> => {
    await Promise.all(deciders.map((decider) => decider.idle()));
  };

  it('every named routing vector, and the answers are today’s', async () => {
    for (const vector of ROUTING_VECTORS.vectors) {
      expect(await admissionAnswer(inputOf(vector.input), shadowed(vector.id)), vector.id).to.deep.equal(vector.expected);
    }
    await settle();
    expect(found).to.deep.equal([]);
    expect(deciders.length).to.be.at.least(ROUTING_VECTORS.vectors.length);
  });

  it('300 random valid inputs', async () => {
    await fc.assert(
      fc.asyncProperty(arbitraryInput.filter(isValidInput), async (input) => {
        await admissionAnswer(input, shadowed(JSON.stringify(input)));
      }),
      { numRuns: 300, seed: 20261012 },
    );
    await settle();
    expect(found).to.deep.equal([]);
  });

  it('every scenario of the replay corpus, as its turn is routed', async () => {
    const scenarios = readdirSync(REPLAY_SCENARIOS).filter((file) => file.endsWith('.yaml'));
    expect(scenarios.length).to.be.at.least(7);
    for (const file of scenarios) {
      const text = readFileSync(join(REPLAY_SCENARIOS, file), 'utf8');
      const chatMode = /^chat_mode:\s*(\S+)/m.exec(text)?.[1];
      const kind = /^route:\s*chat\b/m.test(text) ? 'assistant' : 'agent';
      await admissionAnswer(inputOf({ kind, chatMode: chatMode as string }), shadowed(file));
    }
    await settle();
    expect(found).to.deep.equal([]);
  });

  it('the corpus sees a planted difference: a kill switch only the control plane knows of', async () => {
    const killed = (world: RoutingWorld): RoutingWorld => ({ ...world, settings: { ...world.settings, killSwitch: true } });
    for (const vector of ROUTING_VECTORS.vectors) {
      expect(await admissionAnswer(inputOf(vector.input), shadowed(vector.id, killed)), 'never acted on').to.deep.equal(vector.expected);
    }
    await settle();
    expect(found.length).to.be.greaterThan(0);
    expect(new Set(found.map(([, dimension]) => dimension))).to.deep.equal(new Set(['routing']));
    // Every vector today's rollout does not already stop for the infrastructure or the switch differs.
    const differing = ROUTING_VECTORS.vectors.filter((vector) => !['infrastructure_unavailable', 'kill_switch'].includes(inputOf(vector.input).rollout));
    expect(new Set(found.map(([label]) => label))).to.deep.equal(new Set(differing.map((vector) => vector.id)));
    found.length = 0;
  });
});

type ChildKind = 'engine' | 'loop';

interface FamilyRow {
  id: string;
  /** What the row holds beside the release. */
  what: string;
  child: ChildKind;
  /** The approvals document: the deployment's rules, with or without a flow rule of its own. */
  approvals: unknown;
  stages: boolean;
  models: boolean;
}

const FAMILY_ORG = 'org-family';
const FLOW: FlowRule = { level: 'ask', above: 'public' };
const DOCUMENT_FLOW = { default: { writes: 'ask', flow: { level: 'deny', above: 'private' } } };
const AI_MODELS = {
  llm: [
    { modelKey: 'model-1', provider: 'openAI', isDefault: true, configuration: { model: 'gpt-x' } },
    { modelKey: 'model-2', provider: 'anthropic', configuration: { model: 'claude-x' } },
  ],
};
const STAGES = { ENABLE_SKILLS: false, ENABLE_USER_CONTEXT: true, ENABLE_KNOWLEDGE_PUBLICATIONS: false };
const FAMILY_ROWS: FamilyRow[] = [
  { id: 'PSC-C1', what: 'two flow rules (the deployment’s environment and its approvals document), an engine child', child: 'engine', approvals: DOCUMENT_FLOW, stages: false, models: false },
  { id: 'PSC-C2', what: 'two flow rules, a loop child', child: 'loop', approvals: DOCUMENT_FLOW, stages: false, models: false },
  { id: 'PSC-C3', what: 'the Labs stages and a configured model, a loop child', child: 'loop', approvals: { default: { writes: 'ask' } }, stages: true, models: true },
];
const SILENT = { debug: () => undefined, info: () => undefined, warn: () => undefined, error: () => undefined };
const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);

/**
 * A pinned parent and its child through the real admission, claim and child
 * spawn over the in-memory store, with the production pinner, snapshot cache
 * and loader reading the documents today's readers read. The routing corpus
 * above stops at admission; the child path is a row of its own here.
 */
describe('IX-ADM-PIN-5: a pinned parent and its child count no mismatch, and the child is pinned under its parent', () => {
  const family = async (row: FamilyRow, mode: ControlPlanePin, plantedOrg: Record<string, unknown> = {}, childProfile?: Record<string, unknown>) => {
    const found: Array<[string, PinShadowDimension]> = [];
    const clock = { now: () => 1_000 };
    const store = new InMemoryExecutionStore(clock);
    const payloads = new InMemoryPayloadStore();
    const heads = new InMemoryHeadRevisions();
    const approvals = new InMemoryConfigDocumentStore<unknown>();
    await approvals.replace(null, row.approvals);
    const ceilings = new FakeEngineCeilings();
    const layers = orgPolicyLayers({ approvals, flow: FLOW });
    const pinner = new PolicyRunConfigPinner({
      snapshots: new ResolvedSnapshotCache({
        load: policySnapshotLoader({
          heads,
          // `plantedOrg`: what only the pin's snapshot holds, for the row that shows a difference is counted.
          layers: async (orgId) => {
            const read = new Map(await layers(orgId));
            const scope = `org:${orgId}`;
            if (Object.keys(plantedOrg).length > 0) {
              read.set(scope, { ...(read.get(scope) ?? { schemaVersion: 1, scope: { kind: 'org' } }), ...plantedOrg });
            }
            return read;
          },
          rollout: { read: async () => ({ infrastructureReady: true, killSwitch: false, orgEnabled: true, modelFamilies: null }) },
          ...(row.stages ? { stages: { read: async () => STAGES } } : {}),
          ...(row.models ? { models: { read: async () => configuredModels(AI_MODELS) } } : {}),
        }),
        heads,
      }),
      engine: new PolicyResolver(),
      effectiveSet: { resolve: () => ({ items: [], digest: canonicalDigest([]) }) },
      releases: releaseLayers(),
      prefix: prefixSources({ modelAliases: ['pipeshub:org-default'] }),
      run: runSources(PARENT_DIMENSIONS),
      parent: PARENT_DIMENSIONS,
    });
    const workerFeatures = { live: async () => true, metrics: { withheld: () => undefined } };
    const step = new PolicyPinning({
      mode,
      pinner,
      metrics: { mismatch: (dimension) => void found.push([`${row.id} parent`, dimension]) },
      logger: SILENT,
      workerFeatures,
      approvals: workerApprovals(approvals, FLOW),
    });
    const childPinning = new ChildPinning({
      mode,
      pinner,
      metrics: { mismatch: (dimension) => void found.push([`${row.id} child`, dimension]) },
      logger: SILENT,
      workerFeatures,
    });
    const outcome = await new RunAdmissionService({
      store,
      clock,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      agentAccess: grantingAccess((agentKey) => agentDefinition({ agentKey, maxChildren: 4 })),
      engineProfiles: ceilings,
      modelAccess: testModelAccess(),
      executors: profileExecutor,
      ...(mode === 'off' ? {} : { policySteps: [step] }),
    }).submit({
      orgId: FAMILY_ORG,
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer t',
      idempotencyKey: `k-${row.id}-${mode}`,
      deadlineMs: 600_000,
      // Not the deployment's default model: a child that looked the default up again would be pinned to another.
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto', modelKey: 'model-2', modelName: 'claude-x', modelProvider: 'anthropic' } },
    });
    await step.idle();
    if (outcome.route !== 'durable') return expect.fail('expected a durable parent');
    const parent = (
      await new ExecutionService({
        store,
        journal: store.journal,
        journals: new JournalService(payloads, testCipher()),
        capabilities: { current: () => DURABLE },
        resolveSpec: runSpecResolver(profileExecutor),
      }).claim({ orgId: FAMILY_ORG, runId: outcome.admission.run.runId, workerId: 'w', attemptId: 'a-1', leaseDurationMs: 60_000 })
    ).run;
    const spawned = await new ChildRunService({
      runs: store,
      children: store,
      journals: new JournalService(payloads, testCipher()),
      workspaces: null,
      engineAuthority: childEngineAuthority(ceilings),
      engineAccess: (subject) => testModelAccess().decide(subject),
      pinning: mode === 'off' ? null : childPinning,
    }).spawn(
      { orgId: FAMILY_ORG, runId: parent.runId, workerId: 'w', attemptId: 'a-1', fence: parent.fence },
      {
        invocationId: 't1.0',
        goal: 'do the part',
        grants: {},
        ...(row.child === 'engine' ? { executor: { kind: 'engine' as const, ...(childProfile !== undefined ? { profile: childProfile } : {}) } } : {}),
      },
    );
    await childPinning.idle();
    return {
      found,
      parent: (await store.readClaimSubject(FAMILY_ORG, parent.runId))!,
      child: (await store.readClaimSubject(FAMILY_ORG, spawned.childRunId))!,
    };
  };
  const dimensions = (pin: ResolvedRunConfig | null | undefined) => pin?.effectiveConfig.dimensions ?? {};

  for (const row of FAMILY_ROWS) {
    it(`${row.id} shadow: ${row.what}: nothing counted for parent or child, and the child is admitted as today`, async () => {
      const shadowed = await family(row, 'shadow');
      const today = await family(row, 'off');
      expect(shadowed.found).to.deep.equal([]);
      expect(shadowed.child.resolved).to.equal(null);
      expect(shadowed.child.pinned?.spec).to.deep.equal(today.child.pinned?.spec);
      expect(shadowed.child.run.requires).to.deep.equal(today.child.run.requires);
    });

    it(`${row.id} on: ${row.what}: the child is pinned under its parent, read by a worker, nothing refused`, async () => {
      const { parent, child } = await family(row, 'on');
      const [mine, theirs] = [dimensions(child.resolved), dimensions(parent.resolved)];
      expect(parent.run.requires?.features).to.include('resolved_config');
      expect(child.run.requires?.features).to.include('resolved_config');
      expect(child.resolved?.routing).to.deep.equal(parent.resolved?.routing);
      // release ⊓ parent ⊓ request: the parent's approvals as they were resolved, its stages and its model.
      expect(mine['approvals']?.value).to.deep.equal(theirs['approvals']?.value);
      expect((mine['approvals']?.value as { flow?: unknown[] }).flow).to.have.length(row.approvals === DOCUMENT_FLOW ? 2 : 1);
      const rollout = Object.keys(theirs).filter((path) => path.startsWith('rollout.'));
      expect(rollout.filter((path) => typeof theirs[path]?.value === 'boolean')).to.have.length(row.stages ? 5 : 2);
      expect(rollout).to.include.members(['rollout.durable.modes', 'rollout.durable.assistantModes', 'rollout.durable.modelFamilies']);
      for (const path of rollout) expect(mine[path], path).to.deep.equal(theirs[path]);
      expect(child.resolved?.llm).to.deep.equal(parent.resolved?.llm);
      expect(parent.resolved?.llm?.modelKey ?? null).to.equal(row.models ? 'model-2' : null);
      const inSpec = (child.pinned?.spec.extensions?.['x.pipeshub'] as { resolved?: { digest?: string } } | undefined)?.resolved;
      expect(inSpec?.digest).to.equal(child.resolved?.digest);
    });
  }

  it('PSC-C4: the child row sees a planted difference: a ceiling only the pin knows refuses the child in shadow, and the spawn stands', async () => {
    const row = { ...FAMILY_ROWS[0]!, id: 'PSC-C4' };
    const { found, child } = await family(row, 'shadow', { engineProfile: { limits: { maxSteps: 7 } } }, { limits: { maxSteps: 20 } });
    expect(found).to.deep.equal([['PSC-C4 child', 'refused']]);
    expect(child.run.status).to.equal('queued');
    expect(child.resolved).to.equal(null);
  });
});
