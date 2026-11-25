/**
 * CPD-6 S2: what the pin holds once a worker reads it. The F2 rule for
 * `resolved_config` (CPD6-10), `llm` and the configured-models ceiling from
 * the deployment's model document (CPD6-12, CPD6-13, CPD6-17, CPD6-18), the
 * deployment layer and the Labs stages through a real admission (CPD6-14,
 * CPD6-15), and approvals in the shadow comparison (CPD6-16). The real
 * admission service and in-memory store; the production pinner, snapshot
 * cache and loader over the control plane's in-memory heads.
 */
import { expect } from 'chai';
import { randomUUID } from 'crypto';
import { InMemoryConfigDocumentStore } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { PLATFORM_LABS_REGISTRY } from '../../../src/modules/agent-harness/adapters/configuration/platform-pin-sources';
import { workerApprovals } from '../../../src/modules/agent-harness/adapters/control-plane/org-policy-layers';
import { orgPolicyLayers } from './support/org-policy-layers';
import { policySnapshotLoader } from '../../../src/modules/agent-harness/adapters/control-plane/policy-snapshot-loader';
import { PolicyPinning } from '../../../src/modules/agent-harness/application/admission/policy-pinning';
import { RunAdmissionService, SubmitDurableRun } from '../../../src/modules/agent-harness/application/run-admission-service';
import { canonicalDigest } from '../../../src/modules/agent-harness/contracts/canonical-json';
import type { AgentRunDefinition } from '../../../src/modules/agent-harness/domain/agent-definition';
import type { FlowRule } from '../../../src/modules/agent-harness/domain/approvals';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { configuredLlms, configuredModels } from '../../../src/modules/agent-harness/domain/configured-llms';
import { labsStages } from '../../../src/modules/agent-harness/domain/labs-stages';
import { releaseLayers, type PinShadowDimension } from '../../../src/modules/agent-harness/domain/pin-shadow';
import { PARENT_DIMENSIONS, PolicyResolver } from '../../../src/modules/agent-harness/domain/policy';
import type { HarnessRolloutSettings } from '../../../src/modules/agent-harness/domain/rollout';
import { pinRunSpec, profileExecutor } from '../../../src/modules/agent-harness/domain/run-spec';
import {
  PolicyRunConfigPinner,
  prefixSources,
  ResolvedSnapshotCache,
  runSources,
  type ControlPlanePin,
} from '../../../src/modules/control-plane';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { assertNoCanary, canary } from './support/canary-scan';
import { testModelAccess } from './support/model-access';
import { HARNESS_ENABLED } from './support/rollout';
import { InMemoryPolicyStore } from '../control-plane/support/in-memory-stores';
import { readPolicyLayer } from '../../../src/modules/control-plane/domain/policy-layer';
import { committingTx, InMemoryHeadRevisions } from '../control-plane/support/head-revisions-contract';
import { FakeEngineCeilings, FakeModelAccessPolicies } from './support/org-policy-fakes';
import { EDITION_PLATFORM_FEATURE_FLAGS, EDITION_WORKER_STAGES } from '../../../src/edition';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const ORG = 'org-a';
const OTHER_ORG = 'org-b';
const LABS: HarnessRolloutSettings = { infrastructureReady: true, killSwitch: false, orgEnabled: true, modelFamilies: null };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const SECRET = canary('model-key');

const entry = (modelKey: string, provider: string, model: string, extra: Record<string, unknown> = {}) => ({
  modelKey,
  provider,
  configuration: { model, apiKey: SECRET, endpoint: `https://${SECRET}.example` },
  ...extra,
});
const TWO_MODELS = { llm: [entry('mk-1', 'openAI', 'gpt-x, gpt-y', { isDefault: true, isMultimodal: true, contextLength: 128_000 }), entry('mk-2', 'anthropic', 'claude-x')] };
const NO_MODELS: AgentRunDefinition = agentDefinition({ agentKey: 'a7', models: [] });

