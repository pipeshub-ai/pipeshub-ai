/**
 * The one policy resolver (R2 CP3, 02-lld §5.1, 03-interfaces §4.1): layers
 * folded top to bottom through the dimension registry. A ceiling only narrows
 * (a widening is refused naming the dimension and the first layer that asks
 * for it, or kept at the stricter value where the row says `meet`); a choice is
 * the nearest layer's, within its ceiling; an operator row is the deployment's.
 * A layer's `locks` fix those dimensions for every layer below it (C4.02): a
 * different choice is refused as `locked`, and so is a widening of a locked
 * ceiling, while narrowing one stays allowed.
 * Python's `harness_core.policy.resolver` is the twin; the shared vectors hold
 * the two to one answer, digest included.
 */
import { canonicalDigest } from '../../contracts/canonical-json';
import { LAYERS, type LayerName } from '../../contracts/generated/core/enums';
import type { EffectiveRunConfig } from '../../contracts/generated/core/policy/effective-run-config';
import type { PolicyLayer } from '../../contracts/generated/core/policy/policy-layer';
import { InvalidPolicyValue, type Order } from './orders';
import {
  POLICY_REGISTRY,
  type Dimension,
  type DimensionRegistry,
} from './registry';

/** One layer: where it sits in the chain and its values (a whole policy layer, or one section of it). */
export interface ScopedLayer {
  readonly kind: LayerName;
  readonly values: unknown;
}

export type PolicyRefusalCode =
  'widening' | 'not_exposed' | 'outside_ceiling' | 'invalid' | 'locked';

export interface PolicyRefusal {
  code: PolicyRefusalCode;
  dimension: string;
  layer: LayerName;
  detail: string;
}

interface Entry {
  readonly value: unknown;
  readonly setBy: LayerName;
  readonly ceilings: ReadonlyArray<{ layer: LayerName; value: unknown }>;
}

/** The fold of a chain's first layers, to resolve the rest onto (02-lld §8.2). */
export class FoldedPrefix {
  constructor(
    readonly entries: ReadonlyMap<string, Entry>,
    /** Each locked dimension and the layer that locked it (C4.02). */
    readonly locks: ReadonlyMap<string, LayerName> = new Map(),
  ) {}

  /** The values as one document: each dimension's value at its path. */
  document(): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    for (const [path, entry] of this.entries) {
      if (entry.value === undefined) continue;
      const keys = path.split('.');
      let node = out;
      for (const key of keys.slice(0, -1)) {
        node[key] ??= {};
        node = node[key] as Record<string, unknown>;
      }
      node[keys[keys.length - 1] as string] = structuredClone(entry.value);
    }
    return out;
  }
}

export const EMPTY_PREFIX = new FoldedPrefix(new Map());

export type Folded =
  | { prefix: FoldedPrefix; refusal: null }
  | { prefix: null; refusal: PolicyRefusal };

export type Resolved =
  | { config: EffectiveRunConfig; digest: `sha256:${string}`; refusal: null }
  | { config: null; digest: null; refusal: PolicyRefusal };

const read = (values: unknown, path: string): unknown => {
  let node: unknown = values;
  for (const key of path.split('.')) {
    if (typeof node !== 'object' || node === null || Array.isArray(node))
      return undefined;
    node = (node as Record<string, unknown>)[key];
  }
  return node;
};

/** A whole `PolicyLayer` as the resolver reads it. */
export const scoped = (layer: PolicyLayer): ScopedLayer => ({
  kind: layer.scope.kind,
  values: layer,
});

/**
 * Whether `layer` holds the lock's authority for `path`: its `lockedBy` gives
 * the dimension's section to the locking layer or one above it (an org's rules
 * written into an agent layer, 02-lld §11.1).
 */
const carries = (
  layer: ScopedLayer,
  path: string,
  lock: LayerName,
): boolean => {
  const owner = read(layer.values, `lockedBy.${path.split('.')[0]}`);
  return (
    typeof owner === 'string' &&
    (LAYERS as readonly string[]).includes(owner) &&
    LAYERS.indexOf(owner as LayerName) <= LAYERS.indexOf(lock)
  );
};

