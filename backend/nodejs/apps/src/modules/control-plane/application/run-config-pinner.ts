import { canonicalDigest } from '../../../libs/utils/canonical-json';
import type { Problem, ResolvedRunConfig } from '../contracts/generated';
import { ControlPlaneUnavailableError } from '../domain/errors';
import { pinnedAgentOwner, pinScope, sealPin } from './pin';
import {
  scoped,
  type EffectiveRunConfig,
  type Folded,
  type PolicyRefusal,
  type PolicyEngine,
  type PolicyLayer,
  type Resolved,
  type ScopedLayer,
} from '../domain/policy-alias';
import { ENGINE_MODEL_ROLES, llmRoute, selectLlm } from '../domain/llm';
import { isProblem, problem } from '../domain/problem';
import { REFUSAL_CODES } from '../domain/refusals';
import type {
  PinBasis,
  PinResult,
  PinSubject,
  RouteResult,
  RouteSubject,
  Snapshot,
} from '../domain/resolution';
import { NO_FACTS, routingOf, type Routing } from '../domain/routing';
import type { EffectiveSetResolver } from '../ports/effective-set-resolver';
import type { PolicyLayerSource } from '../ports/policy-layer-source';
import type { RunConfigPinner } from '../ports/run-config-pinner';
import type { SnapshotSource } from '../ports/snapshot-source';
import {
  DEPLOYMENT_SCOPE,
  inheritedDimensions,
  type ParentDimensions,
} from './layer-sources';
import { namesModel, namesUnconfiguredModel } from './model-sources';

export interface PolicyRunConfigPinnerOptions<P> {
  snapshots: SnapshotSource;
  engine: PolicyEngine<P>;
  effectiveSet: EffectiveSetResolver;
  /**
   * The release layer (`RELEASE_DEFAULTS`), the top every layer narrows: one
   * candidate per released engine, in release order. The pin takes the first
   * whose engine every layer admits (E4a's rule); a child takes its parent's.
   */
  releases: readonly unknown[];
  /** Folded once per (org, agent, head, definition): `prefixSources()`. */
  prefix: readonly PolicyLayerSource[];
  /** Folded per run onto the prefix: `runSources(...)`. */
  run: readonly PolicyLayerSource[];
  /** What a child takes from its parent's pin outside the `parent` layer (the same table `runSources` reads). */
  parent: ParentDimensions;
  /** Folded prefixes kept (LRU); the agent tier's bound by default (02-lld §8.1). */
  prefixBound?: number;
  /**
   * What a model costs (USD per 1M tokens, the host's release price table);
   * null: no price. Read only for a child under a role cost ceiling (MA1).
   */
  prices?: (model: string) => number | null;
  /**
   * The cost ceiling `models.roleCostCeilings` holds for a child's role keys
   * (the host's `min-map` rule; the control plane does not import the order);
   * null: none. Absent: a child under such a ceiling is refused.
   */
  roleBound?: (ceilings: unknown, roleKeys: readonly string[]) => number | null;
  /**
   * G-41: whether the org's model groups let `actorId` use the configured
   * model `modelKey` now (the host's live membership read). An engine role
   * (fast, subagent, compaction) mapped to a model they refuse is left out of
   * the pin, so it calls the run's own model; a read that fails leaves every
   * engine role out (fail closed). Absent: no model is limited to groups.
   */
  modelGroups?: (
    orgId: string,
    actorId: string,
    modelKey: string,
  ) => Promise<boolean>;
}

/** The ceiling a child's model is priced against (MA1). */
const ROLE_COST_CEILINGS = 'models.roleCostCeilings';
const MODEL_ROLES = 'models.roles';

/** The dimension a release candidate's engine is refused on. */
const ENGINE = 'engineProfile.engine';

interface EngineId {
  kind?: unknown;
  version?: unknown;
}

