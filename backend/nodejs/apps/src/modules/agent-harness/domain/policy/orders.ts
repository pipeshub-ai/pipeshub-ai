/**
 * The closed set of policy orders (R2 CP3, 02-lld §3.2), each implemented once
 * per language: Python's `harness_core.policy.orders` is the twin, and
 * `contracts/harness/vectors/policy-resolve.json` holds the two to one answer.
 * `leq(a, b)` is "a is at or below b" (a may follow b); `meet` is the greatest
 * value at or below both, `undefined` where none exists (two unrelated modes).
 * Null is unbounded (the top) in every order that admits it.
 */
import type { PolicyDimensions } from '../../contracts/generated/core/policy/policy-dimensions';
import {
  approvalCeiling,
  approvalsWithin,
  meetApprovals,
  normalizeRules,
  type EffectiveApprovals,
  resolvedCeiling,
} from './approval-rules';
import { canonicalJson } from '../../contracts/canonical-json';

export type OrderSpec = NonNullable<
  PolicyDimensions['dimensions'][number]['order']
>;
export type OrderKind = OrderSpec['kind'];

/** Why a value is not at or below another: `at` names a member of a composite value. */
export interface Widened {
  at?: string;
  detail: string;
}

export interface Order {
  readonly kind: OrderKind;
  /** A layer's value in the order's own terms (approval rules normalize); throws `InvalidPolicyValue`. */
  lift(value: unknown): unknown;
  /** null when `a` is at or below `b`, else why not. */
  leq(a: unknown, b: unknown): Widened | null;
  /** What the dimension becomes when `a` narrows `b` (`leq(a, b)` holds). */
  narrow(a: unknown, b: unknown): unknown;
  meet(a: unknown, b: unknown): unknown;
  /** Whether a choice lies within this ceiling. */
  contains(ceiling: unknown, choice: unknown): boolean;
}

export class InvalidPolicyValue extends Error {}

const same = (a: unknown, b: unknown): boolean =>
  canonicalJson(a) === canonicalJson(b);

const show = (value: unknown): string =>
  typeof value === 'string' ? value : JSON.stringify(value);

const sortedStrings = (value: unknown): string[] => {
  if (!Array.isArray(value) || !value.every((v) => typeof v === 'string')) {
    throw new InvalidPolicyValue('not a list of names');
  }
  return [...new Set(value)].sort();
};

const numberOrNull = (value: unknown): number | null => {
  if (value === null || typeof value === 'number') return value;
  throw new InvalidPolicyValue('not a number');
};

const minOrder: Order = {
  kind: 'min',
  lift: numberOrNull,
  leq: (a, b) => {
    const x = numberOrNull(a);
    const y = numberOrNull(b);
    if (y === null || (x !== null && x <= y)) return null;
    return { detail: `${show(x)} is above ${show(y)}` };
  },
  narrow: (a) => a,
  meet: (a, b) => {
    const x = numberOrNull(a);
    const y = numberOrNull(b);
    return x === null ? y : y === null ? x : Math.min(x, y);
  },
  contains: (ceiling, choice) => minOrder.leq(choice, ceiling) === null,
};

const subsetOrder: Order = {
  kind: 'subset',
  lift: (value) => (value === null ? null : sortedStrings(value)),
  leq: (a, b) => {
    if (b === null) return null;
    if (a === null) return { detail: 'any is not within the ceiling' };
    const held = new Set(sortedStrings(b));
    const extra = sortedStrings(a).filter((item) => !held.has(item));
    return extra.length === 0
      ? null
      : { detail: `${extra.join(',')} not within the ceiling` };
  },
  narrow: (a) => (a === null ? null : sortedStrings(a)),
  meet: (a, b) => {
    if (a === null) return b === null ? null : sortedStrings(b);
    if (b === null) return sortedStrings(a);
    const held = new Set(sortedStrings(b));
    return sortedStrings(a).filter((item) => held.has(item));
  },
  // A choice names one member (a string), several (a list) or one per role (a map).
  contains: (ceiling, choice) => {
    if (ceiling === null) return true;
    const held = new Set(sortedStrings(ceiling));
    const named =
      typeof choice === 'string'
        ? [choice]
        : Array.isArray(choice)
          ? choice
          : typeof choice === 'object' && choice !== null
            ? Object.values(choice)
            : [choice];
    return named.every((item) => typeof item === 'string' && held.has(item));
  },
};

/** The key of a `min-map` that bounds every other key. */
export const MIN_MAP_DEFAULT = 'default';

const MIN_MAP_KEY = /^[a-z0-9][a-z0-9_.-]{0,63}$/;