export class PolicyResolver {
  constructor(private readonly registry: DimensionRegistry = POLICY_REGISTRY) {}

  resolve(layers: readonly ScopedLayer[]): Resolved {
    return this.resolveFrom(EMPTY_PREFIX, layers);
  }

  foldPrefix(
    layers: readonly ScopedLayer[],
    from: FoldedPrefix = EMPTY_PREFIX,
  ): Folded {
    const entries = new Map(from.entries);
    const locks = new Map(from.locks);
    for (const layer of layers) {
      const refusal =
        this.applyValues(entries, locks, layer) ??
        this.applyLocks(locks, layer);
      if (refusal !== null) return { prefix: null, refusal };
    }
    return { prefix: new FoldedPrefix(entries, locks), refusal: null };
  }

  resolveFrom(prefix: FoldedPrefix, rest: readonly ScopedLayer[]): Resolved {
    const folded = this.foldPrefix(rest, prefix);
    if (folded.refusal !== null)
      return { config: null, digest: null, refusal: folded.refusal };
    const refusal = this.outsideCeiling(folded.prefix);
    if (refusal !== null) return { config: null, digest: null, refusal };
    const dimensions: EffectiveRunConfig['dimensions'] = {};
    for (const dimension of this.registry.all()) {
      const entry = folded.prefix.entries.get(dimension.path);
      if (entry?.value === undefined) continue;
      const lockedBy = folded.prefix.locks.get(dimension.path);
      dimensions[dimension.path] = {
        value: structuredClone(
          entry.value,
        ) as EffectiveRunConfig['dimensions'][string]['value'],
        setBy: entry.setBy,
        ceilings: entry.ceilings.map((c) => ({
          layer: c.layer,
          value: structuredClone(
            c.value,
          ) as EffectiveRunConfig['dimensions'][string]['value'],
        })),
        ...(lockedBy !== undefined ? { lockedBy } : {}),
      };
    }
    const config: EffectiveRunConfig = { schemaVersion: 1, dimensions };
    return { config, digest: canonicalDigest(config), refusal: null };
  }

  /** Whether `layer` only narrows what `above` resolves to; the refusal it would get otherwise. */
  assertNarrows(
    layer: ScopedLayer,
    above: readonly ScopedLayer[],
  ): PolicyRefusal | null {
    return this.resolve([...above, layer]).refusal;
  }

  private applyValues(
    entries: Map<string, Entry>,
    locks: ReadonlyMap<string, LayerName>,
    layer: ScopedLayer,
  ): PolicyRefusal | null {
    const release = layer.kind === 'release';
    for (const dimension of this.registry.all()) {
      const raw = read(
        layer.values,
        release ? dimension.path : dimension.layerPath,
      );
      if (raw === undefined) continue;
      const refuse = (
        code: PolicyRefusalCode,
        detail: string,
        at?: string,
      ): PolicyRefusal => ({
        code,
        dimension:
          at === undefined ? dimension.path : `${dimension.path}.${at}`,
        layer: layer.kind,
        detail,
      });
      if (!release && !dimension.exposure.has(layer.kind)) {
        return refuse('not_exposed', `the ${layer.kind} layer may not set it`);
      }
      const current = entries.get(dimension.path);
      const lock = locks.get(dimension.path);
      const locked =
        lock !== undefined && !carries(layer, dimension.path, lock);
      const ceilings = [
        ...(current?.ceilings ?? []),
        { layer: layer.kind, value: raw },
      ];
      if (dimension.order === null) {
        if (locked) {
          const held = current?.value;
          if (
            held === undefined ||
            canonicalDigest(raw) !== canonicalDigest(held)
          )
            return refuse('locked', `locked by the ${lock} layer`);
          continue;
        }
        entries.set(dimension.path, {
          value: raw,
          setBy: layer.kind,
          ceilings: [],
        });
        continue;
      }
      const narrowed = release
        ? this.top(dimension.order, raw)
        : this.narrowed(dimension.order, dimension.widening, raw, current);
      if ('refusal' in narrowed) {
        if (locked && narrowed.refusal.code === 'widening')
          return refuse(
            'locked',
            `locked by the ${lock} layer: ${narrowed.refusal.detail}`,
            narrowed.refusal.at,
          );
        return refuse(
          narrowed.refusal.code,
          narrowed.refusal.detail,
          narrowed.refusal.at,
        );
      }
      const setBy =
        dimension.order.kind === 'admits' && current !== undefined
          ? current.setBy
          : layer.kind;
      entries.set(dimension.path, { value: narrowed.value, setBy, ceilings });
    }
    return null;
  }

