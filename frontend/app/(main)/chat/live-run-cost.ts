/**
 * C10.05 (WP-100): the spend Node pushes on a run's stream (`run_cost`), by run, for the timeline header's
 * "$0.42 so far". A run the stream pushes for is never polled; one it does not (an older Node) still is.
 */

/** Runs kept: a tab follows a few at a time; the oldest are forgotten first. */
const MAX_RUNS = 200;

/** Committed spend in micro-dollars by run; null: the run has no ledger account. */
const pushed = new Map<string, number | null>();
const listeners = new Set<() => void>();

/** A `run_cost` frame's value; anything of the wrong shape changes nothing. */
export function pushRunCost(value: unknown): void {
  if (value === null || typeof value !== 'object') return;
  const { runId, budget } = value as { runId?: unknown; budget?: unknown };
  if (typeof runId !== 'string' || runId === '') return;
  const committed = (budget as { committed?: { usdMicros?: unknown } } | null | undefined)?.committed?.usdMicros;
  pushed.delete(runId);
  pushed.set(runId, typeof committed === 'number' && Number.isFinite(committed) && committed >= 0 ? committed : null);
  if (pushed.size > MAX_RUNS) {
    const oldest = pushed.keys().next().value;
    if (oldest !== undefined) pushed.delete(oldest);
  }
  for (const listener of listeners) listener();
}

/** What the stream pushed for the run: micro-dollars, null for no account, undefined when it pushed nothing. */
export function pushedRunCost(runId: string | undefined): number | null | undefined {
  return runId === undefined ? undefined : pushed.get(runId);
}

export function subscribeRunCosts(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** For tests: forget every pushed cost. */
export function resetRunCosts(): void {
  pushed.clear();
}
