/**
 * CPD-5 S4: the production `PolicyRunConfigPinner`, its layer sources and the
 * `ResolvedSnapshotCache`, held to CP-SKEL's suites (RCP, PLS, SS) and to the
 * cases the production adapters add: the cached prefix fold, the head check,
 * bounded staleness, org isolation and MA1's model per role.
 */
import { expect } from 'chai';
import { releaseLayers } from '../../../src/modules/agent-harness/domain/pin-shadow';
import {
  FoldedPrefix,
  PARENT_DIMENSIONS,
  PolicyResolver,
} from '../../../src/modules/agent-harness/domain/policy';
import { resolvedRunConfigSchema } from '../../../src/modules/control-plane/contracts/generated';
import { RELEASE_ENGINE_PROFILES } from '../../../src/modules/agent-harness/domain/engine-profiles';
import { prefixSources, runSources } from '../../../src/modules/control-plane/application/layer-sources';
import { PolicyRunConfigPinner } from '../../../src/modules/control-plane/application/run-config-pinner';
import { ResolvedSnapshotCache } from '../../../src/modules/control-plane/application/snapshot-cache';
import type {
  Folded,
  PolicyEngine,
  Resolved,
  ScopedLayer,
} from '../../../src/modules/control-plane/domain/policy-alias';
import { isProblem } from '../../../src/modules/control-plane/domain/problem';
import type { ResolvedRunConfig, Snapshot } from '../../../src/modules/control-plane/domain/resolution';
import type { SnapshotSource } from '../../../src/modules/control-plane/ports/snapshot-source';
import { ORG_A, ORG_B } from './support/cp-objects';
import { committingTx, describeHeadRevisionsContract, InMemoryHeadRevisions } from './support/head-revisions-contract';
import { InMemoryEffectiveSetResolver, TestPolicyResolver } from './support/in-memory-resolution';
import { describeRunConfigPinnerContract, StaticSnapshots } from './support/pinner-contract';
import { describePolicyLayerSourceContract } from './support/policy-layer-source-contract';
import { aLayer, aSnapshot, aSubject } from './support/resolution-fixtures';
import { describeSnapshotSourceContract } from './support/snapshot-source-contract';

const DIMENSIONS = PARENT_DIMENSIONS;
/** The reference resolver's own table: every dimension travels in the parent layer at its own path. */
const ALL_CARRIED = { layerPathOf: (path: string): string => path, exposedToParent: (): boolean => true };

/** CP-SKEL's reference resolver as an engine: a prefix is the layers it folds (the suites' `tools.grant` rows are CPD-7's). */
class ReferenceEngine implements PolicyEngine<readonly ScopedLayer[]> {
  private readonly resolver = new TestPolicyResolver();

  resolve(layers: readonly ScopedLayer[]): Resolved {
    return this.resolver.resolve(layers);
  }

  foldPrefix(layers: readonly ScopedLayer[]): Folded<readonly ScopedLayer[]> {
    const refusal = this.resolver.resolve(layers).refusal;
    return refusal === null ? { prefix: layers, refusal: null } : { prefix: null, refusal };
  }

  resolveFrom(prefix: readonly ScopedLayer[], rest: readonly ScopedLayer[]): Resolved {
    return this.resolver.resolve([...prefix, ...rest]);
  }
}

/** CPD-3's resolver, counting the prefixes folded from the top (`resolveFrom` folds onto one it is given). */
class CountingEngine extends PolicyResolver {
  folds = 0;

  override foldPrefix(...args: Parameters<PolicyResolver['foldPrefix']>) {
    if (args[1] === undefined) this.folds += 1;
    return super.foldPrefix(...args);
  }
}

const production = (snapshots: SnapshotSource, engine: PolicyEngine<FoldedPrefix> = new PolicyResolver()) =>
  new PolicyRunConfigPinner({
    snapshots,
    engine,
    effectiveSet: new InMemoryEffectiveSetResolver(),
    releases: releaseLayers(),
    prefix: prefixSources(),
    run: runSources(DIMENSIONS),
    parent: DIMENSIONS,
  });