/**
 * `min-map`: a number per key, each a `min` ceiling of its own, and `default`
 * bounding every key (a key's bound is the lower of its own and `default`).
 * Null is the top (no bound). Values are kept canonical: an entry that does
 * not lower its key's bound is dropped, an empty map is null.
 */
const minMap = (value: unknown): Record<string, number> | null => {
  if (value === null) return null;
  if (typeof value !== 'object' || Array.isArray(value)) {
    throw new InvalidPolicyValue('not a map of numbers');
  }
  const entries = Object.entries(value as Record<string, unknown>);
  for (const [key, bound] of entries) {
    if (!MIN_MAP_KEY.test(key)) {
      throw new InvalidPolicyValue(`${key} is not a key`);
    }
    if (typeof bound !== 'number' || !Number.isFinite(bound) || bound < 0) {
      throw new InvalidPolicyValue(`${key} is not bounded by a number`);
    }
  }
  const all = (value as Record<string, number>)[MIN_MAP_DEFAULT];
  const kept = entries
    .filter(
      ([key, bound]) =>
        key === MIN_MAP_DEFAULT || all === undefined || (bound as number) < all,
    )
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  return kept.length === 0
    ? null
    : (Object.fromEntries(kept) as Record<string, number>);
};

/** The bound a `min-map` value holds for a key; null: none. */
export function minMapBound(value: unknown, key: string): number | null {
  const map = minMap(value);
  if (map === null) return null;
  const own = map[key];
  const all = map[MIN_MAP_DEFAULT];
  if (own === undefined) return all ?? null;
  return all === undefined ? own : Math.min(own, all);
}

const minMapKeys = (...values: (Record<string, number> | null)[]): string[] =>
  [...new Set(values.flatMap((value) => Object.keys(value ?? {})))].sort();

const minMapOrder: Order = {
  kind: 'min-map',
  lift: minMap,
  leq: (a, b) => {
    const x = minMap(a);
    const y = minMap(b);
    for (const key of minMapKeys(x, y)) {
      const mine = minMapBound(x, key);
      const theirs = minMapBound(y, key);
      if (theirs !== null && (mine === null || mine > theirs)) {
        return {
          at: key,
          detail: `${mine === null ? 'no bound' : show(mine)} is above ${show(theirs)}`,
        };
      }
    }
    return null;
  },
  narrow: (a) => minMap(a),
  meet: (a, b) => {
    const x = minMap(a);
    const y = minMap(b);
    const out: Record<string, number> = {};
    for (const key of minMapKeys(x, y)) {
      const mine = minMapBound(x, key);
      const theirs = minMapBound(y, key);
      const bound =
        mine === null
          ? theirs
          : theirs === null
            ? mine
            : Math.min(mine, theirs);
      if (bound !== null) out[key] = bound;
    }
    return minMap(out);
  },
  contains: (ceiling, choice) => minMapOrder.leq(choice, ceiling) === null,
};

/** `rank`: groups from the narrowest up; `rank-raise`: from the widest up. */
function rankOrder(spec: OrderSpec, raise: boolean): Order {
  const groups = spec.ranks ?? [];
  const rankOf = new Map<string, number>();
  groups.forEach((group, index) => {
    for (const value of group)
      rankOf.set(value, raise ? groups.length - 1 - index : index);
  });
  const rank = (value: unknown): number => {
    const found = typeof value === 'string' ? rankOf.get(value) : undefined;
    if (found === undefined)
      throw new InvalidPolicyValue(`${show(value)} is not ranked`);
    return found;
  };
  // Equal ranks meet to the value listed first in their group, so meet commutes.
  const listed = groups.flat();
  const order: Order = {
    kind: raise ? 'rank-raise' : 'rank',
    lift: (value) => {
      if (value !== null) rank(value);
      return value;
    },
    leq: (a, b) => {
      if (b === null || (a !== null && rank(a) <= rank(b))) return null;
      return {
        detail: raise
          ? `${show(a)} is looser than ${show(b)}`
          : `${show(a)} is above ${show(b)}`,
      };
    },
    narrow: (a) => a,
    meet: (a, b) => {
      if (a === null) return b;
      if (b === null) return a;
      const ra = rank(a);
      const rb = rank(b);
      if (ra !== rb) return ra < rb ? a : b;
      return listed.indexOf(a as string) <= listed.indexOf(b as string) ? a : b;
    },
    contains: (ceiling, choice) => order.leq(choice, ceiling) === null,
  };
  return order;
}

function boolOrder(narrowValue: boolean): Order {
  const bool = (value: unknown): boolean => {
    if (typeof value !== 'boolean')
      throw new InvalidPolicyValue('not a switch');
    return value;
  };
  const order: Order = {
    kind: narrowValue ? 'bool-or' : 'bool-and',
    lift: bool,
    leq: (a, b) =>
      bool(a) === bool(b) || bool(a) === narrowValue
        ? null
        : { detail: `${show(a)} widens ${show(b)}` },
    narrow: (a) => a,
    meet: (a, b) =>
      bool(a) === narrowValue || bool(b) === narrowValue
        ? narrowValue
        : !narrowValue,
    contains: (ceiling, choice) => order.leq(choice, ceiling) === null,
  };
  return order;
}

