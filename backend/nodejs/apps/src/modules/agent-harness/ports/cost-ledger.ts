import type { BudgetAmount, HeldByScope } from '../domain/budget-ledger';
import type {
  CostAttribution,
  CostEntry,
  SharedBudgetLeft,
} from '../domain/cost-ledger';
import type { ModelRole } from '../domain/model-roles';

/** Writes a charged call's entry inside the transaction that commits its reservation (`Tx` is the store's). */
export interface CostEntryWriter<Tx> {
  insertIn(tx: Tx, entry: CostEntry): Promise<void>;
  /** The role a call's own entry was charged under, so its reconcile counts under the same one; absent: none. */
  roleIn(
    tx: Tx,
    orgId: string,
    runId: string,
    key: string,
  ): Promise<ModelRole | undefined>;
}

/**
 * The most one run of a schedule may budget, where something beyond the
 * release ceiling bounds it. Bound to `AGENT_HARNESS_RUN_BUDGET_CEILING`;
 * unbound, only the release ceiling is checked.
 */
export interface RunBudgetCeiling {
  /** Null: nothing bounds a run of the org (and agent) here. `why` ends the refusal, naming what bounds it. */
  ceiling(
    orgId: string,
    agentKey: string | null,
  ): Promise<{ usdMicros: number; why: string } | null>;
}

/**
 * What the budget ledger asks, at each reservation and commit, of budgets
 * beyond the run's own that runs share. `admit` may lower a reservation or refuse it with
 * `SharedBudgetReached`; `stoppingLeft` names the budget that leaves a run
 * less than its own account; `foldSoon` follows a commit that wrote an entry
 * and never throws. Bound to `AGENT_HARNESS_SHARED_BUDGET_GUARD`; unbound,
 * `noSharedBudgets`.
 */
export interface SharedBudgetGuard {
  /** Whether spend is counted: false, and the budget ledger neither asks this guard nor writes an entry, so a reservation costs no read. */
  readonly recordsSpend: boolean;
  admit(
    orgId: string,
    attribution: CostAttribution,
    requested: BudgetAmount,
    minimum: BudgetAmount,
    held?: () => Promise<HeldByScope>,
  ): Promise<BudgetAmount>;
  stoppingLeft(
    orgId: string,
    attribution: CostAttribution,
    held?: () => Promise<HeldByScope>,
  ): Promise<SharedBudgetLeft>;
  foldSoon(orgId: string): Promise<void>;
}

/** No shared budget: every reservation is admitted as asked, and only the run's own budget bounds it. */
export const noSharedBudgets: SharedBudgetGuard = Object.freeze({
  recordsSpend: false,
  admit: (
    _orgId: string,
    _attribution: CostAttribution,
    requested: BudgetAmount,
  ): Promise<BudgetAmount> => Promise.resolve(requested),
  stoppingLeft: (): Promise<SharedBudgetLeft> =>
    Promise.resolve({ left: null, scope: null }),
  foldSoon: (): Promise<void> => Promise.resolve(),
});

/**
 * What the retention sweep and a person's erasure ask of the spend records: `sweep` finishes what a lost commit left, `eraseUser` removes a
 * deleted person's rows. Each answers how many rows it moved. Bound to
 * `AGENT_HARNESS_COST_ERASER`; unbound, `noCostEraser`.
 */
export interface CostEraser {
  sweep(now: number): Promise<number>;
  eraseUser(orgId: string, userId: string): Promise<number>;
}

/** No spend records are kept, so there is nothing to sweep or erase. */
export const noCostEraser: CostEraser = Object.freeze({
  sweep: (): Promise<number> => Promise.resolve(0),
  eraseUser: (): Promise<number> => Promise.resolve(0),
});
