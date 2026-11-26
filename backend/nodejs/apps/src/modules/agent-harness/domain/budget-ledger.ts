/**
 * The budget ledger's rules (P6.5), pure: one account per run in a tree, each
 * holding what its whole subtree committed and reserved, so a run may reserve
 * the least, over itself and its ancestors, of `cap - committed - reserved`.
 * Python's `harness_core.budget.tree` is the same; the shared table
 * `contracts/harness/vectors/budget-ledger.json` pins both. Money is integer
 * micro-USD; a cap dimension of null draws from the parent.
 */
import { RunSpec } from '../contracts/generated/core/run-spec';
import type { ErrorCodeOf } from '../contracts/generated/ext/pipeshub/errors';
import { HarnessConflictError } from './errors';

export interface BudgetAmount {
  usdMicros: number;
  tokens: number;
}

/** A dimension of null: no limit of its own (a cap) or nothing bounds it (available). */
export interface BudgetCap {
  usdMicros: number | null;
  tokens: number | null;
}

export type BudgetAvailable = BudgetCap;

export const ZERO_AMOUNT: Readonly<BudgetAmount> = Object.freeze({
  usdMicros: 0,
  tokens: 0,
});

export const UNCAPPED: Readonly<BudgetCap> = Object.freeze({
  usdMicros: null,
  tokens: null,
});

/**
 * What the provider reported for a call, as the model gateway (or the worker's
 * own loop) settled it. The ledger is the authority on a run's usage (OWD-8):
 * a turn's usage and the grant auditor's reconciliation both read it from here.
 * `inputTokens` counts every prompt token, cached ones included.
 */
export interface BudgetUsage {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
}

export const ZERO_USAGE: Readonly<BudgetUsage> = Object.freeze({
  inputTokens: 0,
  outputTokens: 0,
  cacheReadTokens: 0,
  cacheWriteTokens: 0,
});

export const USAGE_FIELDS: readonly (keyof BudgetUsage)[] = [
  'inputTokens',
  'outputTokens',
  'cacheReadTokens',
  'cacheWriteTokens',
];

export const subtractUsage = (
  left: BudgetUsage,
  right: BudgetUsage,
): BudgetUsage => ({
  inputTokens: left.inputTokens - right.inputTokens,
  outputTokens: left.outputTokens - right.outputTokens,
  cacheReadTokens: left.cacheReadTokens - right.cacheReadTokens,
  cacheWriteTokens: left.cacheWriteTokens - right.cacheWriteTokens,
});

export const addUsage = (
  left: BudgetUsage,
  right: BudgetUsage,
): BudgetUsage => ({
  inputTokens: left.inputTokens + right.inputTokens,
  outputTokens: left.outputTokens + right.outputTokens,
  cacheReadTokens: left.cacheReadTokens + right.cacheReadTokens,
  cacheWriteTokens: left.cacheWriteTokens + right.cacheWriteTokens,
});

export interface BudgetAccount {
  orgId: string;
  runId: string;
  rootRunId: string;
  parentRunId: string | null;
  /** Ancestors, root first; empty for a root. */
  ancestors: string[];
  cap: BudgetCap;
  committed: BudgetAmount;
  reserved: BudgetAmount;
  /**
   * The usage of the run's own calls (never its children's), summed at each
   * first commit; absent on an account no commit carrying usage reached.
   */
  used?: BudgetUsage;
  /**
   * Calls of the subtree charged the model gateway's estimate and not yet
   * reconciled with the provider's usage (WP-66); absent: none.
   */
  estimated?: number;
  closed: boolean;
  openedAt: number;
}

export type ReservationState = 'open' | 'committed' | 'released' | 'expired';