const pinned = (result: unknown): ResolvedRunConfig => {
  expect(isProblem(result), JSON.stringify(result)).to.equal(false);
  return result as ResolvedRunConfig;
};

const sources = [...prefixSources(), ...runSources(DIMENSIONS)];
describePolicyLayerSourceContract(
  'production sources',
  (kind) => sources.find((source) => source.kind === kind)!,
  ['org', 'team', 'agent', 'user'],
);

describeRunConfigPinnerContract(
  'PolicyRunConfigPinner (reference resolver)',
  (snapshots) =>
    new PolicyRunConfigPinner({
      snapshots,
      engine: new ReferenceEngine(),
      effectiveSet: new InMemoryEffectiveSetResolver(),
      releases: [{}],
      prefix: prefixSources(),
      run: runSources(ALL_CARRIED),
      parent: ALL_CARRIED,
    }),
);

describeSnapshotSourceContract('ResolvedSnapshotCache', (load, bound) => new ResolvedSnapshotCache({ load, bound }));

describeHeadRevisionsContract('InMemoryHeadRevisions', async () => {
  const heads = new InMemoryHeadRevisions();
  return {
    heads,
    commit: async (orgId) => void heads.commit(orgId),
    moveDeployment: () => heads.move(committingTx('any')),
  };
});

