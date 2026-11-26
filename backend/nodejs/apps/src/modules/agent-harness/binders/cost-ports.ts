import type { interfaces } from 'inversify';
import {
  noSharedBudgets,
  noCostEraser,
  type CostEraser,
  type SharedBudgetGuard,
  type RunBudgetCeiling,
} from '../ports/cost-ledger';
import { boundOrNull } from './context';
import {
  AGENT_HARNESS_COST_ERASER,
  AGENT_HARNESS_SHARED_BUDGET_GUARD,
  AGENT_HARNESS_RUN_BUDGET_CEILING,
} from './tokens';

/** What an edition binder bound for budgets runs share, else none. */
export const sharedBudgetGuardOf = (
  container: interfaces.Container,
): SharedBudgetGuard =>
  boundOrNull<SharedBudgetGuard>(
    container,
    AGENT_HARNESS_SHARED_BUDGET_GUARD,
  ) ?? noSharedBudgets;

/** What an edition binder bound for the spend records, else nothing to sweep or erase. */
export const costEraserOf = (container: interfaces.Container): CostEraser =>
  boundOrNull<CostEraser>(container, AGENT_HARNESS_COST_ERASER) ?? noCostEraser;

/** The ceiling a schedule's run budget is checked against; null: the release ceiling only. */
export const runBudgetCeilingOf = (
  container: interfaces.Container,
): RunBudgetCeiling | null =>
  boundOrNull<RunBudgetCeiling>(container, AGENT_HARNESS_RUN_BUDGET_CEILING);
