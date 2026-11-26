import type {
  EffectiveRunConfig,
  LayerName,
  PolicyLayer,
} from '../contracts/generated';

/*
 * The generic policy engine's types (03-interfaces §4.1). `PolicyLayer` and
 * `EffectiveRunConfig` are generated into this module's codegen root from
 * contracts/harness/core/policy. The resolver's own interface types
 * (`ScopedLayer`, `Resolved`, `PolicyRefusal`, `PolicyResolver`) are code in
 * agent-harness `domain/policy`, which 03-interfaces §1 forbids importing, so
 * they are mirrored here shape for shape; `policy-types.test.ts` holds CPD-3's
 * resolver to them at compile time.
 */
export type { EffectiveRunConfig, PolicyLayer };

/** A layer of the precedence chain (02-lld §3.1). */
export type ScopeKind = LayerName;

/** One layer as the resolver reads it: where it sits in the chain, and its values. */
export interface ScopedLayer {
  readonly kind: ScopeKind;
  readonly values: unknown;
}

export const scoped = (layer: PolicyLayer): ScopedLayer => ({
  kind: layer.scope.kind,
  values: layer,
});

/** One resolved dimension, the layer that set it and every ceiling that applied. */
export type ResolvedDimension = EffectiveRunConfig['dimensions'][string];

export type PolicyRefusalCode =
  'widening' | 'not_exposed' | 'outside_ceiling' | 'invalid' | 'locked';

export interface PolicyRefusal {
  readonly code: PolicyRefusalCode;
  readonly dimension: string;
  readonly layer: ScopeKind;
  readonly detail: string;
}

export type Resolved =
  | {
      readonly config: EffectiveRunConfig;
      readonly digest: string;
      readonly refusal: null;
    }
  | {
      readonly config: null;
      readonly digest: null;
      readonly refusal: PolicyRefusal;
    };

/** The part of CPD-3's `PolicyResolver` the pinner calls. */
export interface PolicyResolver {
  resolve(layers: readonly ScopedLayer[]): Resolved;
}

/** A fold of a chain's first layers (CPD-3's `FoldedPrefix`), opaque here. */
export type Folded<P> =
  | { readonly prefix: P; readonly refusal: null }
  | { readonly prefix: null; readonly refusal: PolicyRefusal };

/**
 * CPD-3's resolver with its precomputed folds (02-lld §8.2): the production
 * pinner folds `release … agent` once per (org, agent, head) and resolves
 * only the run's own layers onto it; `resolveFrom(foldPrefix(a), b)` equals
 * `resolve(a ++ b)` (CPD3-16).
 */
export interface PolicyEngine<P> extends PolicyResolver {
  foldPrefix(layers: readonly ScopedLayer[]): Folded<P>;
  resolveFrom(prefix: P, rest: readonly ScopedLayer[]): Resolved;
}

/** The value a resolved config holds at `path`, if any layer set it. */
export const dimensionValue = (
  config: EffectiveRunConfig,
  path: string,
): unknown => config.dimensions[path]?.value;

/** Dimension paths the effective-set resolver reads (02-lld §5.2). */
export const TOOLS_GRANT = 'tools.grant';
export const TOOLS_EFFECTS = 'tools.effects';
export const kindAllowed = (kind: string): string =>
  `catalog.kinds.${kind}.allowed`;

/** One dimension row a domain module contributes (CPD-3's `policy-dimensions.schema.json` row). */
export interface DimensionRow {
  readonly path: string;
  readonly kind: 'ceiling' | 'choice' | 'operator';
  readonly order?: string;
  readonly within?: string;
  readonly exposure: readonly ScopeKind[];
}
