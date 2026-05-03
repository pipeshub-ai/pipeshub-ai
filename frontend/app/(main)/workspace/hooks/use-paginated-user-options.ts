'use client';

import { useState, useCallback, useEffect } from 'react';
import { UsersApi } from '../users/api';
import type { CheckboxOption } from '../components';

const DEFAULT_LIMIT = 25;

interface UsePaginatedUserOptionsConfig {
  /** When true, the first page is fetched. Use this to trigger loading when a panel/mode opens. */
  enabled: boolean;
  /** Which field to use as the option id. 'id' = graph UUID (for teams), 'userId' = MongoDB _id (for groups). */
  idField?: 'id' | 'userId';
  /** API source. 'mongodb' (default) uses fetchMergedUsers, 'graph' uses listGraphUsers (returns graph UUIDs). */
  source?: 'mongodb' | 'graph';
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

/**
 * Reusable hook for paginated, searchable user options in SearchableCheckboxDropdown.
 * Fetches users from `UsersApi.fetchMergedUsers` with server-side search and pagination.
 */
export function usePaginatedUserOptions({
  enabled,
  idField = 'userId',
  source = 'mongodb',
  limit = DEFAULT_LIMIT,
}: UsePaginatedUserOptionsConfig): UsePaginatedUserOptionsReturn {
  const [options, setOptions] = useState<CheckboxOption[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [search, setSearch] = useState('');

  const fetchOptions = useCallback(
    async (query: string, pageNum: number, append: boolean) => {
      setIsLoading(true);
      try {
        const fetcher = source === 'graph'
          ? UsersApi.listGraphUsers
          : UsersApi.fetchMergedUsers;
        const { users, totalCount } = await fetcher({
          page: pageNum,
          limit,
          search: query || undefined,
        });
        const newOpts = users.map((u) => ({
          id: idField === 'id' ? u.id : u.userId,
          label: u.name || u.email || 'Unknown User',
          subtitle: u.email,
          profilePicture: u.profilePicture,
        }));
        setOptions((prev) => (append ? [...prev, ...newOpts] : newOpts));
        setHasMore(pageNum * limit < totalCount);
      } catch {
        // handled by global interceptor
      } finally {
        setIsLoading(false);
      }
    },
    [limit, idField, source]
  );

  // Load first page when enabled; reset state when disabled
  useEffect(() => {
    if (enabled) {
      setSearch('');
      setPage(1);
      fetchOptions('', 1, false);
    }
  }, [enabled, fetchOptions]);

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
