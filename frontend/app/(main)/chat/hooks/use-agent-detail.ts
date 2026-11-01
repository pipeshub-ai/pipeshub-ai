'use client';

import useSWR from 'swr';
import { isSwrReadUnavailable, SWR_POLICY } from '@/lib/api/swr-cache';
import { AgentsApi, agentDetailKey } from '@/app/(main)/agents/api';
import type { GetAgentResult } from '@/app/(main)/agents/types';

export interface AgentDetailState {
  /** The loaded agent (its `agent` is null when the server has none to give). */
  detail: GetAgentResult | undefined;
  /** Nothing to show: it failed before loading, or the agent is gone or forbidden. */
  unavailable: boolean;
  error: unknown;
}

/**
 * One agent's detail, shared by every reader of the same id (one request).
 * Quiet: someone a chat was shared with may not be allowed to open its agent,
 * so each caller decides what to tell the user.
 */
export function useAgentDetail(agentId: string | null): AgentDetailState {
  const id = agentId?.trim() || null;
  const { data, error } = useSWR(
    id ? agentDetailKey(id) : null,
    ([, key]) => AgentsApi.getAgent(key, { quiet: true }),
    SWR_POLICY.list60s,
  );
  const unavailable = isSwrReadUnavailable(Boolean(data), error);
  return { detail: unavailable ? undefined : data, unavailable, error };
}
