'use client';

import { useCallback, useEffect } from 'react';
import { loadJobTimeline, useJobsStore } from './jobs-store';
import type { JobTimeline, SessionRoute } from './jobs-api';

export interface JobTimelineState {
  timeline: JobTimeline | null;
  failed: boolean;
  /** Read it again now (a decision on one of its asks changed it). */
  reload: () => Promise<void>;
}

/**
 * One job's timeline as every place that shows the job reads it (D13, ADR-CJ-01): read when `enabled`, once while it
 * runs and once more after it ended (`ended`), shared with the card's settle read, so opening its steps or its
 * panel after it ended reads nothing new.
 */
export function useJobTimeline(
  session: SessionRoute | null,
  runId: string | undefined,
  options: { enabled: boolean; ended: boolean },
): JobTimelineState {
  const entry = useJobsStore((s) => (runId ? s.timelines[runId] : undefined));
  const failed = useJobsStore((s) => (runId ? s.timelineFailed[runId] === true : false));
  const agentId = session?.agentId;
  const sessionId = session?.sessionId;
  const { enabled, ended } = options;

  useEffect(() => {
    if (!enabled || !sessionId || !runId) return;
    void loadJobTimeline({ ...(agentId ? { agentId } : {}), sessionId }, runId, { ended });
  }, [enabled, ended, agentId, sessionId, runId]);

  const reload = useCallback(async () => {
    if (!enabled || !sessionId || !runId) return;
    await loadJobTimeline({ ...(agentId ? { agentId } : {}), sessionId }, runId, { ended, force: true });
  }, [enabled, ended, agentId, sessionId, runId]);

  return { timeline: entry?.view ?? null, failed: failed && !entry, reload };
}
