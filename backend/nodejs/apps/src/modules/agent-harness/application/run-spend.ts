import {
  BudgetAmount,
  BudgetAvailable,
  BudgetCap,
} from '../domain/budget-ledger';
import { HarnessConflictError } from '../domain/errors';
import { HarnessRun } from '../domain/execution';
import { BudgetLedgerStore } from '../ports/budget-ledger';

/** A run's spend from the ledger (P6.5): its account, which counts its children. */
export interface RunBudgetView {
  cap: BudgetCap;
  committed: BudgetAmount;
  reserved: BudgetAmount;
  available: BudgetAvailable;
  exhausted: boolean;
  /** Calls of the run (its children's too) charged an estimate the provider has not replaced (WP-66). */
  estimated: number;
}

export type RunSpendLedger = Pick<BudgetLedgerStore, 'account' | 'remaining'>;

/**
 * The one reading of what a run has spent (C10.05): the run timeline's `budget`, `GET …/runs/{runId}/cost` and the
 * cost the stream pushes (WP-100) all read it here, so the number a person sees is the ledger's, never a sum of
 * the journal's usage events.
 */
export class RunSpendReader {
  constructor(private readonly ledger: RunSpendLedger | null) {}

  /** No account (a run admitted before P6.5, or the ledger off): nothing to show. */
  async read(
    run: Pick<HarnessRun, 'orgId' | 'runId'>,
  ): Promise<RunBudgetView | null> {
    if (this.ledger === null) return null;
    const account = await this.ledger.account(run.orgId, run.runId);
    if (account === null) return null;
    try {
      const remaining = await this.ledger.remaining(run.orgId, run.runId);
      return {
        cap: account.cap,
        committed: remaining.committed,
        reserved: remaining.reserved,
        available: remaining.available,
        exhausted: remaining.exhausted,
        estimated: remaining.estimated,
      };
    } catch (error) {
      if (
        error instanceof HarnessConflictError &&
        error.code === 'BUDGET_ACCOUNT_MISSING'
      ) {
        return null;
      }
      throw error;
    }
  }
}