export interface BudgetReservation {
  orgId: string;
  runId: string;
  rootRunId: string;
  key: string;
  /** The run and its ancestors: every account the reservation is held on. */
  accounts: string[];
  requested: BudgetAmount;
  granted: BudgetAmount;
  expiresAt: number;
  state: ReservationState;
  createdAt: number;
  /** Set once committed: what was charged and whether it crossed a cap. */
  charged?: BudgetAmount;
  crossed?: boolean;
  /** Set once committed, when the settler reported the call's usage. */
  usage?: BudgetUsage;
  /** Committed at the gateway's estimate and not yet reconciled (WP-66). */
  estimated?: boolean;
  /**
   * A hold (WP-66): the ledger's own reservation of an estimated commit's
   * bound, under `holdKey`; nobody commits, releases or reconciles it, and
   * closing the run leaves it to its own expiry (the spend may have happened).
   */
  hold?: boolean;
  /** A hold's person and agent, for the shared budgets it counts against. */
  holder?: BudgetHolder;
  /**
   * The fence of the attempt that reserved (SEC-C-20): only that attempt settles it. Absent on a reservation made
   * before the binding, which any attempt of its run settles until it lapses (at most `MAX_RESERVATION_TTL_MS`).
   */
  fence?: number;
}

/** Whose shared budgets a hold counts against (the cost ledger's attribution, without the chat). */
export interface BudgetHolder {
  userId: string;
  agentKey: string | null;
}

/**
 * What an estimated commit keeps held beyond its charge (WP-66): the rest of
 * the upper bound the provider could bill, on every account of the path and
 * past any cap, until a reconcile or `ttlMs`.
 */
export interface BudgetHold {
  amount: BudgetAmount;
  ttlMs: number;
}

/** No caller's key has a `#` (the routes refuse one), so no caller can name a hold. */
export const holdKey = (key: string): string => `${key}#hold`;

/** The hold reservation of `held`'s estimated commit. */
export function holdReservation(
  held: BudgetReservation,
  hold: BudgetHold,
  now: number,
  holder?: BudgetHolder,
): BudgetReservation {
  return {
    orgId: held.orgId,
    runId: held.runId,
    rootRunId: held.rootRunId,
    key: holdKey(held.key),
    accounts: [...held.accounts],
    requested: { ...hold.amount },
    granted: { ...hold.amount },
    expiresAt: now + Math.min(hold.ttlMs, MAX_RESERVATION_TTL_MS),
    state: 'open',
    createdAt: now,
    hold: true,
    ...(holder !== undefined ? { holder: { ...holder } } : {}),
    ...(held.fence !== undefined ? { fence: held.fence } : {}),
  };
}

/**
 * SEC-C-20 (ADR-GW-02 D5): a reservation belongs to the attempt that made it. Every claim raises the run's fence,
 * so a superseded attempt's token, still good for settling its own spend, cannot name a later attempt's
 * reservation by its key, nor a later attempt the superseded one's. The refusal reads as a missing reservation:
 * the caller learns nothing about another attempt's keys. `fence` undefined: an in-process caller with no attempt.
 */
export function assertReservationOf(
  held: BudgetReservation,
  fence: number | undefined,
): void {
  if (fence === undefined || held.fence === undefined || held.fence === fence)
    return;
  throw budgetRefused(
    'BUDGET_RESERVATION_MISSING',
    'No such reservation for this attempt',
  );
}

/** USD still held by unexpired holds, by the org, person and agent of a charge, for the shared budgets (WP-66). */
export interface HeldByScope {
  org: number;
  user: number;
  agent: number;
}

export const NOTHING_HELD: Readonly<HeldByScope> = Object.freeze({
  org: 0,
  user: 0,
  agent: 0,
});

/** What `holds` (open, unexpired) keep of the shared budgets for a charge by `holder`. */
export function heldByScope(
  holds: readonly BudgetReservation[],
  holder: BudgetHolder,
): HeldByScope {
  const held = { ...NOTHING_HELD };
  for (const hold of holds) {
    const usd = hold.granted.usdMicros;
    held.org += usd;
    if (hold.holder?.userId === holder.userId) held.user += usd;
    if (holder.agentKey !== null && hold.holder?.agentKey === holder.agentKey)
      held.agent += usd;
  }
  return held;
}

export interface BudgetRemaining {
  available: BudgetAvailable;
  committed: BudgetAmount;
  reserved: BudgetAmount;
  exhausted: boolean;
  /** The run's own calls' usage (`BudgetAccount.used`), zero when none was reported. */
  used: BudgetUsage;
  /**
   * A shared budget set to stop that leaves the run less than its own
   * account does (C9.06): its scope, as the extension that keeps it names it,
   * so a cut it made is named where the refusal was not seen (WP56-STOP-1).
   * Absent: the run's own limit binds. Set by the budget service, never by a
   * store.
   */
  budgetScope?: string;
  /** Calls of the subtree whose charge is still the gateway's estimate (`BudgetAccount.estimated`). */
  estimated: number;
}

