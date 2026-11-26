/**
 * MA1 (QA37-K6, K7; decision D10): a stored pin is data, not a recipe. A run
 * pinned by the previous release (the golden pins PV-1..PV-8, none of which
 * holds `models.roleCostCeilings`) is claimed and read after the upgrade
 * byte for byte, on its stored digest, and spawns children; a pin that holds
 * a dimension this reader's table lacks (after a rollback) is served the same
 * way, and a child under it keeps that entry as its parent's.
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { ChildPinning } from '../../../src/modules/agent-harness/application/admission/child-pinning';
import { PolicyPinning } from '../../../src/modules/agent-harness/application/admission/policy-pinning';
import { ChildRunService } from '../../../src/modules/agent-harness/application/child-run-service';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { canonicalJson } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { POLICY_DIMENSIONS, RELEASE_DEFAULTS } from '../../../src/modules/agent-harness/contracts/generated/core/policy-data';
import { runSpecSchema } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { roleCostBound } from '../../../src/modules/agent-harness/domain/child-model';
import { pipeshub } from '../../../src/modules/agent-harness/domain/child-runs';
import { usdPerMTok } from '../../../src/modules/agent-harness/domain/model-prices-release';
import { releaseLayers } from '../../../src/modules/agent-harness/domain/pin-shadow';
import {
  DimensionRegistry,
  PARENT_DIMENSIONS,
  parentDimensions,
  POLICY_REGISTRY,
  PolicyResolver,
  type ScopedLayer,
} from '../../../src/modules/agent-harness/domain/policy';
import { profileExecutor, runSpecResolver } from '../../../src/modules/agent-harness/domain/run-spec';
import {
  PolicyRunConfigPinner,
  prefixSources,
  runSources,
  type ResolvedRunConfig,
  type RunConfigPinner,
} from '../../../src/modules/control-plane';
import { inheritedDimensions, parentLayer } from '../../../src/modules/control-plane/application/layer-sources';
import { assemblePin, pinDigest, type PinFields } from '../../../src/modules/control-plane/application/pin';
import { InMemoryEffectiveSetResolver } from '../control-plane/support/in-memory-resolution';
import { StaticSnapshots } from '../control-plane/support/pinner-contract';
import { aSnapshot } from '../control-plane/support/resolution-fixtures';
import { pinnerOf } from './support/a-pin';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { HARNESS_ENABLED } from './support/rollout';
import { without } from './support/without';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const ORG = 'org-compat';
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const SILENT = { debug: () => undefined, info: () => undefined, warn: () => undefined, error: () => undefined };
const CEILINGS = 'models.roleCostCeilings';

interface Vector {
  id: string;
  layers: Array<{ scope: { kind: ScopedLayer['kind'] } } & Record<string, unknown>>;
  parent?: unknown;
  pin: Omit<PinFields, 'schemaVersion' | 'effectiveConfig'>;
  expected: { configDigest?: string; digest?: string; refusal?: unknown };
}
const VECTORS = (
  JSON.parse(
    readFileSync(join(__dirname, '../../../../../../contracts/control-plane/vectors/resolved-run-config.json'), 'utf8'),
  ) as { cases: Vector[] }
).cases;

/** The root pins the previous release made: PV-1..PV-8's that are a root's and were not refused. */
const PREVIOUS = VECTORS.filter(
  (vector) => Number(vector.id.slice(3)) <= 8 && vector.parent === undefined && vector.expected.digest !== undefined,
);

/** A table without the rows `dropped` names: the reader a rollback leaves. */
const tableWithout = (...dropped: string[]): DimensionRegistry =>
  DimensionRegistry.fromTable({
    ...POLICY_DIMENSIONS,
    dimensions: POLICY_DIMENSIONS.dimensions.filter((row) => !dropped.includes(row.path)),
  });

const PREVIOUS_TABLE = tableWithout(CEILINGS);

/** What this release would compute for the pin's members (its own `digest` member aside). */
const digestOf = (pin: ResolvedRunConfig): string => pinDigest(without(pin, 'digest'));

