/**
 * MA1 (QA37-I1, I4, I8, I9; rules C1..C5 of the design): a child spawn takes
 * the model its role sets in the root run's definition, in `off`, `shadow`
 * and `on` alike; a role's model the org forbids, that costs more than its
 * role's ceiling or that is no longer configured refuses that spawn and
 * nothing else. The real admission and child services over the in-memory
 * store; the production pinner over a configured-models document.
 */
import { expect } from 'chai';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { policySnapshotLoader } from '../../../src/modules/agent-harness/adapters/control-plane/policy-snapshot-loader';
import { ChildPinning } from '../../../src/modules/agent-harness/application/admission/child-pinning';
import { PolicyPinning } from '../../../src/modules/agent-harness/application/admission/policy-pinning';
import { ChildRunService, type SpawnChild } from '../../../src/modules/agent-harness/application/child-run-service';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { canonicalDigest, canonicalJson } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { runSpecSchema } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import type { PolicyLayer } from '../../../src/modules/agent-harness/contracts/generated/core/policy/policy-layer';
import type { AgentRunDefinition } from '../../../src/modules/agent-harness/domain/agent-definition';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { roleCostBound } from '../../../src/modules/agent-harness/domain/child-model';
import { pipeshub } from '../../../src/modules/agent-harness/domain/child-runs';
import { configuredModels } from '../../../src/modules/agent-harness/domain/configured-llms';
import { HarnessConflictError, HarnessGroupAccessRefusedError, HarnessModelAccessRefusedError, HarnessUnavailableError } from '../../../src/modules/agent-harness/domain/errors';
import { InMemoryGroupMembership } from '../../../src/modules/agent-harness/adapters/in-memory-group-membership';
import { FakeGroupAccess, FakeModelAccessPolicies } from './support/org-policy-fakes';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { releaseLayers, type PinShadowDimension } from '../../../src/modules/agent-harness/domain/pin-shadow';
import { PARENT_DIMENSIONS, PolicyResolver } from '../../../src/modules/agent-harness/domain/policy';
import {
  CHILD_MODELS_FEATURE,
  KNOWN_WORKER_FEATURES,
  missingFor,
  requirementsOf,
} from '../../../src/modules/agent-harness/domain/worker-compat';
import { currentWorker } from './support/worker-capabilities';
import { profileExecutor, runSpecResolver } from '../../../src/modules/agent-harness/domain/run-spec';
import {
  PolicyRunConfigPinner,
  prefixSources,
  ResolvedSnapshotCache,
  runSources,
  type ControlPlanePin,
  type PinSubject,
  type RunConfigPinner,
} from '../../../src/modules/control-plane';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { InMemoryConfiguredModels, testModelAccess, type TestModel } from './support/model-access';
import { HARNESS_ENABLED } from './support/rollout';
import { committingTx, InMemoryHeadRevisions } from '../control-plane/support/head-revisions-contract';
import { without } from './support/without';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const ORG = 'org-ma1';
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const MODES: ControlPlanePin[] = ['off', 'shadow', 'on'];

/** A: the agent's main model. B: the `researcher` role's. C: the `pricey` role's. */
const MODEL_A: TestModel = { provider: 'openAI', modelKey: 'model-1', modelNames: ['gpt-x'], isDefault: true };
const MODEL_B: TestModel = { provider: 'anthropic', modelKey: 'model-2', modelNames: ['claude-x'] };
const MODEL_C: TestModel = { provider: 'openAI', modelKey: 'model-3', modelNames: ['gpt-big'] };
const ROUTE_A = { provider: 'openai', model: 'gpt-x' };
const ROUTE_B = { provider: 'anthropic', model: 'claude-x' };
const PRICES: Record<string, number> = { 'gpt-x': 10, 'claude-x': 3, 'gpt-big': 40 };

const ROLE_MODELS: NonNullable<AgentRunDefinition['roleModels']> = {
  researcher: { modelKey: 'model-2', modelName: 'claude-x', provider: 'Anthropic' },
  pricey: { modelKey: 'model-3', modelName: 'gpt-big', provider: 'openAI' },
};
const definitionWith = (roleModels: AgentRunDefinition['roleModels']) => (agentKey: string): AgentRunDefinition =>
  agentDefinition({
    agentKey,
    maxChildren: 4,
    models: [agentDefinition().models[0]!],
    ...(roleModels !== undefined ? { roleModels } : {}),
  });

const RESEARCHER = { role: 'Researcher', toolNames: [] };
const WRITER = { role: 'writer', toolNames: [] };

interface Logged {
  event: string;
  meta: Record<string, unknown>;
}