export interface BudgetSettlement {
  reservation: BudgetReservation;
  charged: BudgetAmount;
  crossed: boolean;
  remaining: BudgetRemaining;
}

/** An account to open if missing (lazy opening walks a chain root first, P6.5 migration). */
export interface OpenBudgetAccount {
  runId: string;
  parentRunId: string | null;
  cap: BudgetCap;
}

export type BudgetRefusalCode = ErrorCodeOf<'budget'>;

export const budgetRefused = (
  code: BudgetRefusalCode,
  message: string,
): HarnessConflictError => new HarnessConflictError(code, message);

/** The longest a reservation may be held; a caller asking more gets this. */
export const MAX_RESERVATION_TTL_MS = 60 * 60 * 1000;

export const addAmounts = (
  left: BudgetAmount,
  right: BudgetAmount,
  sign: 1 | -1 = 1,
): BudgetAmount => ({
  usdMicros: left.usdMicros + sign * right.usdMicros,
  tokens: left.tokens + sign * right.tokens,
});

const least = (values: number[]): number | null =>
  values.length === 0 ? null : Math.min(...values);

type Dimension = keyof BudgetAmount;
const DIMENSIONS: readonly Dimension[] = ['usdMicros', 'tokens'];

const leftOn = (
  path: readonly BudgetAccount[],
  dimension: Dimension,
  withReserved: boolean,
): number | null =>
  least(
    path
      .filter((account) => account.cap[dimension] !== null)
      .map(
        (account) =>
          (account.cap[dimension] as number) -
          account.committed[dimension] -
          (withReserved ? account.reserved[dimension] : 0),
      ),
  );

/** `path` is the run's account first, then each ancestor up to the root. */
export function budgetAvailable(
  path: readonly BudgetAccount[],
): BudgetAvailable {
  return {
    usdMicros: leftOn(path, 'usdMicros', true),
    tokens: leftOn(path, 'tokens', true),
  };
}

export function budgetRemaining(
  path: readonly BudgetAccount[],
): BudgetRemaining {
  const own = path[0];
  if (own === undefined)
    throw new Error('A budget path has at least its own account');
  const left = budgetAvailable(path);
  const clamp = (value: number | null): number | null =>
    value === null ? null : Math.max(0, value);
  return {
    available: { usdMicros: clamp(left.usdMicros), tokens: clamp(left.tokens) },
    committed: { ...own.committed },
    reserved: { ...own.reserved },
    used: { ...ZERO_USAGE, ...(own.used ?? {}) },
    estimated: own.estimated ?? 0,
    exhausted: DIMENSIONS.some((d) => {
      const unspent = leftOn(path, d, false);
      return unspent !== null && unspent <= 0;
    }),
  };
}

/**
 * What a reservation gets: `min(requested, available)` per dimension once the
 * available amount covers `minimum`. A dimension asked for nothing is not
 * checked (a route the org does not pay for reserves no USD). Committed spend
 * at a cap is `BUDGET_EXHAUSTED`; less left than the minimum (reservations may
 * still return) is `BUDGET_INSUFFICIENT`.
 */
export function grantReservation(
  path: readonly BudgetAccount[],
  requested: BudgetAmount,
  minimum: BudgetAmount,
): BudgetAmount {
  const granted: BudgetAmount = { ...ZERO_AMOUNT };
  for (const dimension of DIMENSIONS) {
    const asked = requested[dimension];
    const floor = minimum[dimension];
    const left = leftOn(path, dimension, true);
    if (left === null || (asked === 0 && floor === 0)) {
      granted[dimension] = asked;
      continue;
    }
    const unspent = leftOn(path, dimension, false);
    if (unspent !== null && unspent <= 0) {
      throw budgetRefused(
        'BUDGET_EXHAUSTED',
        "The run's tree has spent its budget",
      );
    }
    if (Math.max(left, 0) < floor) {
      throw budgetRefused(
        'BUDGET_INSUFFICIENT',
        'Less budget is left than the call needs',
      );
    }
    granted[dimension] = Math.min(asked, Math.max(left, 0));
  }
  return granted;
}

