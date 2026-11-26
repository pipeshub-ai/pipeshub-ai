 
/**
 * In-memory adapters of the resolution ports (03-interfaces §4.2–§4.5): the
 * reference behaviour each production adapter is held to by the same suite.
 * The effective-set resolver is 02-lld §5.2 written out plainly; CPD-7's
 * domain resolver must pass its suite (and may replace it as the reference).
 */
import type { ChangeEvent } from '../../../../src/modules/control-plane/contracts/generated';
import {
  TOOLS_GRANT,
  type EffectiveRunConfig,
  type PolicyLayer,
  type PolicyResolver,
  type Resolved,
  type ResolvedDimension,
  type ScopeKind,
  type ScopedLayer,
  scoped,
} from '../../../../src/modules/control-plane/domain/policy-alias';
import { selectLlm } from '../../../../src/modules/control-plane/domain/llm';
import { REFUSAL_CODES } from '../../../../src/modules/control-plane/domain/refusals';
import { isProblem, problem } from '../../../../src/modules/control-plane/domain/problem';
import type {
  PinBasis,
  PinResult,
  PinSubject,
  RouteResult,
  RouteSubject,
  Snapshot,
} from '../../../../src/modules/control-plane/domain/resolution';
import { NO_FACTS, routingOf, type Routing } from '../../../../src/modules/control-plane/domain/routing';
import { changeStep, touchKey } from '../../../../src/modules/control-plane/domain/revisions';
import type { EffectiveSetResolver } from '../../../../src/modules/control-plane/ports/effective-set-resolver';
import { CanonicalEffectiveSetResolver } from '../../../../src/modules/control-plane/adapters/effective-set-resolver';
import type { PolicyLayerSource } from '../../../../src/modules/control-plane/ports/policy-layer-source';
import type { RunConfigPinner } from '../../../../src/modules/control-plane/ports/run-config-pinner';
import type { SnapshotSource } from '../../../../src/modules/control-plane/ports/snapshot-source';
import { canonicalDigest } from '../../../../src/libs/utils/canonical-json';
import { assemblePin, pinScope } from '../../../../src/modules/control-plane/application/pin';

/** Where each layer kind comes from: a scope of the snapshot, or the subject itself. */
const LAYER_OF: Partial<Record<ScopeKind, (subject: PinSubject, snapshot: Snapshot) => PolicyLayer | null>> = {
  org: (subject, snapshot) => snapshot.layers.get(`org:${subject.orgId}`) ?? null,
  team: (_subject, snapshot) =>
    snapshot.agentOwnerTeam === null ? null : (snapshot.layers.get(`team:${snapshot.agentOwnerTeam}`) ?? null),
  agent: (subject, snapshot) =>
    subject.agentKey === null ? null : (snapshot.layers.get(`agent:${subject.agentKey}`) ?? null),
  user: (subject, snapshot) => snapshot.layers.get(`user:${subject.actorId}`) ?? null,
  trigger: (subject) => subject.trigger ?? null,
  parent: (subject) => (subject.parent === undefined ? null : layerOfConfig('parent', subject.parent.effectiveConfig)),
  request: (subject) => subject.request,
};

export class InMemoryPolicyLayerSource implements PolicyLayerSource {
  private readonly read: (subject: PinSubject, snapshot: Snapshot) => PolicyLayer | null;

  constructor(readonly kind: ScopeKind) {
    const read = LAYER_OF[kind];
    if (read === undefined) throw new Error(`no in-memory layer source for ${kind}`);
    this.read = read;
  }

  layer(subject: PinSubject, snapshot: Snapshot): PolicyLayer | null {
    return this.read(subject, snapshot);
  }
}

/** The layer kinds a pin folds, top to bottom (02-lld §3.1), without release, deployment and live. */
export const PIN_LAYER_KINDS: readonly ScopeKind[] = ['org', 'team', 'agent', 'user', 'trigger', 'parent', 'request'];