/** A golden pin exactly as the vector holds it: its members, its config, its digest. */
const goldenPin = (vector: Vector): ResolvedRunConfig => {
  const resolved = new PolicyResolver().resolve([
    { kind: 'release', values: RELEASE_DEFAULTS },
    ...vector.layers.map((layer) => ({ kind: layer.scope.kind, values: layer })),
  ]);
  if (resolved.config === null) return expect.fail(`${vector.id} does not resolve`);
  return { schemaVersion: 1, ...vector.pin, effectiveConfig: resolved.config, digest: vector.expected.digest } as ResolvedRunConfig;
};

/**
 * The pin an admission stored for the vector's layers: a golden pin holds no
 * request deadline, which every admitted run's does, so the admission's is
 * added. Folded and sealed by `registry`: the previous release's table (no
 * `models.roleCostCeilings` row) for a pin made before the upgrade.
 */
const storedPin = (vector: Vector, registry: DimensionRegistry = POLICY_REGISTRY): ResolvedRunConfig => {
  const resolved = new PolicyResolver(registry).resolve([
    { kind: 'release', values: RELEASE_DEFAULTS },
    ...vector.layers.map((layer) => ({ kind: layer.scope.kind, values: layer })),
    { kind: 'request', values: { schemaVersion: 1, scope: { kind: 'request' }, budgets: { activeTimeS: 600 } } },
  ]);
  if (resolved.config === null) return expect.fail(`${vector.id} does not resolve`);
  return assemblePin({ schemaVersion: 1, ...vector.pin, effectiveConfig: resolved.config } as PinFields);
};