const reached = (account: BudgetAccount): boolean[] =>
  DIMENSIONS.map(
    (d) => account.cap[d] !== null && account.committed[d] >= account.cap[d],
  );

/** A settlement crossed when an account on the path reached a cap it was below. */
export function budgetCrossed(
  before: readonly BudgetAccount[],
  after: readonly BudgetAccount[],
): boolean {
  return before.some((account, index) => {
    const was = reached(account);
    const now = reached(after[index] ?? account);
    return now.some((hit, d) => hit && !was[d]);
  });
}

/**
 * The cap a child's account opens with, or `BUDGET_SHARE_EXCEEDS_REMAINING`:
 * an explicit share must fit in what the parent may still reserve; a dimension
 * without one draws from the parent, which must have something left.
 */
export function reserveForChild(
  parent: BudgetAvailable,
  share: BudgetCap,
): BudgetCap {
  for (const dimension of DIMENSIONS) {
    const left = parent[dimension];
    const asked = share[dimension];
    if (left === null) continue;
    if ((asked !== null && asked > left) || (asked === null && left <= 0)) {
      throw budgetRefused(
        'BUDGET_SHARE_EXCEEDS_REMAINING',
        "The child's budget is more than its parent has left",
      );
    }
  }
  return { ...share };
}

/** Pinned USD as micro-USD (Python's `usd_micros`). */
export const usdMicros = (usd: number): number => Math.round(usd * 1_000_000);

/** The budget a run pinned, as a cap (`maxCostUsd`, `maxTokens`). */
export function pinnedCap(spec: Pick<RunSpec, 'budgets'>): BudgetCap {
  return {
    usdMicros: usdMicros(spec.budgets.maxCostUsd),
    tokens: spec.budgets.maxTokens,
  };
}

/**
 * A child pinned with its parent's own budget draws from the parent (an equal
 * cap can never bind: the parent's account holds the child's spend too); a
 * smaller one is the child's share.
 */
export function childCap(parent: BudgetCap, child: BudgetCap): BudgetCap {
  const own = (held: number | null, asked: number | null): number | null =>
    asked === null || (held !== null && asked >= held) ? null : asked;
  return {
    usdMicros: own(parent.usdMicros, child.usdMicros),
    tokens: own(parent.tokens, child.tokens),
  };
}

export const budgetAccountId = (orgId: string, runId: string): string =>
  `${orgId}:${runId}`;

export const budgetReservationId = (
  orgId: string,
  runId: string,
  key: string,
): string => `${orgId}:${runId}:${key}`;

/** A new account under `parent` (null for a root), with `opening` spend already committed. */
export function newBudgetAccount(
  orgId: string,
  open: OpenBudgetAccount,
  parent: BudgetAccount | null,
  now: number,
): BudgetAccount {
  return {
    orgId,
    runId: open.runId,
    rootRunId: parent?.rootRunId ?? open.runId,
    parentRunId: parent?.runId ?? null,
    ancestors: parent === null ? [] : [...parent.ancestors, parent.runId],
    cap: { ...open.cap },
    committed: { ...ZERO_AMOUNT },
    reserved: { ...ZERO_AMOUNT },
    closed: false,
    openedAt: now,
  };
}

/** A path's run ids: the run, then its ancestors nearest first. */
export const pathRunIds = (account: BudgetAccount): string[] => [
  account.runId,
  ...[...account.ancestors].reverse(),
];

export function assertUsage(usage: BudgetUsage): BudgetUsage {
  if (
    USAGE_FIELDS.some(
      (field) => !Number.isSafeInteger(usage[field]) || usage[field] < 0,
    )
  ) {
    throw new HarnessConflictError(
      'INVALID_COMMIT',
      'A call usage is a non-negative integer per field',
    );
  }
  return usage;
}

export function assertAmount(amount: BudgetAmount): BudgetAmount {
  if (
    !Number.isSafeInteger(amount.usdMicros) ||
    !Number.isSafeInteger(amount.tokens) ||
    amount.usdMicros < 0 ||
    amount.tokens < 0
  ) {
    throw new HarnessConflictError(
      'INVALID_COMMIT',
      'A budget amount is a non-negative integer',
    );
  }
  return amount;
}
