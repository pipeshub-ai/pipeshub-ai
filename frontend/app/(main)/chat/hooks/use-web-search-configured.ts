'use client';

import useSWR from 'swr';
import { SWR_NS, SWR_POLICY } from '@/lib/api/swr-cache';
import { WebSearchApi } from '@/app/(main)/workspace/web-search/api';

/**
 * Whether an admin has set web search up (a provider that can search is the default).
 * `null` while unknown: still loading, the read failed, or an older server sends no flag.
 * Callers treat unknown as set up, so a failed read never takes web search away.
 */
export function useWebSearchConfigured(): boolean | null {
  const { data } = useSWR([SWR_NS.webSearchConfig] as const, () => WebSearchApi.getConfig(), SWR_POLICY.list60s);
  return typeof data?.configured === 'boolean' ? data.configured : null;
}
