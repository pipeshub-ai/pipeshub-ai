'use client';

import { useEffect, useState, useSyncExternalStore } from 'react';
import { ChatApi } from '../../api';
import { runStillActive, usd } from '../../durable-run-ui';
import { pushedRunCost, subscribeRunCosts } from '../../live-run-cost';
import type { DurableAgentRunHandle } from '../../types';

/** How often the live cost is read while the run works, when its stream does not push it (an older Node). */
export const LIVE_COST_POLL_MS = 10_000;

const shown = (micros: number | null | undefined): string | null =>
  typeof micros === 'number' && micros > 0 ? usd(micros) : null;

/**
 * C10.05: what a running durable run has spent so far, as the timeline header shows it beside the elapsed time
 * ("$0.42 so far"). WP-100: the run's stream pushes it from the ledger (`run_cost`); a stream that does not is
 * read every `LIVE_COST_POLL_MS` while `active` and the page is visible, and stops once a push arrives. Null before
 * anything is spent, for a run with no ledger account, and once a read fails.
 */
export function useLiveRunCost(run: DurableAgentRunHandle | null, active: boolean): string | null {
  const [spent, setSpent] = useState<string | null>(null);
  const runId = run?.runId;
  const pushedMicros = useSyncExternalStore(
    subscribeRunCosts,
    () => pushedRunCost(runId),
    () => undefined,
  );
  useEffect(() => {
    if (!active || run === null) return undefined;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const read = async (): Promise<void> => {
      // The stream pushes this run's cost: nothing to ask.
      if (pushedRunCost(run.runId) !== undefined) return;
      let again = true;
      if (typeof document === 'undefined' || document.visibilityState !== 'hidden') {
        try {
          const view = await ChatApi.getRunCost(run);
          if (!alive) return;
          setSpent(shown(view.budget?.committed.usdMicros ?? 0));
          again = runStillActive(view.status);
        } catch {
          again = false;
        }
      }
      if (alive && again) timer = setTimeout(() => void read(), LIVE_COST_POLL_MS);
    };
    void read();
    return () => {
      alive = false;
      if (timer !== undefined) clearTimeout(timer);
    };
    // The handle is rebuilt on each render; the run it names is what matters.
  }, [runId, active]);
  if (!active) return null;
  return pushedMicros !== undefined ? shown(pushedMicros) : spent;
}