describe('MA1 a child spawn and its role’s model (QA37-I1, I4, I8, I9)', () => {
  let store: InMemoryExecutionStore;
  let payloads: InMemoryPayloadStore;
  let heads: InMemoryHeadRevisions;
  let stored: Map<string, PolicyLayer>;
  let configured: TestModel[];
  let pinned: PinSubject[];
  let counted: PinShadowDimension[];
  let logged: Logged[];
  let modelReads: number;
  let admitted = 0;
  let pinner: RunConfigPinner;

  const logger = {
    debug: () => undefined,
    info: () => undefined,
    warn: (event: string, meta?: unknown) => void logged.push({ event, meta: (meta ?? {}) as Record<string, unknown> }),
    error: () => undefined,
  };
  const refusals = (): Array<Record<string, unknown>> =>
    logged.filter((line) => line.event === 'child.refused').map((line) => line.meta);

  /** `aiModels.llm` as the configuration store holds it. */
  const aiModels = () => ({
    llm: configured.map((model) => ({
      provider: model.provider,
      modelKey: model.modelKey,
      isDefault: model.isDefault === true,
      configuration: { model: model.modelNames.join(',') },
    })),
  });
  const llmSource = { read: async () => ((modelReads += 1), configuredModels(aiModels())) };

  /** A loop parent admitted and claimed in `mode`, ready to spawn. */
  const parentIn = async (
    mode: ControlPlanePin,
    definition: (agentKey: string) => AgentRunDefinition = definitionWith(ROLE_MODELS),
  ): Promise<HarnessRun> => {
    const step = new PolicyPinning({ mode, pinner, metrics: { mismatch: () => undefined }, logger });
    const outcome = await new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      agentAccess: grantingAccess(definition),
      executors: profileExecutor,
      policySteps: [step],
    }).submit({
      orgId: ORG,
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer t',
      idempotencyKey: `k-${mode}-${(admitted += 1)}`,
      deadlineMs: 600_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto', modelKey: 'model-1', modelName: 'gpt-x', modelProvider: 'openAI', modelFriendlyName: 'GPT X' } },
    });
    await step.idle();
    if (outcome.route !== 'durable') return expect.fail('expected a durable run');
    pinned = [];
    return claim(outcome.admission.run.runId);
  };

  const claim = async (runId: string): Promise<HarnessRun> =>
    (
      await new ExecutionService({
        store,
        journal: store.journal,
        journals: new JournalService(payloads, testCipher()),
        capabilities: { current: () => DURABLE },
        resolveSpec: runSpecResolver(profileExecutor),
      }).claim({ orgId: ORG, runId, workerId: 'w', attemptId: `a-${runId}`, leaseDurationMs: 60_000 })
    ).run;

  const pinningIn = (mode: ControlPlanePin): ChildPinning | null =>
    mode === 'off' ? null : new ChildPinning({ mode, pinner, metrics: { mismatch: (dimension) => void counted.push(dimension) }, logger });

  const service = (pinning: ChildPinning | null, overrides: Partial<ConstructorParameters<typeof ChildRunService>[0]> = {}) =>
    new ChildRunService({
      runs: store,
      children: store,
      journals: new JournalService(payloads, testCipher()),
      workspaces: null,
      logger,
      engineAccess: (subject) => testModelAccess({ models: new InMemoryConfiguredModels(configured) }).decide(subject),
      pinning,
      configuredLlms: llmSource,
      ...overrides,
    });

  const spawnWith = (svc: ChildRunService, parent: HarnessRun, request: Partial<SpawnChild> = {}) =>
    svc.spawn(
      { orgId: ORG, runId: parent.runId, workerId: 'w', attemptId: `a-${parent.runId}`, fence: parent.fence },
      { invocationId: 't1.0', goal: 'look it up', grants: {}, subAgent: RESEARCHER, ...request },
    );

  /** Spawns in `mode` and waits for the shadow pin, if any. */
  const spawnIn = async (mode: ControlPlanePin, parent: HarnessRun, request: Partial<SpawnChild> = {}) => {
    const pinning = pinningIn(mode);
    const spawned = await spawnWith(service(pinning), parent, request);
    await pinning?.idle();
    return spawned;
  };

  const refusedIn = async (mode: ControlPlanePin, parent: HarnessRun, request: Partial<SpawnChild> = {}): Promise<HarnessConflictError> => {
    const pinning = pinningIn(mode);
    const error = await spawnWith(service(pinning), parent, request).then(
      () => expect.fail('expected the spawn to be refused'),
      (thrown: unknown) => thrown,
    );
    await pinning?.idle();
    expect(error).to.be.instanceOf(HarnessConflictError);
    return error as HarnessConflictError;
  };

  /** What a spawn left behind: the child's model as its spec, input, pin and its parent's journal say it. */
  const outcomeOf = async (parent: HarnessRun, childRunId: string) => {
    const child = await store.readClaimSubject(ORG, childRunId);
    const spec = runSpecSchema.parse(child?.pinned?.spec);
    const events = await store.journal.readStream(ORG, parent.sessionId, 0, 1_000);
    const spawned = events.filter((event) => event.type === 'child.spawned' && event.data?.['childRunId'] === childRunId);
    expect(spawned).to.have.length(1);
    return {
      policy: spec.model.policy,
      extModel: pipeshub(spec)?.model ?? null,
      inputModel: child?.run.input?.model ?? null,
      spawnedModel: (spawned[0]?.data?.['model'] as unknown) ?? null,
      resolved: child?.resolved ?? null,
      // The spec without what is this tree's own (its ids, its admission's clock) and without the pin it
      // carries under `on` (`resolved`, asserted on its own), so two trees and two modes compare byte for byte.
      specBytes: [parent.runId, parent.sessionId, childRunId].reduce(
        (bytes, id, index) => bytes.split(id).join(`<id-${index}>`),
        canonicalJson({
          ...spec,
          budgets: { ...spec.budgets, calendarDeadline: null },
          extensions: { 'x.pipeshub': { ...pipeshub(spec), resolved: null } },
        }),
      ),
    };
  };

  const parentSpecDigest = async (parent: HarnessRun): Promise<string | undefined> =>
    (await store.readClaimSubject(ORG, parent.runId))?.pinned?.specDigest;
  const childrenOf = async (parent: HarnessRun): Promise<number> => (await store.get(ORG, parent.runId))?.children?.spawned ?? 0;
  const spawnedEvents = async (parent: HarnessRun): Promise<number> =>
    (await store.journal.readStream(ORG, parent.sessionId, 0, 1_000)).filter((event) => event.type === 'child.spawned').length;

  const newStore = (): void => {
    store = new InMemoryExecutionStore();
    store.controlPlaneHeads = (orgId) => heads.current(orgId);
  };

  beforeEach(() => {
    heads = new InMemoryHeadRevisions();
    newStore();
    payloads = new InMemoryPayloadStore();
    stored = new Map();
    configured = [MODEL_A, MODEL_B, MODEL_C];
    pinned = [];
    counted = [];
    logged = [];
    modelReads = 0;
    const inner = new PolicyRunConfigPinner({
      snapshots: new ResolvedSnapshotCache({
        load: policySnapshotLoader({ heads, layers: async () => stored, models: { read: async () => configuredModels(aiModels()) } }),
        heads,
      }),
      engine: new PolicyResolver(),
      effectiveSet: { resolve: () => ({ items: [], digest: canonicalDigest([]) }) },
      releases: releaseLayers(),
      prefix: prefixSources({ modelAliases: ['pipeshub:org-default'] }),
      run: runSources(PARENT_DIMENSIONS),
      parent: PARENT_DIMENSIONS,
      prices: (model) => PRICES[model] ?? null,
      roleBound: roleCostBound,
    });
    pinner = { route: (subject) => inner.route(subject), pin: async (subject, basis) => (pinned.push(subject), inner.pin(subject, basis)) };
  });

  /** The org's stored layer changes and its head moves, as a policy write does. */
  const orgLayer = (models: NonNullable<PolicyLayer['models']>): void => {
    stored.set(`org:${ORG}`, { schemaVersion: 1, scope: { kind: 'org' }, models });
    heads.commit(ORG);
  };

  describe('QA37-I1: a parent on A spawns a child whose role is set to B', () => {
    for (const mode of MODES) {
      it(`QA37-I1 ${mode}: the child’s spec, input and \`child.spawned\` name B; the parent’s spec is untouched`, async () => {
        const parent = await parentIn(mode);
        const before = await parentSpecDigest(parent);
        const spawned = await spawnIn(mode, parent);
        const child = await outcomeOf(parent, spawned.childRunId);
        expect(child.policy).to.deep.equal([ROUTE_B]);
        expect(child.extModel).to.deep.equal({ modelKey: 'model-2', modelName: 'claude-x' });
        expect(child.inputModel).to.deep.equal({ chatMode: 'auto', modelKey: 'model-2', modelName: 'claude-x', modelProvider: 'Anthropic' });
        expect(child.spawnedModel).to.deep.equal({ ...ROUTE_B, role: 'researcher' });
        expect(await parentSpecDigest(parent)).to.equal(before);
        const parentSpec = runSpecSchema.parse((await store.readClaimSubject(ORG, parent.runId))?.pinned?.spec);
        expect(parentSpec.model.policy).to.deep.equal([ROUTE_A]);
        expect(pipeshub(parentSpec)?.model).to.deep.equal({ modelKey: 'model-1', modelName: 'gpt-x' });
        expect(refusals()).to.deep.equal([]);
        expect(counted).to.deep.equal([]);
      });
    }

    it('QA37-I1: `off`, `shadow` and `on` give the child the same spec, input and event', async () => {
      const seen: unknown[] = [];
      for (const mode of MODES) {
        newStore();
        const parent = await parentIn(mode);
        const child = without(await outcomeOf(parent, (await spawnIn(mode, parent)).childRunId), 'resolved');
        seen.push(child);
      }
      expect(seen[1]).to.deep.equal(seen[0]);
      expect(seen[2]).to.deep.equal(seen[0]);
    });

    it('QA37-I1 on: the child’s pin names B as the request’s choice, with B’s configured entry; the key is the definition’s', async () => {
      const parent = await parentIn('on');
      const child = await outcomeOf(parent, (await spawnIn('on', parent)).childRunId);
      expect(child.resolved?.effectiveConfig.dimensions['models.primary']).to.deep.include({ value: 'anthropic:claude-x', setBy: 'request' });
      expect(child.resolved?.llm).to.deep.include({ modelKey: 'model-2', provider: 'anthropic', model: 'claude-x' });
      expect(child.extModel?.modelKey).to.equal(child.resolved?.llm?.modelKey);
      expect(child.extModel?.modelKey).to.equal(ROLE_MODELS['researcher']?.modelKey);
      expect(pinned.map((subject) => [subject.surface, subject.model, subject.roleKeys])).to.deep.equal([
        ['child', { modelKey: 'model-2', modelName: 'claude-x', provider: 'anthropic' }, ['researcher', 'default']],
      ]);
      const parentPin = (await store.readClaimSubject(ORG, parent.runId))?.resolved;
      expect(parentPin?.llm).to.deep.include({ modelKey: 'model-1', model: 'gpt-x' });
      expect(parentPin?.effectiveConfig.dimensions['models.primary']?.value).to.equal('openai:gpt-x');
    });

    it('QA37-I1 off, shadow: no pin is written for the child; shadow pins it in memory with the same request', async () => {
      for (const mode of ['off', 'shadow'] as const) {
        const parent = await parentIn(mode);
        const child = await outcomeOf(parent, (await spawnIn(mode, parent)).childRunId);
        expect(child.resolved).to.equal(null);
      }
      const asked = pinned.filter((subject) => subject.surface === 'child');
      expect(asked.map((subject) => [subject.request.models?.primary, subject.model, subject.parent !== undefined])).to.deep.equal([
        ['anthropic:claude-x', { modelKey: 'model-2', modelName: 'claude-x', provider: 'anthropic' }, true],
      ]);
    });
  });

  describe('QA37-K8 (D5): a worker that cannot fold `child.spawned.model` is not offered what reads it', () => {
    const features = async (runId: string): Promise<readonly string[]> =>
      requirementsOf((await store.get(ORG, runId)) ?? {}).features;
    const older = currentWorker({ features: KNOWN_WORKER_FEATURES.filter((feature) => feature !== CHILD_MODELS_FEATURE) });

    for (const mode of MODES) {
      it(`QA37-K8 ${mode}: a role-set child, its parent and the session's later runs require it`, async () => {
        const parent = await parentIn(mode);
        const roleSet = await spawnIn(mode, parent);
        expect(await features(roleSet.childRunId)).to.include(CHILD_MODELS_FEATURE);
        expect(await features(parent.runId)).to.include(CHILD_MODELS_FEATURE);
        const parentRun = (await store.get(ORG, parent.runId))!;
        expect(missingFor(parentRun, older)).to.deep.equal([`feature:${CHILD_MODELS_FEATURE}`]);
        expect(missingFor(parentRun, currentWorker())).to.deep.equal([]);
        // The event is on the session's stream: a later run of the session folds it too (SS1).
        const later = await spawnIn(mode, parent, { invocationId: 't1.1', subAgent: WRITER });
        expect(await features(later.childRunId)).to.include(CHILD_MODELS_FEATURE);
      });

      it(`QA37-K8 ${mode}: an inherited child requires nothing new, as before`, async () => {
        const parent = await parentIn(mode);
        const inherited = await spawnIn(mode, parent, { subAgent: WRITER });
        const second = await spawnIn(mode, parent, { invocationId: 't1.1', subAgent: WRITER });
        expect(await features(inherited.childRunId)).to.not.include(CHILD_MODELS_FEATURE);
        expect(await features(second.childRunId)).to.not.include(CHILD_MODELS_FEATURE);
        expect(await features(parent.runId)).to.not.include(CHILD_MODELS_FEATURE);
      });
    }
  });

  describe('QA37-U5 (C1, C5): inherit unless the role sets a model', () => {
    for (const mode of MODES) {
      it(`QA37-U5 ${mode} (C1): a role without a model takes its parent’s, and everything written is what an agent without role models writes`, async () => {
        const withRoles = await parentIn(mode);
        const inherited = await outcomeOf(withRoles, (await spawnIn(mode, withRoles, { subAgent: WRITER })).childRunId);
        expect(inherited.policy).to.deep.equal([ROUTE_A]);
        expect(inherited.extModel).to.deep.equal({ modelKey: 'model-1', modelName: 'gpt-x' });
        expect(inherited.spawnedModel).to.equal(null);
        expect(inherited.inputModel).to.deep.equal(withRoles.input?.model);
        expect(modelReads).to.equal(0);

        const plain = await parentIn(mode, definitionWith(undefined));
        const before = await outcomeOf(plain, (await spawnIn(mode, plain, { subAgent: WRITER })).childRunId);
        expect(inherited.specBytes).to.equal(before.specBytes);
        expect(inherited.inputModel).to.deep.equal(before.inputModel);
      });
    }

    it('QA37-U5 on (C1): an inheriting child’s pin holds its parent’s model, set by `parent`, and its request names none', async () => {
      const parent = await parentIn('on');
      const child = await outcomeOf(parent, (await spawnIn('on', parent, { subAgent: WRITER })).childRunId);
      expect(child.resolved?.effectiveConfig.dimensions['models.primary']).to.deep.include({ value: 'openai:gpt-x', setBy: 'parent' });
      expect(child.resolved?.llm).to.deep.include({ modelKey: 'model-1' });
      expect(pinned.map((subject) => [subject.request.models, subject.model, subject.roleKeys])).to.deep.equal([
        [undefined, undefined, ['writer', 'default']],
      ]);
    });

    for (const mode of MODES) {
      it(`QA37-U5 ${mode} (C5): a grandchild is decided by the root’s definition; without a role model it takes its own parent’s`, async () => {
        const root = await parentIn(mode);
        const pinning = pinningIn(mode);
        const svc = service(pinning);
        const first = await spawnWith(svc, root, { grants: { maxChildren: 2 } });
        const child = await claim(first.childRunId);
        // A role the root's definition names, asked two levels down: B, read from the root.
        const named = await spawnWith(svc, child, { invocationId: 't2.0', subAgent: { role: 'pricey', toolNames: [] } });
        // A role it does not name: its own parent's model (B), not the root's (A).
        const carried = await spawnWith(svc, child, { invocationId: 't2.1', subAgent: WRITER });
        await pinning?.idle();
        const read = async (childRunId: string) => {
          const subject = await store.readClaimSubject(ORG, childRunId);
          const spec = runSpecSchema.parse(subject?.pinned?.spec);
          return { policy: spec.model.policy, extModel: pipeshub(spec)?.model, inputKey: subject?.run.input?.model.modelKey };
        };
        expect(await read(named.childRunId)).to.deep.equal({
          policy: [{ provider: 'openai', model: 'gpt-big' }],
          extModel: { modelKey: 'model-3', modelName: 'gpt-big' },
          inputKey: 'model-3',
        });
        expect(await read(carried.childRunId)).to.deep.equal({
          policy: [ROUTE_B],
          extModel: { modelKey: 'model-2', modelName: 'claude-x' },
          inputKey: 'model-2',
        });
        const events = await store.journal.readStream(ORG, first.childRunId, 0, 1_000);
        expect(events.filter((event) => event.type === 'child.spawned').map((event) => event.data?.['model'] ?? null)).to.deep.equal([
          { provider: 'openai', model: 'gpt-big', role: 'pricey' },
          null,
        ]);
      });
    }

    it('QA37-U5: a root whose definition was erased has no role models: its children inherit', async () => {
      const parent = await parentIn('off');
      const erased = {
        get: (orgId: string, runId: string) => store.get(orgId, runId),
        readClaimSubject: async (orgId: string, runId: string) => {
          const subject = await store.readClaimSubject(orgId, runId);
          return subject === null ? null : { ...subject, agentDefinition: null };
        },
      };
      const spawned = await spawnWith(service(null, { runs: erased }), parent);
      const child = await outcomeOf(parent, spawned.childRunId);
      expect(child.policy).to.deep.equal([ROUTE_A]);
      expect(child.spawnedModel).to.equal(null);
    });
  });

  describe('QA37-I4: a role’s model that is not allowed refuses that spawn (C3)', () => {
    it('QA37-I4 on: the org’s routes leave B out: CHILD_GRANT_TOO_LARGE naming `models.roles`; no child, no `child.spawned`; the parent goes on', async () => {
      orgLayer({ routes: ['openai:gpt-x', 'openai:gpt-big'] });
      const parent = await parentIn('on');
      const parentPin = (await store.readClaimSubject(ORG, parent.runId))?.resolved;
      expect(parentPin?.effectiveConfig.dimensions['models.roles']?.value).to.deep.equal({ pricey: 'openai:gpt-big' });
      const refused = await refusedIn('on', parent);
      expect(refused.code).to.equal('CHILD_GRANT_TOO_LARGE');
      expect(refused.message).to.equal('The sub-agent role "researcher" is set to anthropic:claude-x, which the organization does not allow');
      expect(refusals().map((meta) => [meta['reason'], meta['dimension']])).to.deep.equal([['CP_OUTSIDE_CEILING', 'models.roles']]);
      expect(await childrenOf(parent)).to.equal(0);
      expect(await spawnedEvents(parent)).to.equal(0);
      expect(pinned).to.deep.equal([]);
      // Never the parent's model instead; and the parent still spawns what it may.
      const allowed = await outcomeOf(parent, (await spawnIn('on', parent, { invocationId: 't1.1', subAgent: WRITER })).childRunId);
      expect(allowed.policy).to.deep.equal([ROUTE_A]);
      const other = await outcomeOf(
        parent,
        (await spawnIn('on', parent, { invocationId: 't1.2', subAgent: { role: 'pricey', toolNames: [] } })).childRunId,
      );
      expect(other.policy).to.deep.equal([{ provider: 'openai', model: 'gpt-big' }]);
      expect(await childrenOf(parent)).to.equal(2);
    });

    it('QA37-I4 on: a role’s model removed from the configuration after the parent’s pin is refused as no longer configured', async () => {
      const parent = await parentIn('on');
      configured = [MODEL_A, MODEL_C];
      await heads.move(committingTx('any'));
      const refused = await refusedIn('on', parent);
      expect(refused.code).to.equal('CHILD_GRANT_TOO_LARGE');
      expect(refused.message).to.equal('The sub-agent role "researcher" is set to anthropic:claude-x, which is no longer configured');
      expect(refusals().map((meta) => [meta['reason'], meta['dimension']])).to.deep.equal([['CP_OUTSIDE_CEILING', 'models.roles']]);
      expect(await childrenOf(parent)).to.equal(0);
      expect(await spawnedEvents(parent)).to.equal(0);
    });

    for (const mode of ['off', 'shadow'] as const) {
      it(`QA37-I4 ${mode}: without a pin, a role’s model that is no longer configured refuses the spawn; nothing is written`, async () => {
        const parent = await parentIn(mode);
        configured = [MODEL_A, MODEL_C];
        const refused = await refusedIn(mode, parent);
        expect(refused.code).to.equal('CHILD_GRANT_TOO_LARGE');
        expect(refused.message).to.equal('The sub-agent role "researcher" is set to anthropic:claude-x, which is no longer configured');
        expect(refusals().map((meta) => [meta['reason'], meta['dimension']])).to.deep.equal([['CHILD_GRANT_TOO_LARGE', 'models.roles']]);
        expect(await childrenOf(parent)).to.equal(0);
        expect(await spawnedEvents(parent)).to.equal(0);
        expect(pinned.filter((subject) => subject.surface === 'child')).to.deep.equal([]);
      });
    }

    it('QA37-I4 off: the entry the definition names must still serve that model under that provider', async () => {
      const parent = await parentIn('off');
      configured = [MODEL_A, { ...MODEL_B, provider: 'bedrock' }, MODEL_C];
      expect((await refusedIn('off', parent)).message).to.contain('no longer configured');
      configured = [MODEL_A, { ...MODEL_B, modelNames: ['claude-y'] }, MODEL_C];
      expect((await refusedIn('off', parent, { invocationId: 't1.1' })).message).to.contain('no longer configured');
      expect(await childrenOf(parent)).to.equal(0);
    });

    it('QA37-I4 off: the configured models are read once for a role’s spawn, and a store that cannot be read refuses it unavailable', async () => {
      const parent = await parentIn('off');
      await spawnIn('off', parent);
      expect(modelReads).to.equal(1);
      const down = service(null, { configuredLlms: { read: async () => Promise.reject(new Error('kv down')) } });
      const error = await spawnWith(down, parent, { invocationId: 't1.1' }).catch((thrown: unknown) => thrown);
      expect(error).to.be.instanceOf(HarnessUnavailableError);
      expect(await childrenOf(parent)).to.equal(1);
    });

    it('QA37-I4 off: a deployment that wires no configured-models reader applies the role’s model unchecked (as the pin does when none is configured)', async () => {
      const parent = await parentIn('off');
      configured = [MODEL_A];
      const spawned = await spawnWith(service(null, { configuredLlms: null }), parent);
      expect((await outcomeOf(parent, spawned.childRunId)).policy).to.deep.equal([ROUTE_B]);
      configured = [];
      const none = await spawnIn('off', parent, { invocationId: 't1.1' });
      expect((await outcomeOf(parent, none.childRunId)).policy).to.deep.equal([ROUTE_B]);
    });
  });

  describe('QA37-U7 (C4): a cost ceiling for the role', () => {
    it('QA37-U7 on: a role’s model above its ceiling is refused naming `models.roleCostCeilings`; one within it is spawned', async () => {
      orgLayer({ roleCostCeilings: { pricey: 20, default: 100 } });
      const parent = await parentIn('on');
      const refused = await refusedIn('on', parent, { subAgent: { role: 'pricey', toolNames: [] } });
      expect(refused.code).to.equal('CHILD_GRANT_TOO_LARGE');
      expect(refused.message).to.equal(
        'The sub-agent role "pricey" would call openai:gpt-big, which is above the cost ceiling for that role or has no price',
      );
      expect(refusals().map((meta) => [meta['reason'], meta['dimension']])).to.deep.equal([['CP_OUTSIDE_CEILING', 'models.roleCostCeilings']]);
      expect(await childrenOf(parent)).to.equal(0);
      const within = await outcomeOf(parent, (await spawnIn('on', parent, { invocationId: 't1.1' })).childRunId);
      expect(within.policy).to.deep.equal([ROUTE_B]);
    });

    it('QA37-U7 on: an inherited model above the role’s ceiling is refused too, naming the parent’s model', async () => {
      orgLayer({ roleCostCeilings: { default: 5 } });
      const parent = await parentIn('on');
      const refused = await refusedIn('on', parent, { subAgent: WRITER });
      expect(refused.message).to.equal(
        'The sub-agent role "writer" would call openai:gpt-x, which is above the cost ceiling for that role or has no price',
      );
      expect(refusals().map((meta) => meta['dimension'])).to.deep.equal(['models.roleCostCeilings']);
      const cheap = await outcomeOf(parent, (await spawnIn('on', parent, { invocationId: 't1.1' })).childRunId);
      expect(cheap.policy).to.deep.equal([ROUTE_B]);
    });

    it('QA37-U7 off: no org dimension is read without a pin: the ceiling does not apply', async () => {
      orgLayer({ roleCostCeilings: { default: 1 } });
      const parent = await parentIn('off');
      const child = await outcomeOf(parent, (await spawnIn('off', parent, { subAgent: { role: 'pricey', toolNames: [] } })).childRunId);
      expect(child.policy).to.deep.equal([{ provider: 'openai', model: 'gpt-big' }]);
    });
  });

  describe('QA37-I8: shadow counts and never acts', () => {
    it('QA37-I8: a planted forbidden role model is counted once under `refused` and changes nothing the child gets', async () => {
      const allowedParent = await parentIn('shadow');
      const expected = await outcomeOf(allowedParent, (await spawnIn('shadow', allowedParent)).childRunId);
      expect(counted).to.deep.equal([]);

      orgLayer({ routes: ['openai:gpt-x', 'openai:gpt-big'] });
      const parent = await parentIn('shadow');
      logged = [];
      const child = await outcomeOf(parent, (await spawnIn('shadow', parent)).childRunId);
      expect(counted).to.deep.equal(['refused']);
      expect(logged.filter((line) => line.event === 'cp.shadow_mismatch').map((line) => [line.meta['surface'], line.meta['dimensions']])).to.deep.equal([
        ['child', ['refused']],
      ]);
      expect(refusals()).to.deep.equal([]);
      expect(child).to.deep.equal(expected);
      expect(child.policy).to.deep.equal([ROUTE_B]);
      expect(await childrenOf(parent)).to.equal(1);
    });

    it('QA37-I8: a role above its cost ceiling is counted once in shadow, and the spawn goes on', async () => {
      orgLayer({ roleCostCeilings: { pricey: 20 } });
      const parent = await parentIn('shadow');
      const spawned = await spawnIn('shadow', parent, { subAgent: { role: 'pricey', toolNames: [] } });
      expect(counted).to.deep.equal(['refused']);
      expect((await outcomeOf(parent, spawned.childRunId)).policy).to.deep.equal([{ provider: 'openai', model: 'gpt-big' }]);
    });
  });

  describe('QA37-I9: an engine child of a loop parent with a role model', () => {
    const ENGINE = { executor: { kind: 'engine' as const } };
    const engineProfiles = (): void => {
      stored.set(`org:${ORG}`, { schemaVersion: 1, scope: { kind: 'org' }, engineProfile: { limits: { maxSteps: 30 } } });
      heads.commit(ORG);
    };

    it('QA37-I9 on: its access route is decided for the role’s model, as the org configures it', async () => {
      engineProfiles();
      const parent = await parentIn('on');
      const child = await outcomeOf(parent, (await spawnIn('on', parent, ENGINE)).childRunId);
      expect(child.policy).to.have.length(1);
      expect(child.policy[0]).to.deep.include(ROUTE_B);
      expect(child.policy[0]?.access).to.deep.include({ route: 'gateway', accountKind: 'org_api_key' });
      expect(child.extModel).to.deep.equal({ modelKey: 'model-2', modelName: 'claude-x' });
      expect(child.spawnedModel).to.deep.equal({ ...ROUTE_B, role: 'researcher' });
      expect(child.resolved?.llm).to.deep.include({ modelKey: 'model-2' });
      expect(child.resolved?.effectiveConfig.dimensions['engineProfile.accountKinds']?.value).to.deep.equal(['org_api_key']);
    });

    it('QA37-I9 on: without a role model the access route is the parent’s model’s, as before', async () => {
      engineProfiles();
      const parent = await parentIn('on');
      const child = await outcomeOf(parent, (await spawnIn('on', parent, { ...ENGINE, subAgent: WRITER })).childRunId);
      expect(child.policy[0]).to.deep.include(ROUTE_A);
      expect(child.spawnedModel).to.equal(null);
    });

    it('QA37-I9 on: a vendor rule that refuses the role’s provider refuses the spawn; the parent’s provider is not asked instead', async () => {
      engineProfiles();
      const parent = await parentIn('on');
      const asked: Array<string | undefined> = [];
      const policies = new FakeModelAccessPolicies();
      await policies.write(ORG, { disabledRules: ['anthropic-api-key-org'], consumerPlans: false });
      const access = testModelAccess({ models: new InMemoryConfiguredModels(configured), policies });
      const svc = service(pinningIn('on'), { engineAccess: (subject) => (asked.push(subject.model.modelKey), access.decide(subject)) });
      const error = await spawnWith(svc, parent, ENGINE).catch((thrown: unknown) => thrown);
      expect(error).to.be.instanceOf(HarnessModelAccessRefusedError);
      expect(asked).to.deep.equal(['model-2']);
      expect(refusals().map((meta) => meta['dimension'])).to.deep.equal(['access']);
      expect(await childrenOf(parent)).to.equal(0);
      // The same rule leaves a child on the parent's (OpenAI) model alone.
      await spawnWith(svc, parent, { ...ENGINE, invocationId: 't1.1', subAgent: WRITER });
      expect(asked).to.deep.equal(['model-2', 'model-1']);
    });

    it('QA37-I9 on: a decision that lands on another route than the role’s is refused on `access`, never pinned', async () => {
      engineProfiles();
      const parent = await parentIn('on');
      const access = testModelAccess({ models: new InMemoryConfiguredModels(configured) });
      const svc = service(pinningIn('on'), {
        // An authority that answers for the parent's model whatever it is asked.
        engineAccess: (subject) => access.decide({ ...subject, model: { ...subject.model, modelKey: 'model-1', modelName: 'gpt-x' } }),
      });
      const error = await spawnWith(svc, parent, ENGINE).catch((thrown: unknown) => thrown);
      expect((error as HarnessConflictError).code).to.equal('CHILD_GRANT_TOO_LARGE');
      expect(refusals().map((meta) => [meta['dimension'], meta['items']])).to.deep.equal([['access', ['openai:gpt-x']]]);
      expect(await childrenOf(parent)).to.equal(0);
    });
  });
  describe('G-41: role models are held to the model groups (GRP-24..26)', () => {
    const PREMIUM = 'group-premium';
    let membership: InMemoryGroupMembership;
    let limited: Record<string, string[]>;
    const groupAccess = () =>
      new FakeGroupAccess({ policies: { read: async () => ({ models: limited }) }, membership, models: new InMemoryConfiguredModels(configured) });

    beforeEach(() => {
      membership = new InMemoryGroupMembership();
      limited = { 'model-2': [PREMIUM] };
    });

    for (const mode of MODES) {
      it(`GRP-24 ${mode}: a sub-agent role on a model outside the person's groups calls the run's own model instead`, async () => {
        const parent = await parentIn(mode);
        const pinning = pinningIn(mode);
        const spawned = await spawnWith(service(pinning, { groupAccess: groupAccess() }), parent);
        await pinning?.idle();
        const child = await outcomeOf(parent, spawned.childRunId);
        expect(child.policy).to.deep.equal([ROUTE_A]);
        expect(child.spawnedModel).to.equal(null);
      });
    }

    it('GRP-25: a member of the group gets the role\'s own model', async () => {
      membership.add(ORG, PREMIUM, 'user-1');
      const parent = await parentIn('off');
      const child = await outcomeOf(parent, (await spawnWith(service(null, { groupAccess: groupAccess() }), parent)).childRunId);
      expect(child.policy).to.deep.equal([ROUTE_B]);
    });

    it('GRP-26: when the run\'s own model is refused too, the spawn is refused in plain words; an unreadable policy refuses it', async () => {
      const parent = await parentIn('off');
      limited = { 'model-1': [PREMIUM], 'model-2': [PREMIUM] };
      const refused = await spawnWith(service(null, { groupAccess: groupAccess() }), parent).then(
        () => expect.fail('expected the spawn to be refused'),
        (thrown: unknown) => thrown,
      );
      expect(refused).to.be.instanceOf(HarnessGroupAccessRefusedError);
      expect((refused as HarnessGroupAccessRefusedError).code).to.equal('MODEL_GROUP_REQUIRED');
      membership.down = true;
      limited = { 'model-2': [PREMIUM] };
      const down = await spawnWith(service(null, { groupAccess: groupAccess() }), parent).then(
        () => expect.fail('expected the spawn to be refused'),
        (thrown: unknown) => thrown,
      );
      expect(down).to.be.instanceOf(HarnessUnavailableError);
      expect(await childrenOf(parent)).to.equal(0);
    });
  });
});
