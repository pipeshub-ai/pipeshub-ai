'use client';

import { useEffect, useState } from 'react';
import { ChatApi } from '../api';
import type { ActivityFollowup, DurableAgentRunHandle } from '../types';

/** How long to hold the request for a run this tab just saw finish; the worker stores them after the run ended. */
export const FOLLOWUPS_WAIT_MS = 12_000;

/**
 * A finished run's suggested next questions (UX5), read once per run: for a
 * run this tab followed to its end the server waits for them, otherwise (a
 * reload, an older answer) it answers what it has. A failure shows none.
 */
export function useRunFollowups(
  run: DurableAgentRunHandle | null,
  options: { enabled: boolean; justFinished: boolean },
): ActivityFollowup[] {
  const [read, setRead] = useState<{ runId: string; followups: ActivityFollowup[] } | null>(null);
  const agentId = run?.agentId;
  const sessionId = run?.sessionId;
  const runId = run?.runId;
  const waitMs = options.justFinished ? FOLLOWUPS_WAIT_MS : 0;

  useEffect(() => {
    if (!options.enabled || !sessionId || !runId) return undefined;
    const aborted = new AbortController();
    ChatApi.getRunFollowups({ ...(agentId ? { agentId } : {}), sessionId, runId }, waitMs, aborted.signal).then(
      (followups) => {
        if (!aborted.signal.aborted) setRead({ runId, followups });
      },
      () => undefined,
    );
    return () => aborted.abort();
  }, [options.enabled, agentId, sessionId, runId, waitMs]);

  return read && read.runId === runId ? read.followups : [];
}
