import type {
  BudgetAccount,
  BudgetAmount,
  BudgetCap,
  BudgetHold,
  BudgetHolder,
  BudgetRemaining,
  BudgetReservation,
  BudgetSettlement,
  BudgetUsage,
  HeldByScope,
  OpenBudgetAccount,
} from '../domain/budget-ledger';
import type { CostAttribution } from '../domain/cost-ledger';

export interface ReserveBudget {
  orgId: string;
  runId: string;
  /** Idempotency key, per run: the same key again returns the same reservation. */
  key: string;
  requested: BudgetAmount;
  minimum: BudgetAmount;
  ttlMs: number;
  /**
   * The reserving attempt's fence (SEC-C-20): the reservation is bound to it, and the same key from another
   * attempt is refused. Absent only for an in-process caller with no attempt.
   */
  fence?: number;
}

/** A child's account, opened in the transaction that admits the child (P6.5). */
export interface ChildBudgetAccount {
  /** The parent and its ancestors root first, opened if missing (runs admitted before P6.5). */
  chain: OpenBudgetAccount[];
  /** The child's own cap: null dimensions draw from the parent. */
  cap: BudgetCap;
}

/**
 * The budget ledger (P6.5): one account per run in a tree, reservations that
 * expire, idempotent settlement. Every refusal is a `HarnessConflictError`
 * with a `BUDGET_*` code. Expired reservations are returned by the next
 * operation on their tree, inside its transaction.
 */
export interface BudgetLedgerStore {
  account(orgId: string, runId: string): Promise<BudgetAccount | null>;
  /** Opens each missing account of `chain` (root first) with no share check; `opening` on the last. */
  ensure(
    orgId: string,
    chain: readonly OpenBudgetAccount[],
    opening?: BudgetAmount,
  ): Promise<void>;
  openRoot(
    orgId: string,
    runId: string,
    cap: BudgetCap,
    opening?: BudgetAmount,
  ): Promise<BudgetRemaining>;
  /** `reserveForChild` decides; idempotent on the child. */
  openChild(
    orgId: string,
    parentRunId: string,
    childRunId: string,
    cap: BudgetCap,
  ): Promise<BudgetRemaining>;
  reserve(
    command: ReserveBudget,
  ): Promise<{ reservation: BudgetReservation; remaining: BudgetRemaining }>;
  /**
   * `usage`, when given, is added to the run's own `used` with the first
   * commit only; `charge`, when given, names who the call is for, and the
   * first commit writes the call's cost entry in the same transaction (C9.06).
   * `hold` (WP-66): `actual` is the model gateway's estimate of a cut call;
   * the call counts in `estimated` and `hold.amount` stays reserved on the
   * path, past any cap, until a reconcile or its time.
   */
  commit(
    orgId: string,
    runId: string,
    key: string,
    actual: BudgetAmount,
    usage?: BudgetUsage,
    charge?: CostAttribution,
    hold?: BudgetHold,
    fence?: number,
  ): Promise<BudgetSettlement>;
  /**
   * The provider's own usage for an estimated commit (WP-66): `actual`
   * replaces the estimate charged (either way), `usage` its usage, and the
   * hold goes back; with `charge`, the difference is a cost entry in the same
   * transaction. Once: a commit not estimated, or reconciled, is left as it
   * is; a reservation never committed is `BUDGET_RESERVATION_SETTLED`.
   *
   * `fence`, on every settlement: the settling attempt's. A reservation another attempt made is
   * `BUDGET_RESERVATION_MISSING` (SEC-C-20, `assertReservationOf`).
   */
  reconcile(
    orgId: string,
    runId: string,
    key: string,
    actual: BudgetAmount,
    usage?: BudgetUsage,
    charge?: CostAttribution,
    fence?: number,
  ): Promise<BudgetSettlement>;
  /** USD the org's unexpired holds keep of the shared budgets a charge by `holder` counts against (WP-66). */
  held(orgId: string, holder: BudgetHolder): Promise<HeldByScope>;
  release(
    orgId: string,
    runId: string,
    key: string,
    fence?: number,
  ): Promise<BudgetSettlement>;
  remaining(orgId: string, runId: string): Promise<BudgetRemaining>;
  /**
   * The run ended (CLEANUP-S4): its account takes no new reservation and what it still reserves is expired. The
   * account as closed, so the run's end reads it no second time (its cache-read share, C5.05); null for a run that
   * has none.
   */
  close(orgId: string, runId: string): Promise<BudgetAccount | null>;
}