/** The engine a release layer or a resolved config names, if any. */
const engineOf = (source: unknown): EngineId | null => {
  if (typeof source !== 'object' || source === null) return null;
  const config = source as {
    dimensions?: Record<string, { value?: unknown }>;
    engineProfile?: { engine?: unknown };
  };
  const engine =
    config.dimensions?.[ENGINE]?.value ?? config.engineProfile?.engine;
  return typeof engine === 'object' && engine !== null ? engine : null;
};

const sameEngine = (a: EngineId | null, b: EngineId): boolean =>
  a !== null && a.kind === b.kind && a.version === b.version;

/** The layers a child folds under the release: its parent's pin and its own request. */
const CHILD_KINDS: ReadonlySet<string> = new Set(['parent', 'request']);

/** The section the routing rows sit in: exposed to the deployment and the org only. */
const ROLLOUT = 'rollout';

/** A stored layer's `rollout` section alone, as a layer of its kind. */
const rolloutOf = (layer: PolicyLayer): ScopedLayer => ({
  kind: layer.scope.kind,
  values: { [ROLLOUT]: layer.rollout ?? {} },
});

/** The route of a turn the subject describes, on rows already resolved. */
const routed = (
  subject: RouteSubject,
  config: EffectiveRunConfig,
  snapshot: Snapshot,
): Routing =>
  subject.parent !== undefined
    ? subject.parent.routing
    : routingOf(
        config,
        snapshot.families,
        subject,
        subject.routingFacts ?? NO_FACTS,
      );

/** `basis` when it is the subject's own snapshot; a basis of another org, agent or actor is never pinned on. */
const ownBasis = (
  subject: RouteSubject,
  basis: PinBasis | undefined,
): Snapshot | undefined =>
  basis?.orgId === subject.orgId &&
  basis.agentKey === subject.agentKey &&
  basis.actorId === subject.actorId
    ? basis
    : undefined;

/**
 * The one admission-time resolver (03-interfaces §4.4, 02-lld §5.3): the
 * snapshot's and the subject's layers folded through CPD-3's resolver, the
 * effective set resolved against the result, the pin assembled with its
 * digest. The `release … agent` fold is computed once per (org, agent, head,
 * deployment revision, definition) and only the run's own layers are folded
 * per pin.
 */
export class PolicyRunConfigPinner<P> implements RunConfigPinner {
  private readonly prefixes = new Map<string, P>();
  private readonly bound: number;
  private readonly releases: readonly ScopedLayer[];
  /** A snapshot's rollout rows, folded once: a snapshot is never changed in place. */
  private readonly rollouts = new WeakMap<Snapshot, EffectiveRunConfig>();

  constructor(private readonly options: PolicyRunConfigPinnerOptions<P>) {
    this.bound = options.prefixBound ?? 20_000;
    this.releases = options.releases.map((values) => ({
      kind: 'release' as const,
      values,
    }));
  }

  async route(subject: RouteSubject): Promise<RouteResult> {
    const snapshot = await this.options.snapshots.snapshot(
      subject.orgId,
      subject.agentKey,
      subject.actorId,
    );
    const config = this.rollout(snapshot);
    return {
      routing: routed(subject, config, snapshot),
      scope: pinScope(subject),
      basis: snapshot,
      config,
    };
  }