/** `{a: {b: 1}}` → `{'a.b': 1}`; `tools.grant` is one value (installation → tool names). */
export function dimensionsOf(values: unknown): Map<string, unknown> {
  const out = new Map<string, unknown>();
  const walk = (value: unknown, path: string): void => {
    if (path === TOOLS_GRANT || value === null || typeof value !== 'object' || Array.isArray(value)) {
      out.set(path, value);
      return;
    }
    for (const [key, inner] of Object.entries(value as Record<string, unknown>)) walk(inner, path ? `${path}.${key}` : key);
  };
  const sections = { ...(values as Record<string, unknown>) };
  delete sections['scope'];
  delete sections['schemaVersion'];
  walk(sections, '');
  return out;
}

/** The inverse of `dimensionsOf` over a resolved config: its values as a layer of `kind`. */
export function layerOfConfig(kind: ScopeKind, config: EffectiveRunConfig): PolicyLayer {
  const layer: Record<string, unknown> = { scope: { kind } };
  for (const [path, { value }] of Object.entries(config.dimensions)) {
    const parts = path.split('.');
    let at = layer;
    for (const part of parts.slice(0, -1)) at = (at[part] ??= {}) as Record<string, unknown>;
    at[parts[parts.length - 1]!] = value;
  }
  return layer as unknown as PolicyLayer;
}

type ToolGrant = Readonly<Record<string, readonly string[] | null>>;

const meetGrant = (above: ToolGrant, below: ToolGrant): ToolGrant =>
  Object.fromEntries(
    Object.entries(below)
      .filter(([inst]) => inst in above)
      .map(([inst, tools]) => {
        const ceiling = above[inst] ?? null;
        if (ceiling === null) return [inst, tools];
        return [inst, tools === null ? ceiling : tools.filter((tool) => ceiling.includes(tool))];
      }),
  );

const grantNarrows = (above: ToolGrant, below: ToolGrant): boolean =>
  Object.entries(below).every(([inst, tools]) => {
    if (!(inst in above)) return false;
    const ceiling = above[inst] ?? null;
    return ceiling === null || (tools !== null && tools.every((tool) => ceiling.includes(tool)));
  });

/**
 * A stand-in for CPD-3's `PolicyResolver` over four orders: numbers are
 * `min` ceilings, booleans `bool-and`, lists and `tools.grant` subsets; a layer that
 * widens what the layers above it set is refused, naming the layer.
 */
export class TestPolicyResolver implements PolicyResolver {
  resolve(layers: readonly ScopedLayer[]): Resolved {
    const config: Record<string, ResolvedDimension> = {};
    for (const layer of layers) {
      for (const [path, raw] of dimensionsOf(layer.values)) {
        const value = raw as ResolvedDimension['value'];
        const current = config[path];
        if (current === undefined) {
          config[path] = { value, setBy: layer.kind, ceilings: [{ layer: layer.kind, value }] };
          continue;
        }
        const narrows =
          path === TOOLS_GRANT
            ? grantNarrows(current.value as ToolGrant, value as ToolGrant)
            : Array.isArray(value)
              ? Array.isArray(current.value) && value.every((item) => (current.value as string[]).includes(item))
              : typeof value === 'number'
                ? value <= (current.value as number)
              : typeof value === 'boolean'
                ? !value || (current.value as boolean)
                : value === current.value;
        if (!narrows) {
          const refusal = { code: 'widening' as const, dimension: path, layer: layer.kind, detail: `${layer.kind} widens ${path}` };
          return { config: null, digest: null, refusal };
        }
        const met = (path === TOOLS_GRANT ? meetGrant(current.value as ToolGrant, value as ToolGrant) : value) as ResolvedDimension['value'];
        config[path] = { value: met, setBy: layer.kind, ceilings: [...current.ceilings, { layer: layer.kind, value }] };
      }
    }
    const resolved = { schemaVersion: 1 as const, dimensions: config };
    return { config: resolved, digest: canonicalDigest(resolved), refusal: null };
  }
}

/**
 * The in-memory adapter is the production resolver itself (CPD-7 promoted
 * CP-SKEL's reference into `domain/`); this name keeps it in C-2's registry.
 */
export class InMemoryEffectiveSetResolver extends CanonicalEffectiveSetResolver implements EffectiveSetResolver {}

export interface PinnerDeps {
  readonly snapshots: SnapshotSource;
  readonly sources: readonly PolicyLayerSource[];
  readonly resolver: PolicyResolver;
  readonly effectiveSet: EffectiveSetResolver;
}

