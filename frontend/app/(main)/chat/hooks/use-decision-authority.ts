'use client';

import { useEffect, useState } from 'react';
import { ChatApi } from '../api';
import type { DecisionAuthority, DurableAgentRunHandle } from '../types';

/**
 * What the viewer may decide on a durable run's approvals, asked once per run
 * while a card is open. Unknown (still loading, or no run): undefined, and the
 * card shows both buttons; the server's refusal still has the last word.
 * `ruleAsk`: a request an edition's rule decides is open (`approvalAskRules`);
 * an answer read before the run asked one is read again, since only an answer
 * read after it says whether the viewer may approve it.
 */
export function useDecisionAuthority(
  run: DurableAgentRunHandle | null | undefined,
  enabled: boolean,
  ruleAsk = false,
): DecisionAuthority | undefined {
  const [known, setKnown] = useState<{ runId: string; ruleAsk: boolean; authority: DecisionAuthority } | null>(null);
  const runId = run?.runId;
  const stale = known === null || known.runId !== runId || (ruleAsk && !known.ruleAsk);
  useEffect(() => {
    if (!enabled || !run || !stale) return undefined;
    let cancelled = false;
    void ChatApi.getDecisionAuthority(run).then((authority) => {
      if (!cancelled) setKnown({ runId: run.runId, ruleAsk, authority });
    });
    return () => {
      cancelled = true;
    };
    // `run` is a fresh object per render; its id is what identifies it.
  }, [enabled, runId, run?.sessionId, run?.agentId, stale, ruleAsk]);
  return known !== null && known.runId === runId ? known.authority : undefined;
}