describe('CPD-5 PolicyRunConfigPinner over CPD-3 resolver', () => {
  const world = (head = 7): Snapshot =>
    aSnapshot({
      headRevision: head,
      layers: {
        [`org:${ORG_A}`]: aLayer('org', { budgets: { maxCostUsd: 4 }, models: { routes: ['openai:gpt-x', 'anthropic:claude-z'] } }),
        'agent:a7': aLayer('agent', { budgets: { maxTurns: 10 } }),
        'user:u3': aLayer('user', { models: { primary: 'anthropic:claude-z' } }),
      },
    });

  it('CPD5-30: the prefix is folded once per (org, agent, head, definition); the pin equals the whole chain resolved at once', async () => {
    const engine = new CountingEngine();
    const pinner = production(new StaticSnapshots(world()), engine);
    const subject = aSubject({ request: aLayer('request', { budgets: { maxTurns: 4 } }) });
    const first = pinned(await pinner.pin(subject));
    pinned(await pinner.pin(aSubject({ actorId: 'u9' })));
    expect(engine.folds).to.equal(1);
    pinned(await pinner.pin(aSubject({ agent: aLayer('agent', { budgets: { maxCostUsd: 2 } }) })));
    expect(engine.folds).to.equal(2);

    const whole = new PolicyResolver().resolve([
      { kind: 'release', values: releaseLayers()[0] },
      ...[world().layers.get(`org:${ORG_A}`)!, world().layers.get('agent:a7')!, world().layers.get('user:u3')!, subject.request].map(
        (layer) => ({ kind: layer.scope.kind, values: layer }),
      ),
    ]);
    expect(first.effectiveConfig).to.deep.equal(whole.config);
    expect(first.effectiveConfig.dimensions['budgets.maxTurns']).to.deep.include({ value: 4, setBy: 'request' });
    expect(first.effectiveConfig.dimensions['budgets.maxCostUsd']).to.deep.include({ value: 4, setBy: 'org' });
  });

  it('CPD5-30: a new head folds the prefix again', async () => {
    const engine = new CountingEngine();
    const snapshots = new StaticSnapshots(world(7));
    const pinner = production(snapshots, engine);
    pinned(await pinner.pin(aSubject()));
    snapshots.current = world(8);
    expect(pinned(await pinner.pin(aSubject())).headRevision).to.equal(8);
    expect(engine.folds).to.equal(2);
  });

  it('CPD5-33: the pin starts from the first released engine every layer admits; a child keeps its parent’s; none admitted is refused on the engine', async () => {
    const engines = RELEASE_ENGINE_PROFILES.map((profile) => profile.engine);
    expect(engines.length).to.be.greaterThan(1);
    const pinner = production(new StaticSnapshots(world()));
    const first = pinned(await pinner.pin(aSubject()));
    expect(first.effectiveConfig.dimensions['engineProfile.engine']?.value).to.deep.equal(engines[0]);
    const second = pinned(
      await pinner.pin(aSubject({ agent: aLayer('agent', { engineProfile: { engines: [engines[1]!.kind] } }) })),
    );
    expect(second.effectiveConfig.dimensions['engineProfile.engine']?.value).to.deep.equal(engines[1]);
    const child = pinned(await pinner.pin(aSubject({ surface: 'child', parent: second })));
    expect(child.effectiveConfig.dimensions['engineProfile.engine']?.value).to.deep.equal(engines[1]);
    const none = await pinner.pin(aSubject({ agent: aLayer('agent', { engineProfile: { engines: ['no-such-engine'] } }) }));
    expect(none).to.deep.include({ code: 'CP_WIDENING', dimension: 'engineProfile.engine', layer: 'agent' });
  });

  it('CPD5-32 (MA1): the pin carries the primary model and a model per role; a child takes its parent’s unless its role sets one', async () => {
    const pinner = production(new StaticSnapshots(world()));
    const parent = pinned(
      await pinner.pin(
        aSubject({ agent: aLayer('agent', { models: { primary: 'openai:gpt-x', roles: { researcher: 'anthropic:claude-z' } } }) }),
      ),
    );
    // The user's own choice is nearer than the agent's: the actor picked it.
    expect(parent.effectiveConfig.dimensions['models.primary']).to.deep.include({ value: 'anthropic:claude-z', setBy: 'user' });
    expect(parent.effectiveConfig.dimensions['models.roles']?.value).to.deep.equal({ researcher: 'anthropic:claude-z' });
    const child = pinned(await pinner.pin(aSubject({ surface: 'child', parent, actorId: 'u9' })));
    expect(child.effectiveConfig.dimensions['models.primary']).to.deep.include({ value: 'anthropic:claude-z', setBy: 'parent' });
    expect(child.routing).to.deep.equal(parent.routing);
    // A child whose role sets a model takes it, not its parent's (here the agent's `openai:gpt-x`: this actor stored no choice).
    const agent = aLayer('agent', { models: { primary: 'openai:gpt-x', roles: { researcher: 'anthropic:claude-z' } } });
    const onAgentModel = pinned(await pinner.pin(aSubject({ actorId: 'u9', agent })));
    expect(onAgentModel.effectiveConfig.dimensions['models.primary']).to.deep.include({ value: 'openai:gpt-x', setBy: 'agent' });
    const roleSet = pinned(
      await pinner.pin(
        aSubject({
          surface: 'child',
          parent: onAgentModel,
          actorId: 'u9',
          roleKeys: ['researcher', 'default'],
          request: aLayer('request', { models: { primary: 'anthropic:claude-z' } }),
        }),
      ),
    );
    expect(roleSet.effectiveConfig.dimensions['models.primary']).to.deep.include({ value: 'anthropic:claude-z', setBy: 'request' });
    expect(roleSet.effectiveConfig.dimensions['models.roles']?.value).to.deep.equal({ researcher: 'anthropic:claude-z' });
    const widened = await pinner.pin(
      aSubject({ surface: 'child', parent, actorId: 'u9', request: aLayer('request', { models: { primary: 'mistral:m' } }) }),
    );
    expect(widened).to.deep.include({ code: 'CP_OUTSIDE_CEILING', dimension: 'models.primary' });
  });

  it('ADM-25 (child): a child’s route is its parent’s, whatever the subject says', async () => {
    const pinner = production(new StaticSnapshots(world()));
    const parent = pinned(await pinner.pin(aSubject()));
    const facts = { infrastructureReady: false, workerLive: false, scheduled: false, placement: null, modelKey: null };
    const child = pinned(await pinner.pin(aSubject({ surface: 'child', parent, routingFacts: facts })));
    expect(child.routing).to.deep.equal(parent.routing);
  });

  it('CPD5-23: the same subject and snapshot pin one digest; the pin validates against its contract', async () => {
    const first = pinned(await production(new StaticSnapshots(world())).pin(aSubject()));
    const second = pinned(await production(new StaticSnapshots(world())).pin(aSubject()));
    expect(second.digest).to.equal(first.digest);
    expect(first.scope).to.deep.equal({ agentKey: 'a7', projectId: null });
    expect(resolvedRunConfigSchema.parse(first)).to.deep.equal(first);
  });
});

