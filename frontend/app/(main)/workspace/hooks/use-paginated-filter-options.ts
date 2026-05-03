'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import type { FilterOption } from '@/app/components/ui/filter-dropdown';

const DEFAULT_LIMIT = 25;

interface PaginatedPage<T> {
  items: T[];
  totalCount: number;
}

interface UsePaginatedFilterOptionsConfig<T> {
  /** Async fetcher: receives search query, page number, and page size; returns a page of items. */
  fetcher: (search: string | undefined, page: number, limit: number) => Promise<PaginatedPage<T>>;
  /** Maps each raw item to a FilterOption ({value, label, icon?}). */
  mapOption: (item: T) => FilterOption;
  /** Optional side-effect called with raw items on each fetch (e.g. caching). */
  onFetched?: (items: T[], page: number, search: string) => void;
  /** Page size (default 25) */
  limit?: number;
}

interface UsePaginatedFilterOptionsReturn {
  options: FilterOption[];
  isLoading: boolean;
  hasMore: boolean;
  onSearch: (query: string) => void;
  onLoadMore: () => void;
}

/**
 * Generic paginated + searchable options hook for FilterDropdown.
 * Replaces hand-rolled pagination state (page, search, hasMore, loading, fetch, handlers).
 */
export function usePaginatedFilterOptions<T>({
  fetcher,
  mapOption,
  onFetched,
  limit = DEFAULT_LIMIT,
}: UsePaginatedFilterOptionsConfig<T>): UsePaginatedFilterOptionsReturn {
  const [options, setOptions] = useState<FilterOption[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [search, setSearch] = useState('');

  // Store callbacks in refs so fetchOptions stays stable across renders
  const fetcherRef = useRef(fetcher);
  const mapOptionRef = useRef(mapOption);
  const onFetchedRef = useRef(onFetched);
  fetcherRef.current = fetcher;
  mapOptionRef.current = mapOption;
  onFetchedRef.current = onFetched;

  const fetchOptions = useCallback(
    async (query: string, pageNum: number, append: boolean) => {
      setIsLoading(true);
      try {
        const { items, totalCount } = await fetcherRef.current(query || undefined, pageNum, limit);
        onFetchedRef.current?.(items, pageNum, query);
        const newOpts = items.map(mapOptionRef.current);
        setOptions((prev) => (append ? [...prev, ...newOpts] : newOpts));
        setHasMore(pageNum * limit < totalCount);
      } catch {
        /* filter dropdown degrades gracefully */
      } finally {
        setIsLoading(false);
      }
    },
    [limit]
  );

  // Initial load — runs once (fetchOptions is stable)
  useEffect(() => {
    fetchOptions('', 1, false);
  }, [fetchOptions]);

  const onSearch = useCallback(
    (query: string) => {
      setSearch(query);
      setPage(1);
      fetchOptions(query, 1, false);
    },
    [fetchOptions]
  );

  const onLoadMore = useCallback(() => {
    const nextPage = page + 1;
    setPage(nextPage);
    fetchOptions(search, nextPage, true);
  }, [page, search, fetchOptions]);

  return { options, isLoading, hasMore, onSearch, onLoadMore };
}