/** Values are unordered except a `bottom` below each; null (when admitted) is the top. */
function flatOrder(spec: OrderSpec): Order {
  const bottom = spec.bottom;
  const order: Order = {
    kind: 'flat',
    lift: (value) => value,
    leq: (a, b) =>
      b === null || same(a, b) || (bottom !== undefined && a === bottom)
        ? null
        : { detail: `${show(a)} is not within ${show(b)}` },
    narrow: (a) => a,
    meet: (a, b) => {
      if (a === null || same(a, b)) return b;
      if (b === null) return a;
      return bottom;
    },
    contains: (ceiling, choice) => order.leq(choice, ceiling) === null,
  };
  return order;
}

/** Each value narrows to itself and to every value below it in `children`. */
function treeOrder(spec: OrderSpec): Order {
  const children = spec.children ?? {};
  const below = (value: string): Set<string> => {
    const out = new Set<string>([value]);
    for (const child of children[value] ?? []) {
      for (const v of below(child)) out.add(v);
    }
    return out;
  };
  const name = (value: unknown): string => {
    if (typeof value !== 'string') throw new InvalidPolicyValue('not a name');
    return value;
  };
  const order: Order = {
    kind: 'tree',
    lift: name,
    leq: (a, b) =>
      below(name(b)).has(name(a))
        ? null
        : { detail: `${show(a)} is not within ${show(b)}` },
    narrow: (a) => a,
    meet: (a, b) => {
      if (below(name(b)).has(name(a))) return a;
      if (below(name(a)).has(name(b))) return b;
      return undefined;
    },
    contains: (ceiling, choice) => order.leq(choice, ceiling) === null,
  };
  return order;
}

/** A record whose boolean `switch` turns numeric `bounds` on; switched off, it is `off`. */
function gatedOrder(spec: OrderSpec): Order {
  const flag = spec.switch ?? 'enabled';
  const bounds = spec.bounds ?? [];
  const off = spec.off ?? { [flag]: false };
  const record = (value: unknown): Record<string, unknown> => {
    if (typeof value !== 'object' || value === null || Array.isArray(value)) {
      throw new InvalidPolicyValue('not a record');
    }
    return value as Record<string, unknown>;
  };
  const isOn = (value: unknown): boolean => record(value)[flag] === true;
  const order: Order = {
    kind: 'gated',
    lift: record,
    leq: (a, b) => {
      if (!isOn(a)) return null;
      if (!isOn(b)) return { at: flag, detail: 'switched off above' };
      for (const key of bounds) {
        const x = record(a)[key] as number;
        const y = record(b)[key] as number;
        if (x > y) return { at: key, detail: `${show(x)} is above ${show(y)}` };
      }
      return null;
    },
    narrow: (a) => (isOn(a) ? { ...record(a) } : { ...off }),
    meet: (a, b) => {
      if (!isOn(a) || !isOn(b)) return { ...off };
      const met: Record<string, unknown> = { ...record(a) };
      for (const key of bounds) {
        met[key] = Math.min(record(a)[key] as number, record(b)[key] as number);
      }
      return met;
    },
    contains: (ceiling, choice) => order.leq(choice, ceiling) === null,
  };
  return order;
}

/**
 * A layer admits items `{kind, version}` by selectors (`kind` or
 * `kind@version`); the item itself is chosen elsewhere and never changes here.
 */
const admitsOrder: Order = {
  kind: 'admits',
  lift: sortedStrings,
  leq: (selectors, item) => {
    const { kind, version } = item as { kind: string; version: string };
    const admitted = sortedStrings(selectors);
    return admitted.includes(kind) || admitted.includes(`${kind}@${version}`)
      ? null
      : { detail: `${kind}@${version} is not admitted` };
  },
  narrow: (_selectors, item) => item,
  meet: () => undefined,
  contains: (selectors, item) => admitsOrder.leq(selectors, item) === null,
};

const approvalRulesOrder: Order = {
  kind: 'approval-rules',
  lift: (value) => {
    const rules = normalizeRules(value);
    if (rules !== null) return approvalCeiling(rules);
    const ceiling = resolvedCeiling(value);
    if (ceiling === null) throw new InvalidPolicyValue('not approval rules');
    return ceiling;
  },
  leq: (a, b) =>
    approvalsWithin(a as EffectiveApprovals, b as EffectiveApprovals)
      ? null
      : { detail: 'decides a call more loosely' },
  narrow: (a, b) =>
    b === null
      ? a
      : meetApprovals(a as EffectiveApprovals, b as EffectiveApprovals),
  meet: (a, b) =>
    meetApprovals(a as EffectiveApprovals, b as EffectiveApprovals),
  contains: (ceiling, choice) =>
    approvalsWithin(
      choice as EffectiveApprovals,
      ceiling as EffectiveApprovals,
    ),
};

