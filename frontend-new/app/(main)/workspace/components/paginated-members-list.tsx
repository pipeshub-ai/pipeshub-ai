'use client';

import React, { useCallback, useRef } from 'react';
import { Flex, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { usePaginatedList, type PaginatedFetchResult } from '../hooks/use-paginated-list';

const DEFAULT_LIMIT = 25;
const SCROLL_THRESHOLD_PX = 40;

interface PaginatedMembersListProps<T> {
  /** Async fetcher: receives search query, page number, and limit; returns items + totalCount. */
  fetcher: (search: string | undefined, page: number, limit: number) => Promise<PaginatedFetchResult<T>>;
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
  const listRef = useRef<HTMLDivElement>(null);
  const {
    items,
    search,
    isLoading,
    isLoadingMore,
    setSearch,
    loadMore,
    refresh,
  } = usePaginatedList<T>({ fetcher, limit, onFetched });

  React.useImperativeHandle(ref, () => ({ refresh }), [refresh]);

  const handleScroll = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - SCROLL_THRESHOLD_PX) {
      loadMore();
    }
  }, [loadMore]);

  return (
    <Flex direction="column" gap="3">
      <TextField.Root
        placeholder={searchPlaceholder}
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        size="2"
      >
        <TextField.Slot>
          <MaterialIcon name="search" size={14} color="var(--slate-9)" />
        </TextField.Slot>
      </TextField.Root>

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
