'use client';

import { useEffect, useState } from 'react';
import { AgentsApi } from '@/app/(main)/agents/api';
import type { DurableAgentRunHandle } from '../types';

// A finished run's version never changes, so each run is read once per page load, whichever answer asks first.
const known = new Map<string, Promise<number | null>>();

function versionOf(agentId: string, sessionId: string, runId: string): Promise<number | null> {
  let pending = known.get(runId);
  if (!pending) {
    pending = AgentsApi.getAgentRun(agentId, sessionId, runId)
      .then((run) => (typeof run?.agentVersion === 'number' && run.agentVersion >= 1 ? run.agentVersion : null))
      .catch(() => {
        // A failed read is not remembered: the next answer that asks tries again.
        known.delete(runId);
        return null;
      });
    known.set(runId, pending);
  }
  return pending;
}

/**
 * The published agent version a finished agent run ran (OWD-14), for the answer's activity line; null for the
 * assistant, a run admitted before versions, while the run streams, or when the run cannot be read.
 */
export function useRunAgentVersion(run: DurableAgentRunHandle | null, enabled: boolean): number | null {
  const [version, setVersion] = useState<number | null>(null);
  const agentId = run?.agentId ?? null;
  const sessionId = run?.sessionId ?? null;
  const runId = run?.runId ?? null;

  useEffect(() => {
    setVersion(null);
    if (!enabled || !agentId || !sessionId || !runId) return undefined;
    let live = true;
    void versionOf(agentId, sessionId, runId).then((found) => {
      if (live) setVersion(found);
    });
    return () => {
      live = false;
    };
  }, [enabled, agentId, sessionId, runId]);

  return version;
}

/** Tests only: forget what was read. */
export function resetRunAgentVersions(): void {
  known.clear();
}
