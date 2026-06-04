'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import { UsersApi } from '../users/api';
import type { CheckboxOption } from '../components';

const DEFAULT_LIMIT = 25;

interface UsePaginatedUserOptionsConfig {
  /** When true, the first page is fetched. Use this to trigger loading when a panel/mode opens. */
  enabled: boolean;
  /** Which field to use as the option id (default MongoDB userId). */
  idField?: 'id' | 'userId';
  /** Page size (default 25) */
  limit?: number;
}

interface UsePaginatedUserOptionsReturn {
  /** Current page of options (accumulates on load-more) */
  options: CheckboxOption[];
  /** Whether more options are being loaded */
  isLoading: boolean;
  /** Whether there are more pages to load */
  hasMore: boolean;
  /** Call when the user types in the search box (debounced by the dropdown component) */
  onSearch: (query: string) => void;
  /** Call when the user scrolls to the bottom of the list */
  onLoadMore: () => void;
}

/** Merge options by id so infinite scroll never produces duplicate React keys. */
function mergeOptionsById(
  prev: CheckboxOption[],
  incoming: CheckboxOption[],
  append: boolean,
): CheckboxOption[] {
  const seen = new Set<string>();
  const merged: CheckboxOption[] = [];

  for (const opt of append ? prev : []) {
    if (!opt.id || seen.has(opt.id)) continue;
    seen.add(opt.id);
    merged.push(opt);
  }
  for (const opt of incoming) {
    if (!opt.id || seen.has(opt.id)) continue;
    seen.add(opt.id);
    merged.push(opt);
  }
  return merged;
}

/**
 * Reusable hook for paginated, searchable user options in SearchableCheckboxDropdown.
 * Fetches users from `UsersApi.fetchMergedUsers` with server-side search and pagination.
 */
export function usePaginatedUserOptions({
  enabled,
  idField = 'userId',
  limit = DEFAULT_LIMIT,
}: UsePaginatedUserOptionsConfig): UsePaginatedUserOptionsReturn {
  const [options, setOptions] = useState<CheckboxOption[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  const pageRef = useRef(1);
  const searchRef = useRef('');
  const fetchInFlightRef = useRef(false);
  const requestIdRef = useRef(0);

  const fetchOptions = useCallback(
    async (query: string, pageNum: number, append: boolean) => {
      const requestId = ++requestIdRef.current;
      fetchInFlightRef.current = true;
      setIsLoading(true);
      try {
        const { users, totalCount } = await UsersApi.fetchMergedUsers({
          page: pageNum,
          limit,
          search: query || undefined,
        });
        if (requestId !== requestIdRef.current) return;

        const newOpts = users.map((u) => ({
          id: idField === 'id' ? u.id : u.userId,
          label: u.name || u.email || 'Unknown User',
          subtitle: u.email,
          profilePicture: u.profilePicture,
        }));
        setOptions((prev) => mergeOptionsById(prev, newOpts, append));
        pageRef.current = pageNum;
        setHasMore(pageNum * limit < totalCount);
      } catch {
        // handled by global interceptor
      } finally {
        if (requestId === requestIdRef.current) {
          fetchInFlightRef.current = false;
          setIsLoading(false);
        }
      }
    },
    [limit, idField],
  );

  // Load first page when enabled; reset state when panel opens
  useEffect(() => {
    if (enabled) {
      searchRef.current = '';
      pageRef.current = 1;
      setHasMore(false);
      fetchOptions('', 1, false);
    }
  }, [enabled, fetchOptions]);

  const onSearch = useCallback(
    (query: string) => {
      searchRef.current = query;
      pageRef.current = 1;
      fetchOptions(query, 1, false);
    },
    [fetchOptions],
  );

  const onLoadMore = useCallback(() => {
    if (fetchInFlightRef.current || !hasMore) return;
    const nextPage = pageRef.current + 1;
    fetchOptions(searchRef.current, nextPage, true);
  }, [hasMore, fetchOptions]);

  return { options, isLoading, hasMore, onSearch, onLoadMore };
}