/**
 * A rank per rule key `{rule}:{subject}`, raised only (from the widest up, as
 * `rank-raise`). `{rule}:*` binds every subject of that rule, so a value's
 * rank for a key is the higher of its own and its wildcard's. Meet keeps the
 * higher rank per key; null is the top (no rule).
 */
function rankRaiseRulesOrder(spec: OrderSpec): Order {
  const groups = spec.ranks ?? [];
  const rankOf = new Map<string, number>();
  groups.forEach((group, index) => {
    for (const value of group) rankOf.set(value, groups.length - 1 - index);
  });
  const looseness = (value: unknown): number => {
    const found = typeof value === 'string' ? rankOf.get(value) : undefined;
    if (found === undefined)
      throw new InvalidPolicyValue(`${show(value)} is not ranked`);
    return found;
  };
  const rules = (value: unknown): Record<string, string> => {
    if (typeof value !== 'object' || value === null || Array.isArray(value)) {
      throw new InvalidPolicyValue('not a set of rules');
    }
    const out: Record<string, string> = {};
    for (const [key, level] of Object.entries(value)) {
      if (!/^[A-Za-z0-9_.-]+:(\*|[A-Za-z0-9_.-]+)$/.test(key)) {
        throw new InvalidPolicyValue(`${key} is not a rule key`);
      }
      looseness(level);
      out[key] = level as string;
    }
    return out;
  };
  const wildcard = (key: string): string =>
    `${key.slice(0, key.indexOf(':'))}:*`;
  // The level a value sets for a key, as looseness (lower is stricter); none: unbounded.
  const levelAt = (
    value: Record<string, string>,
    key: string,
  ): number | null => {
    const own = value[key] === undefined ? null : looseness(value[key]);
    const wild =
      key.endsWith(':*') || value[wildcard(key)] === undefined
        ? null
        : looseness(value[wildcard(key)]);
    if (own === null) return wild;
    return wild === null ? own : Math.min(own, wild);
  };
  const meet = (a: unknown, b: unknown): unknown => {
    if (a === null) return b === null ? null : rules(b);
    if (b === null) return rules(a);
    const x = rules(a);
    const y = rules(b);
    const out: Record<string, string> = {};
    for (const key of [
      ...new Set([...Object.keys(x), ...Object.keys(y)]),
    ].sort()) {
      const left = x[key];
      const right = y[key];
      if (left === undefined || right === undefined) {
        const only = left ?? right;
        if (only !== undefined) out[key] = only;
        continue;
      }
      out[key] = looseness(left) <= looseness(right) ? left : right;
    }
    return out;
  };
  const order: Order = {
    kind: 'rank-raise-rules',
    lift: (value) => (value === null ? null : rules(value)),
    leq: (a, b) => {
      if (b === null) return null;
      const ceiling = rules(b);
      const keys = Object.keys(ceiling).sort();
      if (a === null) {
        return keys.length === 0
          ? null
          : { at: keys[0], detail: 'no rule is looser than the ceiling' };
      }
      const value = rules(a);
      for (const key of keys) {
        const level = levelAt(value, key);
        if (level === null || level > looseness(ceiling[key])) {
          return {
            at: key,
            detail: `${show(value[key] ?? 'no rule')} is looser than ${show(ceiling[key])}`,
          };
        }
      }
      return null;
    },
    narrow: meet,
    meet,
    contains: (ceiling, choice) => order.leq(choice, ceiling) === null,
  };
  return order;
}

/** The order a dimension row names, built from its parameters. */
export function orderOf(spec: OrderSpec): Order {
  switch (spec.kind) {
    case 'min':
      return minOrder;
    case 'subset':
      return subsetOrder;
    case 'min-map':
      return minMapOrder;
    case 'rank':
      return rankOrder(spec, false);
    case 'rank-raise':
      return rankOrder(spec, true);
    case 'bool-and':
      return boolOrder(false);
    case 'bool-or':
      return boolOrder(true);
    case 'flat':
      return flatOrder(spec);
    case 'tree':
      return treeOrder(spec);
    case 'gated':
      return gatedOrder(spec);
    case 'admits':
      return admitsOrder;
    case 'approval-rules':
      return approvalRulesOrder;
    case 'rank-raise-rules':
      return rankRaiseRulesOrder(spec);
  }
}