  /** Records the dimensions `layer` locks; the topmost lock of a dimension is the one that holds. */
  private applyLocks(
    locks: Map<string, LayerName>,
    layer: ScopedLayer,
  ): PolicyRefusal | null {
    const raw = read(layer.values, 'locks');
    if (raw === undefined) return null;
    if (!Array.isArray(raw))
      return {
        code: 'invalid',
        dimension: 'locks',
        layer: layer.kind,
        detail: 'locks is not a list of dimensions',
      };
    for (const path of raw as unknown[]) {
      const dimension =
        typeof path === 'string' ? this.registry.find(path) : undefined;
      if (dimension === undefined)
        return {
          code: 'invalid',
          dimension: String(path),
          layer: layer.kind,
          detail: 'a lock names no dimension',
        };
      if (layer.kind !== 'release' && !dimension.exposure.has(layer.kind))
        return {
          code: 'not_exposed',
          dimension: dimension.path,
          layer: layer.kind,
          detail: `the ${layer.kind} layer may not lock it`,
        };
      if (!locks.has(dimension.path)) locks.set(dimension.path, layer.kind);
    }
    return null;
  }

  /** The release's value: the top every layer below narrows (an admitted item as it is). */
  private top(
    order: Order,
    raw: unknown,
  ):
    | { value: unknown }
    | { refusal: { code: PolicyRefusalCode; detail: string; at?: string } } {
    if (order.kind === 'admits') return { value: raw };
    try {
      return { value: order.lift(raw) };
    } catch (error) {
      if (!(error instanceof InvalidPolicyValue)) throw error;
      return { refusal: { code: 'invalid', detail: error.message } };
    }
  }

  private narrowed(
    order: Order,
    widening: Dimension['widening'],
    raw: unknown,
    current: Entry | undefined,
  ):
    | { value: unknown }
    | { refusal: { code: PolicyRefusalCode; detail: string; at?: string } } {
    let value: unknown;
    try {
      value = order.lift(raw);
    } catch (error) {
      if (!(error instanceof InvalidPolicyValue)) throw error;
      return { refusal: { code: 'invalid', detail: error.message } };
    }
    if (current?.value === undefined) {
      return {
        value: order.kind === 'admits' ? undefined : order.narrow(value, null),
      };
    }
    if (widening === 'meet') return { value: order.meet(value, current.value) };
    const widened = order.leq(value, current.value);
    if (widened !== null) {
      return {
        refusal: {
          code: 'widening',
          detail: widened.detail,
          ...(widened.at !== undefined ? { at: widened.at } : {}),
        },
      };
    }
    return { value: order.narrow(value, current.value) };
  }

  private outsideCeiling(prefix: FoldedPrefix): PolicyRefusal | null {
    for (const dimension of this.registry.all()) {
      if (dimension.within === null) continue;
      const choice = prefix.entries.get(dimension.path);
      const ceiling = prefix.entries.get(dimension.within);
      if (choice === undefined || ceiling?.value === undefined) continue;
      const order = this.registry.get(dimension.within).order;
      if (order !== null && !order.contains(ceiling.value, choice.value)) {
        return {
          code: 'outside_ceiling',
          dimension: dimension.path,
          layer: choice.setBy,
          detail: `not within ${dimension.within}`,
        };
      }
    }
    return null;
  }
}
