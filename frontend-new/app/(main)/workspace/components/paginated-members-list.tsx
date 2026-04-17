'use client';

import React, { useState, useCallback, useRef } from 'react';
import { Flex, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

const DEFAULT_LIMIT = 25;
const SCROLL_THRESHOLD_PX = 40;

interface PaginatedMembersListProps<T> {
  /** Async fetcher: receives search query, page number, and limit; returns items + totalCount. */
  fetcher: (search: string | undefined, page: number, limit: number) => Promise<{ items: T[]; totalCount: number }>;
  /** Render each item. */
  renderItem: (item: T, index: number) => React.ReactNode;
  /** Unique key extractor for each item. */
  keyExtractor: (item: T) => string;
  /** Search placeholder text */
  searchPlaceholder?: string;
  /** Text shown when there are no items and not loading */
  emptyText?: string;
  /** Page size (default 25) */
  limit?: number;
  /** Max height of the scrollable list area (default 300) */
  maxHeight?: number;
  /** Called with the fetched items on each successful fetch (for parent-level caching). */
  onFetched?: (items: T[], totalCount: number, page: number, search: string) => void;
}

interface PaginatedMembersListHandle {
  /** Refresh the list from page 1 with current search cleared. */
  refresh: () => void;
}

/**
 * Reusable paginated + searchable members list with infinite scroll.
 * Handles pagination state, debounced search, and scroll-to-load-more internally.
 *
 * Use `ref` with `PaginatedMembersListHandle` to imperatively refresh after mutations.
 */
export const PaginatedMembersList = React.forwardRef(function PaginatedMembersListInner<T>(
  {
    fetcher,
    renderItem,
    keyExtractor,
    searchPlaceholder = 'Search...',
    emptyText = 'No items found',
    limit = DEFAULT_LIMIT,
    maxHeight = 300,
    onFetched,
  }: PaginatedMembersListProps<T>,
  ref: React.Ref<PaginatedMembersListHandle>
) {
  const [items, setItems] = useState<T[]>([]);
  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [search, setSearch] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hasMore = page * limit < totalCount;

  // Store callbacks in refs so fetchItems stays stable across renders
  const fetcherRef = useRef(fetcher);
  const onFetchedRef = useRef(onFetched);
  fetcherRef.current = fetcher;
  onFetchedRef.current = onFetched;

  const fetchItems = useCallback(
    async (query: string, pageNum: number, append: boolean) => {
      if (append) {
        setIsLoadingMore(true);
      } else {
        setIsLoading(true);
      }
      try {
        const result = await fetcherRef.current(query || undefined, pageNum, limit);
        let updated: T[] = [];
        setItems((prev) => {
          updated = append ? [...prev, ...result.items] : result.items;
          return updated;
        });
        setTotalCount(result.totalCount);
        onFetchedRef.current?.(updated, result.totalCount, pageNum, query);
      } catch {
        // handled by global interceptor
      } finally {
        setIsLoading(false);
        setIsLoadingMore(false);
      }
    },
    [limit]
  );

  // Initial load
  React.useEffect(() => {
    fetchItems('', 1, false);
  }, [fetchItems]);

  // Imperative handle for parent to refresh
  React.useImperativeHandle(ref, () => ({
    refresh: () => {
      setSearch('');
      setPage(1);
      fetchItems('', 1, false);
    },
  }), [fetchItems]);

  const handleSearch = useCallback(
    (value: string) => {
      setSearch(value);
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
      searchTimerRef.current = setTimeout(() => {
        setPage(1);
        fetchItems(value, 1, false);
      }, 300);
    },
    [fetchItems]
  );

  const handleScroll = useCallback(() => {
    if (!hasMore || isLoadingMore) return;
    const el = listRef.current;
    if (!el) return;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - SCROLL_THRESHOLD_PX) {
      const nextPage = page + 1;
      setPage(nextPage);
      fetchItems(search, nextPage, true);
    }
  }, [hasMore, isLoadingMore, page, search, fetchItems]);

  return (
    <Flex direction="column" gap="3">
      {/* Search */}
      <TextField.Root
        placeholder={searchPlaceholder}
        value={search}
        onChange={(e) => handleSearch(e.target.value)}
        size="2"
      >
        <TextField.Slot>
          <MaterialIcon name="search" size={14} color="var(--slate-9)" />
        </TextField.Slot>
      </TextField.Root>

      {/* List */}
      {items.length === 0 && !isLoading ? (
        <Text size="2" style={{ color: 'var(--slate-11)' }}>
          {emptyText}
        </Text>
      ) : (
        <Flex
          ref={listRef}
          direction="column"
          gap="3"
          onScroll={handleScroll}
          style={{ maxHeight, overflowY: 'auto' }}
        >
          {items.map((item, index) => (
            <React.Fragment key={keyExtractor(item)}>
              {renderItem(item, index)}
            </React.Fragment>
          ))}
          {isLoadingMore && (
            <Text size="1" style={{ color: 'var(--slate-9)', textAlign: 'center', padding: 4 }}>
              Loading...
            </Text>
          )}
        </Flex>
      )}
    </Flex>
  );
}) as <T>(
  props: PaginatedMembersListProps<T> & { ref?: React.Ref<PaginatedMembersListHandle> }
) => React.ReactElement | null;

export type { PaginatedMembersListProps, PaginatedMembersListHandle };
