import type { BudgetAccount } from '../domain/budget-ledger';
import type { ChatPathMetrics } from '../ports/chat-path-metrics';

/**
 * C5.05 (SLO #11): an ended run's cache-read share, from the budget account its close returned (one read of the
 * account for both, WP88-FU-4). A run with no account or no prompt tokens records nothing.
 */
export function recordRunCacheRead(
  metrics: Pick<ChatPathMetrics, 'runCacheRead'>,
  closed: Pick<BudgetAccount, 'used'> | null,
): void {
  const used = closed?.used;
  if (used !== undefined && used.inputTokens > 0) metrics.runCacheRead(used);
}