  async pin(subject: PinSubject, basis?: PinBasis): Promise<PinResult> {
    const snapshot =
      ownBasis(subject, basis) ??
      (await this.options.snapshots.snapshot(
        subject.orgId,
        subject.agentKey,
        subject.actorId,
      ));
    if (namesUnconfiguredModel(subject, snapshot)) {
      return refused({
        code: 'outside_ceiling',
        dimension: 'models.primary',
        layer: 'request',
        detail: 'not a model the deployment configures',
      });
    }
    const resolved = this.resolve(subject, snapshot);
    if (resolved.refusal !== null) return refused(resolved.refusal);
    // A child that names no model of its own calls the one its parent was pinned to.
    const llm =
      subject.parent !== undefined && !namesModel(subject)
        ? subject.parent.llm
        : selectLlm(snapshot.models, subject.model);
    if (subject.surface === 'child') {
      const above = this.aboveRoleCeiling(subject, resolved.config, llm);
      if (above !== null) return refused(above);
    }
    const engineRoleAbove = this.engineRoleAboveCeiling(resolved.config);
    if (engineRoleAbove !== null) return refused(engineRoleAbove);
    const config = await this.engineRolesInGroups(
      subject,
      resolved.config,
      snapshot,
    );
    const effectiveSet = this.options.effectiveSet.resolve(
      { ...subject, agentOwner: pinnedAgentOwner(subject) },
      config,
      snapshot,
    );
    if (isProblem(effectiveSet)) return effectiveSet;
    return sealPin({
      schemaVersion: 1,
      headRevision: snapshot.headRevision,
      routing: routed(subject, config, snapshot),
      scope: pinScope(subject),
      effectiveSet,
      effectiveConfig:
        subject.parent !== undefined
          ? inheritedDimensions(
              config,
              subject.parent.effectiveConfig,
              this.options.parent,
            )
          : config,
      llm,
      deploymentDigest: deploymentDigest(snapshot),
    });
  }

  /**
   * The release's, the deployment's and the org's `rollout` sections folded:
   * every row routing reads, and nothing a refusal elsewhere in a layer could
   * hide. The rows are exposed to those layers alone, so the whole chain
   * `pin` folds resolves them to the same values.
   */
  private rollout(snapshot: Snapshot): EffectiveRunConfig {
    const held = this.rollouts.get(snapshot);
    if (held !== undefined) return held;
    const release = this.releases[0]?.values as
      { [ROLLOUT]?: unknown } | undefined;
    const org = snapshot.layers.get(`org:${snapshot.orgId}`);
    const resolved = this.options.engine.resolve([
      { kind: 'release', values: { [ROLLOUT]: release?.[ROLLOUT] ?? {} } },
      ...[...snapshot.layers]
        .filter(([scope]) => isDeploymentScope(scope))
        .map(([, layer]) => rolloutOf(layer)),
      ...(org !== undefined ? [rolloutOf(org)] : []),
    ]);
    if (resolved.refusal !== null) {
      throw new ControlPlaneUnavailableError(
        `The rollout cannot be resolved: the ${resolved.refusal.layer} layer's ${resolved.refusal.dimension} (${resolved.refusal.code})`,
      );
    }
    this.rollouts.set(snapshot, resolved.config);
    return resolved.config;
  }

  /**
   * A child never calls a model above its role's cost ceiling, inherited or
   * set by its role (MA1 C4): a model with no price cannot be shown to be
   * within it. The refusal names the layer that chose the model.
   */
  private aboveRoleCeiling(
    subject: PinSubject,
    config: EffectiveRunConfig,
    llm: ResolvedRunConfig['llm'],
  ): PolicyRefusal | null {
    const ceilings = config.dimensions[ROLE_COST_CEILINGS]?.value ?? null;
    if (ceilings === null) return null;
    const refusal = (detail: string): PolicyRefusal => ({
      code: 'outside_ceiling',
      dimension: ROLE_COST_CEILINGS,
      layer: namesModel(subject) ? 'request' : 'parent',
      detail,
    });
    const { roleBound, prices } = this.options;
    if (roleBound === undefined) {
      return refusal('role cost ceilings cannot be checked here');
    }
    const bound = roleBound(ceilings, subject.roleKeys ?? []);
    if (bound === null) return null;
    const price = llm === null ? null : (prices?.(llm.model) ?? null);
    if (price === null) return refusal('the model has no price');
    return price <= bound
      ? null
      : refusal(
          `the model costs ${String(price)} per 1M tokens, above ${String(bound)}`,
        );
  }

