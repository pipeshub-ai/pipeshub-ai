import type { BudgetScope } from './types';

/** The stop line and the next step shown when a budget beyond the run's own stopped an answer. */
export interface BudgetStopLines {
  stopped: string;
  hint: string;
}

export type BudgetStopTranslate = (key: string, options?: Record<string, unknown>) => string;

/**
 * What to say when the answer names a budget beyond the run's own (`budgetScope`) as what stopped it.
 * Editions plug in via config.ts; this default says nothing of it, so the run's own spending-limit notice shows.
 */
export function budgetStopLines(
  _scope: BudgetScope,
  _t: BudgetStopTranslate,
  _language: string,
  _now: Date,
): BudgetStopLines | null {
  return null;
}