export class InMemoryRunConfigPinner implements RunConfigPinner {
  constructor(private readonly deps: PinnerDeps) {}

  async route(subject: RouteSubject): Promise<RouteResult> {
    const snapshot = await this.deps.snapshots.snapshot(subject.orgId, subject.agentKey, subject.actorId);
    const config = this.rollout(snapshot);
    return { routing: this.routing(subject, config, snapshot), scope: pinScope(subject), basis: snapshot, config };
  }

  /** The stored deployment and org layers alone: every layer that may set a rollout row. */
  private rollout(snapshot: Snapshot): EffectiveRunConfig {
    const layers = [...snapshot.layers]
      .filter(([scope]) => scope === 'deployment' || scope.startsWith('deployment:') || scope === `org:${snapshot.orgId}`)
      .map(([, layer]) => ({ kind: layer.scope.kind, values: { rollout: (layer as { rollout?: unknown }).rollout ?? {} } }));
    const resolved = this.deps.resolver.resolve(layers);
    if (resolved.refusal !== null) throw new Error(`rollout ${resolved.refusal.code}`);
    return resolved.config;
  }

  private routing(subject: RouteSubject, config: EffectiveRunConfig, snapshot: Snapshot): Routing {
    return subject.parent?.routing ?? routingOf(config, snapshot.families, subject, subject.routingFacts ?? NO_FACTS);
  }

  async pin(subject: PinSubject, basis?: PinBasis): Promise<PinResult> {
    const own = basis !== undefined && basis.orgId === subject.orgId && basis.agentKey === subject.agentKey && basis.actorId === subject.actorId;
    const snapshot = own ? basis : await this.deps.snapshots.snapshot(subject.orgId, subject.agentKey, subject.actorId);
    const layers = this.deps.sources
      .map((source) => source.layer(subject, snapshot))
      .filter((layer): layer is PolicyLayer => layer !== null);
    const resolved = this.deps.resolver.resolve(layers.map(scoped));
    if (resolved.refusal !== null) {
      const { code, dimension, layer, detail } = resolved.refusal;
      return problem(REFUSAL_CODES[code], detail, { dimension, layer, action: 'narrow_request' });
    }
    const effectiveSet = this.deps.effectiveSet.resolve(subject, resolved.config, snapshot);
    if (isProblem(effectiveSet)) return effectiveSet;
    return assemblePin({
      schemaVersion: 1,
      headRevision: snapshot.headRevision,
      routing: this.routing(subject, resolved.config, snapshot),
      scope: pinScope(subject),
      effectiveSet,
      effectiveConfig: resolved.config,
      llm: selectLlm(snapshot.models, subject.model),
      deploymentDigest: canonicalDigest({}),
    });
  }
}

export type SnapshotLoader = (orgId: string, agentKey: string | null, actorId: string) => Promise<Snapshot>;

/** Cached snapshots keyed by (org, agent, actor), bounded LRU, kept current by the revision rule. */
export class InMemorySnapshotSource implements SnapshotSource {
  private readonly entries = new Map<string, Snapshot>();

  constructor(
    private readonly load: SnapshotLoader,
    private readonly bound = 1000,
  ) {}

  async snapshot(orgId: string, agentKey: string | null, actorId: string): Promise<Snapshot> {
    const id = `${orgId}|${agentKey ?? ''}|${actorId}`;
    const cached = this.entries.get(id);
    if (cached !== undefined) {
      this.entries.delete(id);
      this.entries.set(id, cached);
      return cached;
    }
    const loaded = await this.load(orgId, agentKey, actorId);
    this.entries.set(id, loaded);
    while (this.entries.size > this.bound) this.entries.delete(this.entries.keys().next().value!);
    return loaded;
  }

  onChange(event: ChangeEvent): void {
    const touched = event.touched.map((ref) => touchKey(ref.kind, ref.id));
    for (const [id, entry] of [...this.entries]) {
      if (entry.orgId !== event.orgId) continue;
      const step = changeStep(entry.headRevision, event);
      if (step === 'duplicate') continue;
      if (step === 'gap' || touched.some((key) => entry.dependsOn.has(key))) {
        this.entries.delete(id);
        continue;
      }
      this.entries.set(id, { ...entry, headRevision: event.revision });
    }
  }

  get size(): number {
    return this.entries.size;
  }
}
