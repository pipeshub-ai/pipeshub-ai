import type {
  EffectiveRunConfig,
  PolicyLayer,
  ScopeKind,
} from '../domain/policy-alias';
import type { PinSubject, Snapshot } from '../domain/resolution';
import type { PolicyLayerSource } from '../ports/policy-layer-source';
import {
  definitionLayer,
  modelsCeilingLayer,
  requestLayer,
} from './model-sources';

type Read = (subject: PinSubject, snapshot: Snapshot) => PolicyLayer | null;

/** One layer kind read from the snapshot or the subject; the sources never read anything else (03-interfaces §4.2). */
class LayerSource implements PolicyLayerSource {
  constructor(
    readonly kind: ScopeKind,
    private readonly read: Read,
  ) {}

  layer(subject: PinSubject, snapshot: Snapshot): PolicyLayer | null {
    return this.read(subject, snapshot);
  }
}

/**
 * What the dimension table says of a dimension a child takes from its
 * parent's pin (the host's registry; the control plane does not import it).
 */
export interface ParentDimensions {
  /** Where a layer below the release carries the dimension (CPD-3's `narrowedBy`); `path` when it is the same. */
  layerPathOf(path: string): string;
  /** Whether a `parent` layer may set it; one it may not set is the parent's, copied to the child's pin as it is. */
  exposedToParent(path: string): boolean;
}

/**
 * A parent's resolved values as a `parent` layer. A dimension a layer
 * carries at another path (the engine, which layers admit by selector) stays
 * with the parent's pinned profile, which `childExecutor` narrows; one the
 * table does not expose to `parent` (the rollout stages) is not a layer's to
 * set and reaches the child's pin through `inheritedDimensions`.
 */
export function parentLayer(
  config: EffectiveRunConfig,
  dimensions: ParentDimensions,
): PolicyLayer {
  const layer: Record<string, unknown> = {
    schemaVersion: 1,
    scope: { kind: 'parent' },
  };
  for (const [path, { value }] of Object.entries(config.dimensions)) {
    if (
      dimensions.layerPathOf(path) !== path ||
      !dimensions.exposedToParent(path)
    )
      continue;
    const keys = path.split('.');
    let node = layer;
    for (const key of keys.slice(0, -1)) {
      node[key] ??= {};
      node = node[key] as Record<string, unknown>;
    }
    node[keys[keys.length - 1] as string] = structuredClone(value);
  }
  return layer as unknown as PolicyLayer;
}

/**
 * A child's resolved config with what its parent's pin holds and no `parent`
 * layer may carry: the parent's entries, as they are (value, who set it, its
 * ceilings), in place of the release's where the release names the row. A
 * child is part of its parent's run, so it runs on the stages and the routing
 * lists its root was admitted on, whatever the org's are now. An entry the
 * `parent` layer set keeps where its value came from (`withParentCeilings`).
 */
export function inheritedDimensions(
  child: EffectiveRunConfig,
  parent: EffectiveRunConfig,
  dimensions: ParentDimensions,
): EffectiveRunConfig {
  const inherited = Object.entries(parent.dimensions).filter(
    ([path]) => !dimensions.exposedToParent(path),
  );
  const own = Object.fromEntries(
    Object.entries(child.dimensions).map(([path, entry]) => [
      path,
      withParentCeilings(entry, parent.dimensions[path]),
    ]),
  );
  return {
    ...child,
    dimensions: {
      ...own,
      ...structuredClone(Object.fromEntries(inherited)),
    },
  };
}

type DimensionEntry = EffectiveRunConfig['dimensions'][string];

/** At most this many ceilings to an entry (effective-run-config.schema.json). */
const MAX_CEILINGS = 10;

/**
 * The parent's ceilings below its release go before the `parent` ceiling of a
 * child's entry (the child folds under the same release, and an ancestor's
 * own `parent` ceilings restate what precedes them). The `parent` layer
 * carries only the value, and readers take meaning from the layers that set
 * it: whose rule a child's approval card names (QA6-F6), which engine classes
 * are the org's rule (QA10-F3). Without them a deep child's card named the
 * org's `writes` rule the deployment's (QAFINAL-FU-PW107). Python's
 * `with_parent_ceilings` is the same rule.
 */