describe('CPD-5 ResolvedSnapshotCache with the head check', () => {
  let heads: InMemoryHeadRevisions;
  let loads: string[];
  let clock: number;

  const load = async (orgId: string, agentKey: string | null, actorId: string): Promise<Snapshot> => {
    loads.push(`${orgId}/${agentKey}/${actorId}`);
    return { ...aSnapshot({ headRevision: heads.revisions.get(orgId) ?? 0, agentKey, actorId }), orgId };
  };

  const cache = () => new ResolvedSnapshotCache({ load, heads, now: () => clock, staleMs: 30_000 });

  beforeEach(() => {
    heads = new InMemoryHeadRevisions();
    loads = [];
    clock = 1_000;
  });

  it('CPD5-31 (IX-ADM-PIN-6): an unmoved head serves the entry with no store read but the head', async () => {
    const source = cache();
    await source.snapshot(ORG_A, 'a7', 'u3');
    await source.snapshot(ORG_A, 'a7', 'u3');
    expect(loads).to.have.length(1);
  });

  it('CPD5-31: a head moved by a write whose event was lost reloads the org once', async () => {
    const source = cache();
    await source.snapshot(ORG_A, 'a7', 'u3');
    await source.snapshot(ORG_A, 'a8', 'u3');
    heads.commit(ORG_A);
    expect((await source.snapshot(ORG_A, 'a7', 'u3')).headRevision).to.equal(1);
    expect(source.size).to.equal(1);
    await source.snapshot(ORG_A, 'a7', 'u3');
    expect(loads).to.have.length(3);
  });

  it('PIN-21: head unreadable: an entry confirmed in the last 30 s is served; older, or none, fails closed', async () => {
    const source = cache();
    await source.snapshot(ORG_A, 'a7', 'u3');
    heads.unreadable = true;
    clock += 29_999;
    await source.snapshot(ORG_A, 'a7', 'u3');
    clock += 30_000;
    const stale = await source.snapshot(ORG_A, 'a7', 'u3').catch((e: unknown) => e);
    expect(stale).to.have.property('code', 'CP_UNAVAILABLE');
    const none = await source.snapshot(ORG_A, 'a9', 'u3').catch((e: unknown) => e);
    expect(none).to.have.property('code', 'CP_UNAVAILABLE');
    expect(loads).to.have.length(1);
  });

  it('PIN-24: two orgs with the same agent and actor never share an entry; a head move in one leaves the other', async () => {
    const source = cache();
    heads.commit(ORG_B);
    const a = await source.snapshot(ORG_A, 'a7', 'u3');
    const b = await source.snapshot(ORG_B, 'a7', 'u3');
    expect([a.orgId, b.orgId, a.headRevision, b.headRevision]).to.deep.equal([ORG_A, ORG_B, 0, 1]);
    heads.commit(ORG_A);
    await source.snapshot(ORG_B, 'a7', 'u3');
    expect(loads).to.have.length(2);
    expect((await source.snapshot(ORG_A, 'a7', 'u3')).headRevision).to.equal(1);
    expect(loads).to.have.length(3);
  });

  it('PIN-23: refresh drops only that org (the admission’s CP_STALE re-pin)', async () => {
    const source = cache();
    await source.snapshot(ORG_A, 'a7', 'u3');
    await source.snapshot(ORG_B, 'a7', 'u3');
    source.refresh(ORG_A);
    expect(source.size).to.equal(1);
  });
});
