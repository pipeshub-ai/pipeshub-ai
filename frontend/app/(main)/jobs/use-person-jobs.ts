'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import useSWR from 'swr';
import { SWR_POLICY } from '@/lib/api/swr-cache';
import { PERSON_JOBS_KEY, PERSON_JOBS_PAGE } from '@/lib/harness-jobs/swr-keys';
import type { PersonRuns } from '@/lib/contracts/harness/generated/ext/pipeshub/api/person-runs';
import { fetchMorePersonJobs, fetchPersonJobs } from './api';

type PersonRun = PersonRuns['runs'][number];

export interface PersonJobs {
  runs: PersonRun[];
  loading: boolean;
  /** The first read failed and nothing is shown. */
  failed: boolean;
  hasMore: boolean;
  loadingMore: boolean;
  loadMore: () => void;
  retry: () => void;
}

/**
 * The caller's jobs: the first page from the shared cache key (refreshed by pushes and a job's end, §6), and the
 * pages after it read on Load more. A refresh of the first page replaces those rows; the older pages stay.
 */
export function usePersonJobs(): PersonJobs {
  const { data, error, isLoading, mutate } = useSWR(PERSON_JOBS_KEY, fetchPersonJobs, SWR_POLICY.list30s);
  const [older, setOlder] = useState<PersonRun[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    if (data && older.length === 0) setCursor(data.nextCursor);
  }, [data, older.length]);

  const runs = useMemo(() => {
    const first = data?.runs ?? [];
    const known = new Set(first.map((r) => r.runId));
    return [...first, ...older.filter((r) => !known.has(r.runId))];
  }, [data, older]);

  const loadMore = useCallback(() => {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    fetchMorePersonJobs(cursor, PERSON_JOBS_PAGE)
      .then((page) => {
        setOlder((rows) => [...rows, ...page.runs]);
        setCursor(page.nextCursor);
      })
      .catch(() => {
        // The rows already shown stay; Load more stays offered.
      })
      .finally(() => setLoadingMore(false));
  }, [cursor, loadingMore]);

  return {
    runs,
    loading: isLoading && !data,
    failed: Boolean(error) && !data,
    hasMore: Boolean(cursor),
    loadingMore,
    loadMore,
    retry: () => void mutate(),
  };
}
