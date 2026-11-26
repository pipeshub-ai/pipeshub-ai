/**
 * A charged call's spend entry, pure: every charged model call of a run as
 * one entry, written in the transaction that commits its budget reservation
 * (so once), with the run's attribution. What an entry is counted against
 * beyond the run's own budget is an extension point's (`SharedBudgetGuard`).
 * Money is integer micro-USD, as in the budget ledger.
 */
import {
  budgetReservationId,
  type BudgetAmount,
  type BudgetUsage,
} from './budget-ledger';
import { HarnessConflictError } from './errors';
import type { ModelRole } from './model-roles';

/** A month of spend: `YYYY-MM` in UTC. */
export type CostPeriod = string;

export const costPeriodOf = (at: number): CostPeriod =>
  new Date(at).toISOString().slice(0, 7);

/**
 * Which budget beyond the run's own a charge is counted against, as the
 * extension that keeps such budgets names it; opaque here
 * (`BUDGET_SCOPE_PATTERN`).
 */
export type BudgetScope = string;

/** The shape of a budget scope on the wire and in a saved message. */
export const BUDGET_SCOPE_PATTERN = /^[a-z][a-z0-9_-]{0,31}$/;

export const isBudgetScope = (value: unknown): value is BudgetScope =>
  typeof value === 'string' && BUDGET_SCOPE_PATTERN.test(value);

/** Who a charge is for, read from the run itself (its session, actor and agent), never from the caller. */
export interface CostAttribution {
  sessionId: string;
  userId: string;
  agentKey: string | null;
  /** GA-15: the role the call was for; absent (an entry written before roles): primary. */
  modelRole?: ModelRole;
}

/**
 * One charged model call. `_id` is the reservation's id: a commit writes it
 * once. A reconcile (WP-66) writes one more entry under `reconciledKey`: the
 * difference between the provider's usage and the estimate charged, which
 * counts no call and takes the call out of `estimatedCalls`.
 */
export interface CostEntry extends CostAttribution {
  orgId: string;
  runId: string;
  key: string;
  period: CostPeriod;
  at: number;
  usdMicros: number;
  tokens: number;
  usage: BudgetUsage;
  /** Model calls it counts: 1, or 0 for a reconcile's difference; absent on entries before WP-66 (1). */
  calls?: number;
  /** 1: charged the gateway's estimate (a cut call); -1: that estimate's reconcile; absent: 0. */
  estimatedCalls?: number;
  /** Whether the totals hold it yet; the fold sets it in the transaction that adds it. */
  folded: boolean;
}

/** The key of a reconcile's difference entry: no caller's key has a `#`. */
export const reconciledKey = (key: string): string => `${key}#reconciled`;

export const costEntryId = budgetReservationId;

export function costEntry(
  orgId: string,
  runId: string,
  key: string,
  charged: BudgetAmount,
  usage: BudgetUsage,
  attribution: CostAttribution,
  at: number,
  estimated = false,
): CostEntry {
  return {
    orgId,
    runId,
    key,
    sessionId: attribution.sessionId,
    userId: attribution.userId,
    agentKey: attribution.agentKey,
    modelRole: attribution.modelRole ?? 'primary',
    period: costPeriodOf(at),
    at,
    usdMicros: charged.usdMicros,
    tokens: charged.tokens,
    usage: { ...usage },
    calls: 1,
    estimatedCalls: estimated ? 1 : 0,
    folded: false,
  };
}

/** A reconcile's entry (WP-66): what the provider's usage adds to, or takes from, the estimate charged. */
export function reconcileEntry(
  orgId: string,
  runId: string,
  key: string,
  difference: BudgetAmount,
  usage: BudgetUsage,
  attribution: CostAttribution,
  at: number,
): CostEntry {
  return {
    ...costEntry(
      orgId,
      runId,
      reconciledKey(key),
      difference,
      usage,
      attribution,
      at,
    ),
    calls: 0,
    estimatedCalls: -1,
  };
}

export interface SharedBudgetLeft {
  /** Micro-USD left under the tightest budget; null: no budget bounds the charge. */
  left: number | null;
  /** The scope whose budget is tightest; null with `left`. */
  scope: BudgetScope | null;
}

/**
 * A reservation refused by a budget beyond the run's own (BU-03): the budget
 * ledger's own codes, the scope that refused it, and the words and finer
 * machine reason the extension that keeps the budget gives.
 */
export class SharedBudgetReached extends HarnessConflictError {
  constructor(
    code: 'BUDGET_EXHAUSTED' | 'BUDGET_INSUFFICIENT',
    readonly scope: BudgetScope,
    message: string,
    reasonCode: string,
  ) {
    super(code, message, reasonCode);
    this.name = 'SharedBudgetReached';
  }
}