  /**
   * G-41: the engine roles whose model the actor's groups
   * allow; a role refused is dropped and calls the run's own model, which
   * admission already held to the groups (ModelGroupStep). A role's model is
   * the configured entry that lists its route, as the gateway serves it.
   */
  private async engineRolesInGroups(
    subject: PinSubject,
    config: EffectiveRunConfig,
    snapshot: Snapshot,
  ): Promise<EffectiveRunConfig> {
    const check = this.options.modelGroups;
    const dimension = config.dimensions[MODEL_ROLES];
    const roles = dimension?.value;
    if (
      check === undefined ||
      dimension === undefined ||
      roles === null ||
      typeof roles !== 'object'
    ) {
      return config;
    }
    const map = { ...(roles as Record<string, unknown>) };
    const mapped = ENGINE_MODEL_ROLES.filter(
      (role) =>
        Object.prototype.hasOwnProperty.call(map, role) &&
        typeof map[role] === 'string',
    );
    if (mapped.length === 0) return config;
    const refused = new Set<string>();
    for (const role of mapped) {
      const route = map[role] as string;
      const entry = snapshot.models.find((model) =>
        model.modelNames.some(
          (name) => llmRoute(model.provider, name) === route,
        ),
      );
      let allowed: boolean;
      try {
        allowed =
          entry === undefined ||
          (await check(subject.orgId, subject.actorId, entry.modelKey));
      } catch {
        allowed = false;
      }
      if (!allowed) refused.add(role);
    }
    if (refused.size === 0) return config;
    const kept = Object.fromEntries(
      Object.entries(map).filter(([role]) => !refused.has(role)),
    );
    return {
      ...config,
      dimensions: {
        ...config.dimensions,
        [MODEL_ROLES]: { ...dimension, value: kept },
      },
    };
  }

  /**
   * GA-15: an engine role (fast, subagent, compaction) mapped in
   * `models.roles` is held to its role's cost ceiling as a child's role is
   * (`default` bounds every role): a model with no price cannot be shown to
   * be within it. Unmapped roles call the primary model and are not checked.
   */
  private engineRoleAboveCeiling(
    config: EffectiveRunConfig,
  ): PolicyRefusal | null {
    const ceilings = config.dimensions[ROLE_COST_CEILINGS]?.value ?? null;
    const roles = config.dimensions[MODEL_ROLES]?.value;
    if (ceilings === null || roles === null || typeof roles !== 'object') {
      return null;
    }
    const map = roles as Record<string, unknown>;
    const setBy = config.dimensions[MODEL_ROLES]?.setBy;
    const layer: PolicyRefusal['layer'] = setBy ?? 'org';
    for (const role of ENGINE_MODEL_ROLES) {
      const route = Object.prototype.hasOwnProperty.call(map, role)
        ? map[role]
        : undefined;
      if (typeof route !== 'string') continue;
      const refusal = (detail: string): PolicyRefusal => ({
        code: 'outside_ceiling',
        dimension: ROLE_COST_CEILINGS,
        layer,
        detail: `the ${role} role's model ${detail}`,
      });
      const { roleBound, prices } = this.options;
      if (roleBound === undefined) {
        return refusal('cannot be checked against its cost ceiling here');
      }
      const bound = roleBound(ceilings, [role]);
      if (bound === null) continue;
      const model = route.slice(route.indexOf(':') + 1);
      const price = prices?.(model) ?? null;
      if (price === null) return refusal('has no price');
      if (price > bound) {
        return refusal(
          `costs ${String(price)} per 1M tokens, above ${String(bound)}`,
        );
      }
    }
    return null;
  }

