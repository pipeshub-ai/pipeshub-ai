'use client';

import { useState, useCallback, useEffect, useRef } from 'react';

const DEFAULT_LIMIT = 25;
const DEFAULT_SEARCH_DEBOUNCE_MS = 300;

/** Drop duplicate rows when paginated fetches overlap (append races, load-more). */
function dedupeItemsWithId<T>(items: T[]): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const item of items) {
    const id = item ? (item as { id?: string }).id : undefined;
    if (typeof id === 'string' && id.length > 0) {
      if (seen.has(id)) continue;
      seen.add(id);
    }
    out.push(item);
  }
  return out;
}

export interface PaginatedFetchResult<T> {
  items: T[];
  totalCount: number;
}

export interface UsePaginatedListConfig<T> {
  /** Async fetcher: receives search query, page number, and limit. */
  fetcher: (search: string | undefined, page: number, limit: number) => Promise<PaginatedFetchResult<T>>;
  /** Page size (default 25) */
  limit?: number;
  /** Debounce delay for search (ms, default 300) */
  searchDebounceMs?: number;
  /**
   * When false, the hook is idle — no initial fetch, no search reactions.
   * Flip to true to enable (e.g. when a panel opens). Defaults to true.
   */
  enabled?: boolean;
  /** Optional callback invoked after every successful fetch with the accumulated items. */
  onFetched?: (items: T[], totalCount: number, page: number, search: string) => void;
}

export interface UsePaginatedListReturn<T> {
  items: T[];
  totalCount: number;
  search: string;
  isLoading: boolean;
  isLoadingMore: boolean;
  hasMore: boolean;
  /** Update the search string. Debounced — resets to page 1 and refetches. */
  setSearch: (value: string) => void;
  /** Fetch the next page and append. No-op if already loading or no more pages. */
  loadMore: () => void;
  /** Clear everything and refetch page 1 with an empty search. */
  refresh: () => void;
  /** Clear all state without fetching (useful when a panel closes). */
  reset: () => void;
}

/**
 * Stateful logic for paginated + searchable + infinite-scroll lists.
 * UI-agnostic — pair with any renderer.
 */
export function usePaginatedList<T>({
  fetcher,
  limit = DEFAULT_LIMIT,
  searchDebounceMs = DEFAULT_SEARCH_DEBOUNCE_MS,
  enabled = true,
  onFetched,
}: UsePaginatedListConfig<T>): UsePaginatedListReturn<T> {
  const [items, setItems] = useState<T[]>([]);
  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [search, setSearchState] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const fetchGenerationRef = useRef(0);
  const hasMore = page * limit < totalCount;

  // Refs so fetchItems stays stable across renders even when callbacks are inline
  const fetcherRef = useRef(fetcher);
  const onFetchedRef = useRef(onFetched);
  fetcherRef.current = fetcher;
  onFetchedRef.current = onFetched;

  const fetchItems = useCallback(
    async (query: string, pageNum: number, append: boolean) => {
      if (!append) {
        fetchGenerationRef.current += 1;
      }
      const generation = fetchGenerationRef.current;

      if (append) {
        setIsLoadingMore(true);
      } else {
        setIsLoading(true);
      }
      try {
        const result = await fetcherRef.current(query || undefined, pageNum, limit);
        if (generation !== fetchGenerationRef.current) return;

        let updated: T[] = [];
        setItems((prev) => {
          const merged = append ? [...prev, ...result.items] : result.items;
          updated = dedupeItemsWithId(merged);
          return updated;
        });
        setTotalCount(result.totalCount);
        onFetchedRef.current?.(updated, result.totalCount, pageNum, query);
      } catch {
        // handled by global interceptor
      } finally {
        if (generation === fetchGenerationRef.current) {
          setIsLoading(false);
          setIsLoadingMore(false);
        }
      }
    },
    [limit]
  );

  // Initial load when enabled
  useEffect(() => {
    if (!enabled) return;
    fetchItems('', 1, false);
  }, [enabled, fetchItems]);

  const setSearch = useCallback(
    (value: string) => {
      setSearchState(value);
      if (!enabled) return;
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
      searchTimerRef.current = setTimeout(() => {
        setPage(1);
        fetchItems(value, 1, false);
      }, searchDebounceMs);
    },
    [enabled, fetchItems, searchDebounceMs]
  );

  const loadMore = useCallback(() => {
    if (!enabled || !hasMore || isLoadingMore) return;
    const nextPage = page + 1;
    setPage(nextPage);
    fetchItems(search, nextPage, true);
  }, [enabled, hasMore, isLoadingMore, page, search, fetchItems]);

  const refresh = useCallback(() => {
    setSearchState('');
    setPage(1);
    fetchItems('', 1, false);
  }, [fetchItems]);

  const reset = useCallback(() => {
    fetchGenerationRef.current += 1;
    if (searchTimerRef.current) {
      clearTimeout(searchTimerRef.current);
      searchTimerRef.current = null;
    }
    setItems([]);
    setPage(1);
    setTotalCount(0);
    setSearchState('');
    setIsLoading(false);
    setIsLoadingMore(false);
  }, []);

  return {
    items,
    totalCount,
    search,
    isLoading,
    isLoadingMore,
    hasMore,
    setSearch,
    loadMore,
    refresh,
    reset,
  };
}