describe('MA1 a stored pin across releases (QA37-K6, QA37-K7)', () => {
  let store: InMemoryExecutionStore;
  let payloads: InMemoryPayloadStore;
  let pins: number;

  beforeEach(() => {
    store = new InMemoryExecutionStore();
    payloads = new InMemoryPayloadStore();
    pins = 0;
  });

  const executions = () =>
    new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(payloads, testCipher()),
      capabilities: { current: () => DURABLE },
      resolveSpec: runSpecResolver(profileExecutor),
    });

  /** A run whose admission stored `pin` (the head it names is the org's), claimed by a worker that reads pins. */
  const claimedWith = async (pin: ResolvedRunConfig, key: string) => {
    store.controlPlaneHeads = () => ({ org: pin.headRevision, deployment: 0 });
    const stub: RunConfigPinner = pinnerOf(async () => ((pins += 1), structuredClone(pin)), { org: pin.headRevision, deployment: 0 });
    const step = new PolicyPinning({ mode: 'on', pinner: stub, metrics: { mismatch: () => undefined }, logger: SILENT });
    const outcome = await new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      agentAccess: grantingAccess((agentKey) => agentDefinition({ agentKey, maxChildren: 4 })),
      executors: profileExecutor,
      policySteps: [step],
    }).submit({
      orgId: ORG,
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer t',
      idempotencyKey: key,
      deadlineMs: 600_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto', modelKey: 'model-1', modelName: 'gpt-x', modelProvider: 'openAI' } },
    });
    if (outcome.route !== 'durable') return expect.fail('expected a durable run');
    const runId = outcome.admission.run.runId;
    const claimed = await executions().claim({ orgId: ORG, runId, workerId: 'w', attemptId: `a-${key}`, leaseDurationMs: 60_000 });
    return { run: claimed.run, runId };
  };

  /** Everywhere a stored pin is read back: the claim's spec, the store's subject, the resolved view. */
  const readBack = async (runId: string): Promise<unknown[]> => {
    const subject = await store.readClaimSubject(ORG, runId);
    const spec = runSpecSchema.parse(subject?.pinned?.spec);
    return [pipeshub(spec)?.resolved, subject?.resolved, (await executions().resolvedView(ORG, runId))?.resolved];
  };

  it('QA37-K6: the previous release’s golden pins hold no `models.roleCostCeilings` and keep their digests under this release’s table', () => {
    expect(PREVIOUS.map((vector) => vector.id)).to.deep.equal(['PV-1', 'PV-3', 'PV-4', 'PV-5']);
    for (const vector of PREVIOUS) {
      const pin = goldenPin(vector);
      expect(CEILINGS in pin.effectiveConfig.dimensions, vector.id).to.equal(false);
      // `pin-vectors.test.ts` holds every vector to its digest with the new row in the table; so does the table without it.
      expect(digestOf(pin), vector.id).to.equal(vector.expected.digest);
      expect(new PolicyResolver(PREVIOUS_TABLE).resolve([
        { kind: 'release', values: RELEASE_DEFAULTS },
        ...vector.layers.map((layer) => ({ kind: layer.scope.kind, values: layer })),
      ]).digest, vector.id).to.equal(vector.expected.configDigest);
    }
  });

  for (const vector of PREVIOUS) {
    it(`QA37-K6 ${vector.id}: after the upgrade the claim, the store and the resolved view hand back the stored pin byte for byte`, async () => {
      const pin = storedPin(vector, PREVIOUS_TABLE);
      const stored = canonicalJson(pin);
      const { runId } = await claimedWith(pin, vector.id);
      const read = await readBack(runId);
      expect(read).to.have.length(3);
      for (const copy of read) expect(canonicalJson(copy)).to.equal(stored);
      // Admission pinned once; no read of the pin folds or digests it again.
      expect(pins).to.equal(1);
    });
  }

  it('QA37-K6: a stored pin is never re-digested: one whose digest this release would not compute is read back as stored', async () => {
    const pin = { ...storedPin(PREVIOUS[0]!, PREVIOUS_TABLE), digest: `sha256:${'d'.repeat(64)}` as const };
    expect(digestOf(pin)).not.to.equal(pin.digest);
    const { runId } = await claimedWith(pin, 'foreign-digest');
    for (const copy of await readBack(runId)) expect(canonicalJson(copy)).to.equal(canonicalJson(pin));
  });

  /** The production pinner of this release over `registry`, for the child of a pinned parent. */
  const childPinning = (registry: DimensionRegistry = POLICY_REGISTRY) => {
    const dimensions = parentDimensions(registry);
    return new ChildPinning({
      mode: 'on',
      pinner: new PolicyRunConfigPinner({
        snapshots: new StaticSnapshots(aSnapshot({ headRevision: 41 })),
        engine: new PolicyResolver(registry),
        effectiveSet: new InMemoryEffectiveSetResolver(),
        releases: releaseLayers(),
        prefix: prefixSources(),
        run: runSources(dimensions),
        parent: dimensions,
        roleBound: roleCostBound,
        prices: usdPerMTok,
      }),
      metrics: { mismatch: () => undefined },
      logger: SILENT,
    });
  };

  const spawnUnder = (run: { runId: string; fence: number }, pinning: ChildPinning, key: string) =>
    new ChildRunService({ runs: store, children: store, journals: new JournalService(payloads, testCipher()), workspaces: null, pinning }).spawn(
      { orgId: ORG, runId: run.runId, workerId: 'w', attemptId: `a-${key}`, fence: run.fence },
      { invocationId: 't1.0', goal: 'look it up', grants: {}, subAgent: { role: 'writer', toolNames: [] } },
    );

  it('QA37-K6: a child spawned after the upgrade under a previous release’s pin is pinned under it and inherits its model', async () => {
    const vector = PREVIOUS.find((candidate) => candidate.id === 'PV-3')!;
    const pin = storedPin(vector, PREVIOUS_TABLE);
    const { run, runId } = await claimedWith(pin, 'child');
    const spawned = await spawnUnder(run, childPinning(), 'child');
    const child = (await store.readClaimSubject(ORG, spawned.childRunId))?.resolved;
    expect(child?.llm).to.deep.equal(pin.llm);
    expect(child?.effectiveConfig.dimensions['models.primary']).to.deep.include({ value: 'openai:gpt-x', setBy: 'parent' });
    expect(child?.effectiveConfig.dimensions['models.roles']?.value).to.deep.equal({ researcher: 'anthropic:claude-z' });
    expect(CEILINGS in (child?.effectiveConfig.dimensions ?? {})).to.equal(false);
    // The parent's stored pin is as it was.
    expect(canonicalJson((await store.readClaimSubject(ORG, runId))?.resolved)).to.equal(canonicalJson(pin));
  });

  /** A pin this release made for an org that set a cost ceiling: PV-9. */
  const withCeilings = (): ResolvedRunConfig => storedPin(VECTORS.find((vector) => vector.id === 'PV-9')!);

  it('QA37-K7: a pin holding a dimension the reader does not know is served at claim and by the view on its stored digest', async () => {
    const invented = structuredClone(withCeilings());
    invented.effectiveConfig.dimensions['models.inventedByALaterRelease'] = { value: { anything: 1 }, setBy: 'org', ceilings: [] };
    const sealed = assemblePin(invented);
    const { runId } = await claimedWith(sealed, 'invented');
    for (const copy of await readBack(runId)) expect(canonicalJson(copy)).to.equal(canonicalJson(sealed));
    expect(pins).to.equal(1);
  });

  it('QA37-K7: an org that set no ceiling is pinned identically by a reader with and without the row', () => {
    const vector = PREVIOUS.find((candidate) => candidate.id === 'PV-3')!;
    const layers = [
      { kind: 'release' as const, values: RELEASE_DEFAULTS },
      ...vector.layers.map((layer) => ({ kind: layer.scope.kind, values: layer })),
    ];
    const here = new PolicyResolver().resolve(layers);
    const rolledBack = new PolicyResolver(tableWithout(CEILINGS)).resolve(layers);
    expect(rolledBack.digest).to.equal(here.digest);
    expect(here.digest).to.equal(vector.expected.configDigest);
  });

  it('QA37-K7: the parent table of a reader without the row: the unknown path is no layer’s to set, and reaches a child as the parent’s entry', () => {
    const parent = withCeilings().effectiveConfig;
    const reader = parentDimensions(tableWithout(CEILINGS));
    expect([reader.layerPathOf(CEILINGS), reader.exposedToParent(CEILINGS)]).to.deep.equal([CEILINGS, false]);
    expect([PARENT_DIMENSIONS.layerPathOf(CEILINGS), PARENT_DIMENSIONS.exposedToParent(CEILINGS)]).to.deep.equal([CEILINGS, true]);
    expect(parentLayer(parent, reader).models).to.not.have.property('roleCostCeilings');
    expect(parentLayer(parent, PARENT_DIMENSIONS).models).to.have.property('roleCostCeilings');
    const child = inheritedDimensions({ schemaVersion: 1, dimensions: {} }, parent, reader);
    expect(child.dimensions[CEILINGS]).to.deep.equal(parent.dimensions[CEILINGS]);
    // A path nobody's table has is read the same way, by this release's table too.
    expect([PARENT_DIMENSIONS.layerPathOf('x.unknown'), PARENT_DIMENSIONS.exposedToParent('x.unknown')]).to.deep.equal(['x.unknown', false]);
  });

  for (const [name, registry] of [
    ['a reader whose table lacks `models.roleCostCeilings` (a rollback)', tableWithout(CEILINGS)],
    ['this release, under a pin with a path a later release invented', POLICY_REGISTRY],
  ] as const) {
    it(`QA37-K7: ${name}: a child is spawned and pinned; the unknown entry is its parent’s, unchanged; the parent’s stored pin is untouched`, async () => {
      const pin = structuredClone(withCeilings());
      const unknown = registry === POLICY_REGISTRY ? 'models.inventedByALaterRelease' : CEILINGS;
      if (registry === POLICY_REGISTRY) {
        pin.effectiveConfig.dimensions[unknown] = { value: { anything: 1 }, setBy: 'org', ceilings: [] };
      }
      const stored = canonicalJson(pin);
      const { run, runId } = await claimedWith(pin, 'rollback');
      const spawned = await spawnUnder(run, childPinning(registry), 'rollback');
      const child = (await store.readClaimSubject(ORG, spawned.childRunId))?.resolved;
      expect(child?.effectiveConfig.dimensions[unknown]).to.deep.equal(pin.effectiveConfig.dimensions[unknown]);
      expect(child?.llm).to.deep.equal(pin.llm);
      expect(child?.effectiveConfig.dimensions['models.primary']).to.deep.include({ value: 'openai:gpt-5.4', setBy: 'parent' });
      expect(canonicalJson((await store.readClaimSubject(ORG, runId))?.resolved)).to.equal(stored);
    });
  }
});