  /**
   * The chain resolved on the first release candidate whose engine every
   * layer admits; when none is, the last candidate's refusal names the layer.
   * A child's chain is its parent's pin and its own request alone: authority
   * is resolved once, at the root's admission, and a child never re-reads the
   * org's or the agent's layers (a narrowing since then is the live layer's).
   */
  private resolve(subject: PinSubject, snapshot: Snapshot): Resolved {
    const run = this.layers(
      subject.parent !== undefined
        ? this.options.run.filter((source) => CHILD_KINDS.has(source.kind))
        : this.options.run,
      subject,
      snapshot,
    );
    const inherited = engineOf(subject.parent?.effectiveConfig);
    let last: Resolved | null = null;
    for (const [index, release] of this.releases.entries()) {
      if (
        inherited !== null &&
        !sameEngine(engineOf(release.values), inherited)
      )
        continue;
      last =
        subject.parent !== undefined
          ? this.options.engine.resolve([release, ...run])
          : this.onPrefix(subject, snapshot, index, release, run);
      if (last.refusal?.dimension !== ENGINE) return last;
    }
    if (last !== null) return last;
    return {
      config: null,
      digest: null,
      refusal: {
        code: 'widening',
        dimension: ENGINE,
        layer: 'parent',
        detail: "the parent's engine is not released here",
      },
    };
  }

  private onPrefix(
    subject: PinSubject,
    snapshot: Snapshot,
    index: number,
    release: ScopedLayer,
    run: readonly ScopedLayer[],
  ): Resolved {
    const folded = this.prefixOf(subject, snapshot, index, release);
    return folded.refusal !== null
      ? { config: null, digest: null, refusal: folded.refusal }
      : this.options.engine.resolveFrom(folded.prefix, run);
  }

  private prefixOf(
    subject: PinSubject,
    snapshot: Snapshot,
    index: number,
    release: ScopedLayer,
  ): Folded<P> {
    const key = [
      snapshot.orgId,
      subject.agentKey ?? '',
      snapshot.headRevision,
      snapshot.deploymentRevision,
      subject.agent === undefined ? '' : canonicalDigest(subject.agent),
      (subject.verifiedRoutes ?? []).join('\u0001'),
      index,
    ].join('\u0000');
    const cached = this.prefixes.get(key);
    if (cached !== undefined) {
      this.prefixes.delete(key);
      this.prefixes.set(key, cached);
      return { prefix: cached, refusal: null };
    }
    const folded = this.options.engine.foldPrefix([
      release,
      ...this.layers(this.options.prefix, subject, snapshot),
    ]);
    if (folded.refusal !== null) return folded;
    this.prefixes.set(key, folded.prefix);
    while (this.prefixes.size > this.bound) {
      this.prefixes.delete(this.prefixes.keys().next().value as string);
    }
    return folded;
  }

  private layers(
    sources: readonly PolicyLayerSource[],
    subject: PinSubject,
    snapshot: Snapshot,
  ): ScopedLayer[] {
    return sources
      .map((source) => source.layer(subject, snapshot))
      .filter((layer): layer is PolicyLayer => layer !== null)
      .map(scoped);
  }
}

/**
 * The digest of the deployment's layers the snapshot holds for this subject.
 * A deployment with its one `deployment` layer digests as that layer alone
 * (the shared pin vectors); its flow rule and its rules for the agent join as
 * a map by scope.
 */
function deploymentDigest(snapshot: Snapshot): string {
  const layers = [...snapshot.layers].filter(([scope]) =>
    isDeploymentScope(scope),
  );
  const only = layers.length === 1 ? layers[0] : undefined;
  return canonicalDigest(
    layers.length === 0
      ? {}
      : only?.[0] === DEPLOYMENT_SCOPE
        ? only[1]
        : Object.fromEntries(layers),
  );
}

/** The deployment's own layers among a snapshot's: its rules, its flow rule, its rules for the agent. */
const isDeploymentScope = (scope: string): boolean =>
  scope === DEPLOYMENT_SCOPE || scope.startsWith(`${DEPLOYMENT_SCOPE}:`);

const refused = (refusal: PolicyRefusal): Problem =>
  problem(REFUSAL_CODES[refusal.code], refusal.detail, {
    dimension: refusal.dimension,
    layer: refusal.layer,
    action: 'narrow_request',
  });