describe('CPD-6 the pin completed (CPD6-10, CPD6-12..18)', () => {
  let store: InMemoryExecutionStore;
  let heads: InMemoryHeadRevisions;
  let approvals: InMemoryConfigDocumentStore<unknown>;
  let aiModels: unknown;
  let settings: unknown;
  let flow: FlowRule | null;
  let live: boolean | undefined;
  let withheld: number;
  let counted: PinShadowDimension[];
  let definition: AgentRunDefinition;
  let cache: ResolvedSnapshotCache;
  let pinner: PolicyRunConfigPinner<unknown>;
  let policyStore: InMemoryPolicyStore | undefined;

  // One pinner for a test's admissions, as one process has: its folded prefixes outlive a pin.
  const pinning = (mode: ControlPlanePin): PolicyPinning => {
    const liveNow = live;
    return new PolicyPinning({
      mode,
      pinner,
      metrics: { mismatch: (dimension) => void counted.push(dimension) },
      logger: { debug: () => undefined, info: () => undefined, warn: () => undefined, error: () => undefined },
      refresh: (orgId) => cache.refresh(orgId),
      ...(liveNow !== undefined
        ? {
            workerFeatures: {
              // A live worker (`chat`) with or without the feature (ADR-CAP-01 D2).
              live: async (feature: string) => liveNow || feature === 'chat',
              metrics: { withheld: () => void (withheld += 1) },
            },
          }
        : {}),
      approvals: (orgId, agentKey) => workerApprovals(approvals, flow)(orgId, agentKey),
    });
  };
  const pinnerOver = (snapshots: ResolvedSnapshotCache): PolicyRunConfigPinner<unknown> =>
    new PolicyRunConfigPinner({
      snapshots,
      engine: new PolicyResolver(),
      effectiveSet: { resolve: () => ({ items: [], digest: canonicalDigest([]) }) },
      releases: releaseLayers(),
      prefix: prefixSources({ modelAliases: ['pipeshub:org-default'] }),
      run: runSources(PARENT_DIMENSIONS),
      parent: PARENT_DIMENSIONS,
    }) as PolicyRunConfigPinner<unknown>;

  const submit = async (mode: ControlPlanePin, request: Partial<SubmitDurableRun> = {}) => {
    const step = pinning(mode);
    const outcome = await new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      agentAccess: grantingAccess(() => definition),
      engineProfiles: new FakeEngineCeilings(),
      modelAccess: testModelAccess({
        policies: new FakeModelAccessPolicies(),
      }),
      executors: profileExecutor,
      policySteps: [step],
      repin: (context) => step.repin(context),
    }).submit({
      orgId: ORG,
      actorId: 'u3',
      agentKey: 'a7',
      authorization: 'Bearer u3',
      idempotencyKey: randomUUID(),
      deadlineMs: 600_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
      ...request,
    });
    await step.idle();
    if (outcome.route !== 'durable') return expect.fail('expected a durable admission');
    return outcome.admission;
  };
  const pinOf = async (request: Partial<SubmitDurableRun> = {}) => {
    const admitted = await submit('on', request);
    const subject = await store.readClaimSubject(admitted.run.orgId, admitted.run.runId);
    return { admitted, subject: subject!, pin: subject!.resolved! };
  };
  const refusal = (request: Partial<SubmitDurableRun> = {}): Promise<Error> =>
    submit('on', request).then(
      () => expect.fail('expected the admission to be refused'),
      (error: unknown) => error as Error,
    );
  const dimension = (pin: { effectiveConfig: { dimensions: Record<string, { value: unknown; setBy: string }> } }, path: string) =>
    pin.effectiveConfig.dimensions[path];
  const model = (modelKey?: string, modelName?: string, modelProvider?: string) => ({
    chatMode: 'auto',
    ...(modelKey !== undefined ? { modelKey } : {}),
    ...(modelName !== undefined ? { modelName } : {}),
    ...(modelProvider !== undefined ? { modelProvider } : {}),
  });
  const asking = (modelKey?: string, modelName?: string, modelProvider?: string): Partial<SubmitDurableRun> => ({
    input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: model(modelKey, modelName, modelProvider) },
  });

  beforeEach(() => {
    store = new InMemoryExecutionStore();
    heads = new InMemoryHeadRevisions();
    store.controlPlaneHeads = (orgId) => heads.current(orgId);
    approvals = new InMemoryConfigDocumentStore<unknown>();
    aiModels = null;
    settings = {};
    flow = null;
    live = true;
    withheld = 0;
    counted = [];
    definition = NO_MODELS;
    policyStore = undefined;
    cache = new ResolvedSnapshotCache({
      load: policySnapshotLoader({
        heads,
        layers: (orgId) => orgPolicyLayers({ approvals, flow, ...(policyStore ? { store: policyStore } : {}) })(orgId),
        rollout: { read: async () => LABS },
        stages: { read: async () => labsStages(settings, PLATFORM_LABS_REGISTRY) },
        models: { read: async () => configuredModels(aiModels) },
      }),
      heads,
    });
    pinner = pinnerOver(cache);
  });

  describe('the F2 rule for resolved_config (CPD6-10)', () => {
    const features = (admitted: Awaited<ReturnType<typeof submit>>) => admitted.run.requires?.features ?? [];

    it('a live worker advertises it: the run requires it and its spec carries the pin', async () => {
      const { admitted, subject, pin } = await pinOf();
      expect(features(admitted)).to.include('resolved_config');
      const spec = pinRunSpec(subject, DURABLE).spec;
      expect((spec.extensions?.['x.pipeshub'] as { resolved?: { digest: string } }).resolved?.digest).to.equal(pin.digest);
      expect(withheld).to.equal(0);
    });

    it('none does: admitted without it, counted, the pin on the run and not in the spec', async () => {
      live = false;
      const { admitted, subject, pin } = await pinOf();
      expect(features(admitted)).to.not.include('resolved_config');
      expect(withheld).to.equal(1);
      expect(pin.digest).to.be.a('string');
      expect(pinRunSpec(subject, DURABLE).spec.extensions?.['x.pipeshub']).to.not.have.property('resolved');
    });

    it('no availability wired: required, as agent_code is', async () => {
      live = undefined;
      expect(features((await pinOf()).admitted)).to.include('resolved_config');
    });

    it('off and shadow never require it', async () => {
      for (const mode of ['off', 'shadow'] as const) {
        expect(features(await submit(mode))).to.not.include('resolved_config');
      }
      expect(withheld).to.equal(0);
    });
  });

  describe('llm (CPD6-12)', () => {
    beforeEach(() => {
      aiModels = TWO_MODELS;
    });

    it('a run that names no model pins the default entry on its first name', async () => {
      const { pin } = await pinOf();
      expect(pin.llm).to.deep.include({ modelKey: 'mk-1', provider: 'openai', model: 'gpt-x' });
      expect(dimension(pin, 'models.primary')?.value).to.equal('pipeshub:org-default');
    });

    it('a run that names a key and a name the entry lists pins exactly them', async () => {
      const { pin } = await pinOf(asking('mk-1', 'gpt-y', 'openAI'));
      expect(pin.llm).to.deep.include({ modelKey: 'mk-1', provider: 'openai', model: 'gpt-y' });
      expect(dimension(pin, 'models.primary')?.value).to.equal('openai:gpt-y');
    });

    it('a client that names a key alone gets the entry’s first name, its route named as configured', async () => {
      const { pin } = await pinOf(asking('mk-2'));
      expect(pin.llm).to.deep.include({ modelKey: 'mk-2', provider: 'anthropic', model: 'claude-x' });
      expect(dimension(pin, 'models.primary')).to.include({ value: 'anthropic:claude-x', setBy: 'request' });
    });

    it('no default marked: the first entry; no model configured: null and no ceiling', async () => {
      aiModels = { llm: [entry('mk-9', 'openAI', 'gpt-z')] };
      expect((await pinOf()).pin.llm?.modelKey).to.equal('mk-9');
      aiModels = null;
      await heads.move(committingTx(ORG));
      const { pin } = await pinOf(asking('mk-1', 'gpt-x', 'openAI'));
      expect(pin.llm).to.equal(null);
      expect(dimension(pin, 'models.routes')).to.equal(undefined);
    });

    it('the pin holds no member of an entry’s configuration, and the digest does not move with them', async () => {
      const { pin } = await pinOf();
      assertNoCanary('the pin', pin, { secrets: [SECRET] });
      expect(Object.keys(pin.llm ?? {}).sort()).to.deep.equal(['configDigest', 'model', 'modelKey', 'provider']);
      const rotated = { llm: [{ ...TWO_MODELS.llm[0], configuration: { model: 'gpt-x, gpt-y', apiKey: 'another' } }] };
      expect(configuredLlms(rotated)[0]?.configDigest).to.equal(pin.llm?.configDigest);
      const repointed = { llm: [{ ...TWO_MODELS.llm[0], configuration: { model: 'gpt-z' } }] };
      expect(configuredLlms(repointed)[0]?.configDigest).to.not.equal(pin.llm?.configDigest);
    });

    it('entries that are not models are not pinned', () => {
      expect(configuredLlms({ llm: ['x', { provider: 'openAI' }, { modelKey: 'k', provider: 7 }, entry('ok', 'openAI', '')] })).to.deep.equal([
        { modelKey: 'ok', provider: 'openai', modelNames: [], isDefault: false, configDigest: configuredLlms({ llm: [entry('ok', 'openAI', '')] })[0]!.configDigest },
      ]);
      expect(configuredLlms({ llm: 'none' })).to.deep.equal([]);
      expect(configuredLlms(null)).to.deep.equal([]);
    });
  });

  describe('a save of the deployment’s models reaches every org’s next pin (CPD6-13, CPD6-17)', () => {
    it('org B pins the model org A’s admin saved, though only A’s head and the deployment’s revision moved', async () => {
      aiModels = TWO_MODELS;
      const before = await pinOf({ orgId: OTHER_ORG });
      expect(before.pin.llm?.model).to.equal('gpt-x');
      const readsBefore = heads.reads;

      aiModels = { llm: [entry('mk-1', 'openAI', 'gpt-next', { isDefault: true })] };
      heads.commit(ORG);
      await heads.move(committingTx(ORG));

      const after = await pinOf({ orgId: OTHER_ORG });
      expect(after.pin.llm).to.deep.include({ modelKey: 'mk-1', model: 'gpt-next' });
      expect(after.pin.headRevision, 'org B’s own head did not move').to.equal(before.pin.headRevision);
      expect(dimension(after.pin, 'models.routes')?.value).to.deep.equal(['openai:gpt-next', 'pipeshub:org-default']);
      expect(heads.reads - readsBefore, 'the reload: the cache’s check, which the loader stamps with (PERFE2-9)').to.equal(1);
      await pinOf({ orgId: OTHER_ORG });
      expect(heads.reads - readsBefore, 'one head read per pin after it: the deployment’s revision rides with the org’s').to.equal(2);
    });

    it('with neither revision moved the models are not read again', async () => {
      aiModels = TWO_MODELS;
      await pinOf();
      aiModels = { llm: [] };
      expect((await pinOf()).pin.llm?.model).to.equal('gpt-x');
    });
  });

  describe('the configured-models ceiling (CPD6-17, CPD6-18)', () => {
    beforeEach(() => {
      aiModels = TWO_MODELS;
    });

    it('models.routes is every configured model and the default alias, set by the org layer', async () => {
      const { pin } = await pinOf();
      expect(dimension(pin, 'models.routes')).to.deep.include({
        value: ['anthropic:claude-x', 'openai:gpt-x', 'openai:gpt-y', 'pipeshub:org-default'],
        setBy: 'org',
      });
    });

    it('a key that is not configured is refused, naming models.primary', async () => {
      const refused = await refusal(asking('mk-gone', 'gpt-x', 'openAI'));
      expect(refused).to.have.property('name', 'HarnessForbiddenError');
      expect(refused.message).to.include('CP_OUTSIDE_CEILING');
    });

    it('a name its entry does not list is refused, never the entry’s first name', async () => {
      const refused = await refusal(asking('mk-1', 'gpt-unlisted', 'openAI'));
      expect(refused.message).to.include('CP_OUTSIDE_CEILING');
    });

    it('in shadow the same choice is counted as refused and the run is admitted', async () => {
      await submit('shadow', asking('mk-gone', 'gpt-x', 'openAI'));
      expect(counted).to.deep.equal(['refused']);
    });

    it('an agent that grants a model the deployment dropped runs on the ones it still has', async () => {
      definition = agentDefinition({ agentKey: 'a7' });
      aiModels = { llm: [entry('model-2', 'anthropic', 'claude-x')] };
      const { pin } = await pinOf(asking('model-2', 'claude-x'));
      expect(dimension(pin, 'models.routes')).to.deep.include({ value: ['anthropic:claude-x'], setBy: 'agent' });
      expect(pin.llm?.model).to.equal('claude-x');
      const refused = await refusal(asking('model-1', 'gpt-x'));
      expect(refused.message).to.include('CP_OUTSIDE_CEILING');
    });

    it('an agent none of whose models is configured is refused: its default names no configured model', async () => {
      definition = agentDefinition({ agentKey: 'a7' });
      aiModels = { llm: [entry('mk-9', 'openAI', 'gpt-z')] };
      const refused = await refusal();
      expect(refused.message).to.include('CP_OUTSIDE_CEILING');
    });

    it('the org’s stored models.routes narrows the ceiling and never widens it', async () => {
      await approvals.replace(null, {});
      cache = new ResolvedSnapshotCache({
        load: policySnapshotLoader({
          heads,
          layers: async () =>
            new Map([[`org:${ORG}`, { schemaVersion: 1, scope: { kind: 'org' }, models: { routes: ['openai:gpt-y', 'pipeshub:org-default', 'vendor:not-configured'] } }]]),
          rollout: { read: async () => LABS },
          models: { read: async () => configuredModels(aiModels) },
        }),
        heads,
      });
      pinner = pinnerOver(cache);
      const { pin } = await pinOf(asking('mk-1', 'gpt-y'));
      expect(dimension(pin, 'models.routes')?.value).to.deep.equal(['openai:gpt-y', 'pipeshub:org-default']);
      expect((await refusal(asking('mk-2'))).message).to.include('CP_OUTSIDE_CEILING');
    });

    it('a route the host verified against a model account sits inside the ceiling for that run only (A1)', async () => {
      const subject = (verified: boolean) => ({
        orgId: ORG,
        actorId: 'u3',
        agentKey: null,
        projectId: null,
        chatMode: 'auto',
        surface: 'web' as const,
        request: { schemaVersion: 1 as const, scope: { kind: 'request' as const }, models: { primary: 'vendor:own-key-model' } },
        ...(verified ? { verifiedRoutes: ['vendor:own-key-model'] } : {}),
      });
      expect(await pinner.pin(subject(false))).to.include({ code: 'CP_OUTSIDE_CEILING', dimension: 'models.primary' });
      const pinned = await pinner.pin(subject(true));
      if ('code' in pinned) return expect.fail(pinned.code);
      expect(dimension(pinned, 'models.primary')?.value).to.equal('vendor:own-key-model');
      expect(dimension(pinned, 'models.routes')?.value).to.include('vendor:own-key-model');
      expect(await pinner.pin(subject(false)), 'the next run of the same org does not inherit it').to.include({ code: 'CP_OUTSIDE_CEILING' });
    });

    it('today and the pin name the same model for a client that left its provider out: nothing counted', async () => {
      await submit('shadow', asking('mk-2'));
      await submit('shadow');
      expect(counted).to.deep.equal([]);
    });
  });

  describe('the deployment layer and the Labs stages through an admission (CPD6-14, CPD6-15)', () => {
    it('the pinned approvals are the strictest of deployment, flow rule, org and agent', async () => {
      flow = { level: 'ask', above: 'public' };
      await approvals.replace(null, {
        default: { writes: 'ask' },
        agents: { a7: { tools: { 'jira@inst-42/create_issue': 'deny' } } },
      });
      const { pin } = await pinOf();
      expect(dimension(pin, 'approvals')?.value).to.deep.equal({
        classes: { read: 'allow', workspace: 'allow', write: 'ask', execute: 'ask' },
        tools: { 'jira@inst-42/create_issue': 'deny' },
        flow: [{ level: 'ask', above: 'public' }],
      });
      expect(dimension(pin, 'approvals')?.setBy).to.equal('deployment');
      expect(pin.deploymentDigest).to.not.equal(canonicalDigest({}));
    });

    it('every Labs stage is pinned from the settings document', async () => {
      settings = { featureFlags: { ENABLE_SKILLS: false, ENABLE_KNOWLEDGE_PUBLICATIONS: true, ENABLE_NAMED_ENTITY_EXTRACTION: true } };
      const { pin } = await pinOf();
      const stages = Object.fromEntries(
        Object.entries(pin.effectiveConfig.dimensions)
          .filter(([path, entry]) => path.startsWith('rollout.') && typeof entry.value === 'boolean')
          .map(([path, entry]) => [path.slice('rollout.'.length), entry.value]),
      );
      expect(stages).to.deep.equal({
        ENABLE_ACTIONS: true,
        ENABLE_MCP: true,
        ENABLE_NAMED_ENTITY_EXTRACTION: true,
        ENABLE_AGENT_HARNESS: true,
        AGENT_HARNESS_KILL_SWITCH: false,
        ENABLE_SCHEDULED_RUNS: false,
        ENABLE_KNOWLEDGE_PUBLICATIONS: true,
        ENABLE_SKILLS: false,
        ENABLE_USER_CONTEXT: true,
        // An edition's own worker stages, at their release defaults (the document sets none of them).
        ...Object.fromEntries(
          EDITION_WORKER_STAGES.map((key) => [
            key,
            EDITION_PLATFORM_FEATURE_FLAGS.find((flag) => flag.key === key)?.defaultEnabled === true,
          ]),
        ),
      });
    });
  });

  describe('CTL-5: a stored layer that still carries a retired rollout key', () => {
    const DENY = { 'jira@inst-42/create_issue': 'deny' } as const;
    const storeWithRetiredKey = () => {
      // The store reads as the Mongo store does: through the shared reader.
      policyStore = new InMemoryPolicyStore((raw) => {
        const layer = readPolicyLayer(raw);
        if (layer === null) throw new Error('not a policy layer');
        return layer;
      });
      policyStore.corrupt(ORG, `org:${ORG}`, {
        schemaVersion: 1,
        scope: { kind: 'org', id: ORG },
        approvals: { tools: DENY },
        rollout: { ENABLE_FOLLOWUP_SUGGESTIONS: true },
      });
    };

    it('pin on: the org layer still applies, so its tool deny is pinned', async () => {
      storeWithRetiredKey();
      const { pin } = await pinOf();
      expect((dimension(pin, 'approvals')?.value as { tools?: unknown }).tools).to.deep.equal(DENY);
    });
  });

  describe('approvals in the shadow comparison (CPD6-16)', () => {
    it('the pin and the worker’s reading of the same document agree: nothing counted', async () => {
      flow = { level: 'deny', above: 'private' };
      await approvals.replace(null, { default: { writes: 'ask' }, agents: { a7: { classes: { read: 'ask' } } } });
      await submit('shadow');
      expect(counted).to.deep.equal([]);
    });

    it('no approvals document at all agrees too', async () => {
      await submit('shadow');
      expect(counted).to.deep.equal([]);
    });

    for (const [name, pinned, today] of [
      ['a class', { default: { writes: 'ask' } }, { default: { writes: 'deny' } }],
      ['a tool rule', { default: { tools: { t1: 'ask' } } }, { default: { tools: { t1: 'deny' } } }],
      ['a flow rule', { default: { flow: { level: 'ask' } } }, { default: { flow: { level: 'deny' } } }],
      ['a rule the pin lacks', {}, { agents: { a7: { writes: 'ask' } } }],
    ] as const) {
      it(`a pin that differs from the worker’s reading in ${name} counts under approvals and changes nothing`, async () => {
        await approvals.replace(null, pinned);
        await submit('shadow');
        expect(counted).to.deep.equal([]);
        // The document changes under a snapshot the cache still serves: the pin is behind what the worker reads.
        const { revision } = await approvals.read();
        await approvals.replace(revision, today);
        const admitted = await submit('shadow');
        expect(counted).to.deep.equal(['approvals']);
        expect(admitted.run.status).to.equal('queued');
      });
    }
  });
});