function withParentCeilings(
  entry: DimensionEntry,
  parentEntry: DimensionEntry | undefined,
): DimensionEntry {
  const at = entry.ceilings.findIndex((c) => c.layer === 'parent');
  if (at < 0 || parentEntry === undefined) return entry;
  const provenance = parentEntry.ceilings.filter(
    (c) => c.layer !== 'release' && c.layer !== 'parent',
  );
  if (
    provenance.length === 0 ||
    entry.ceilings.length + provenance.length > MAX_CEILINGS
  )
    return entry;
  return {
    ...entry,
    ceilings: [
      ...entry.ceilings.slice(0, at),
      ...structuredClone(provenance),
      ...entry.ceilings.slice(at),
    ],
  };
}

const stored =
  (scope: (subject: PinSubject, snapshot: Snapshot) => string | null): Read =>
  (subject, snapshot) => {
    const key = scope(subject, snapshot);
    return key === null ? null : (snapshot.layers.get(key) ?? null);
  };

/** The deployment's layer as the snapshot loader built it (02-lld §2.7). */
export const DEPLOYMENT_SCOPE = 'deployment';

/** The deployment's flow rule, a layer of its own: one layer holds one flow rule. */
export const DEPLOYMENT_FLOW_SCOPE = 'deployment:flow';
/** The deployment's rules for one agent (the approvals document's `agents[agentKey]`). */
export const deploymentAgentScope = (agentKey: string): string =>
  `deployment:agent:${agentKey}`;

/** A source of one layer kind from a pure read of the subject and the snapshot. */
export const layerSource = (
  kind: ScopeKind,
  read: (subject: PinSubject, snapshot: Snapshot) => PolicyLayer | null,
): PolicyLayerSource => new LayerSource(kind, read);

export interface ModelSourceOptions {
  /** Routes that name "the configured default" on the host (they resolve to a configured model). */
  modelAliases?: readonly string[];
}

/**
 * The layers folded once per (org, agent, head) below the release (02-lld
 * §8.2): the deployment's (its rules, its flow rule, its rules for the
 * agent), the org's, the configured-models ceiling, the agent's owner team,
 * the stored agent layer and the agent's definition. Adding a layer kind is
 * one entry here.
 */
export const prefixSources = (
  options: ModelSourceOptions = {},
): readonly PolicyLayerSource[] => {
  const aliases = options.modelAliases ?? [];
  return [
    new LayerSource(
      'deployment',
      stored(() => DEPLOYMENT_SCOPE),
    ),
    new LayerSource(
      'deployment',
      stored(() => DEPLOYMENT_FLOW_SCOPE),
    ),
    new LayerSource(
      'deployment',
      stored((subject) =>
        subject.agentKey === null
          ? null
          : deploymentAgentScope(subject.agentKey),
      ),
    ),
    new LayerSource(
      'org',
      stored((subject) => `org:${subject.orgId}`),
    ),
    new LayerSource('org', (subject, snapshot) =>
      modelsCeilingLayer(subject, snapshot, aliases),
    ),
    new LayerSource(
      'team',
      stored((_subject, snapshot) =>
        snapshot.agentOwnerTeam === null
          ? null
          : `team:${snapshot.agentOwnerTeam}`,
      ),
    ),
    new LayerSource(
      'agent',
      stored((subject) =>
        subject.agentKey === null ? null : `agent:${subject.agentKey}`,
      ),
    ),
    new LayerSource('agent', (subject, snapshot) =>
      definitionLayer(subject, snapshot, aliases),
    ),
  ];
};

/** The layers each run folds onto the prefix: the actor's, the trigger's or the parent's, the request's. */
export const runSources = (
  dimensions: ParentDimensions,
): readonly PolicyLayerSource[] => [
  new LayerSource(
    'user',
    stored((subject) => `user:${subject.actorId}`),
  ),
  new LayerSource('trigger', (subject) => subject.trigger ?? null),
  new LayerSource('parent', (subject) =>
    subject.parent === undefined
      ? null
      : parentLayer(subject.parent.effectiveConfig, dimensions),
  ),
  new LayerSource('request', requestLayer),
];
