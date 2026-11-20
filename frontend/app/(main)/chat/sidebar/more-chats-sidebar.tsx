'use client';

import React, { useEffect, useRef, useCallback, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Flex, Box, Text, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ListSkeleton } from '@/app/components/ui/list-skeleton';
import { useDeferredLoading } from '@/app/components/ui/use-deferred-loading';
import { SecondaryPanel, SidebarBackHeader } from '@/app/components/sidebar';
import { useChatStore } from '@/chat/store';
import type { MoreChatsSection } from '@/chat/store';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useIsBelowDesktop } from '@/lib/hooks/use-is-mobile';
import { useFeatureFlagsStore, selectProjectsEnabled } from '@/lib/store/feature-flags-store';
import { debugLog } from '@/chat/debug-logger';
import { ChatApi } from '@/chat/api';
import { ChatSectionElement } from './chat-section-element';
import type { Conversation, ConversationSource } from '@/chat/types';
import { splitPinned } from '@/chat/utils/pinned-conversations';
import { startListRead, withHeldTitles } from '@/chat/utils/held-titles';
import { chatSlots } from '@/config';
import { MORE_CHATS_PAGE_SIZE, SIDEBAR_LISTS_STALE_MS } from '../constants';
import { buildChatHref } from '@/chat/build-chat-url';
import { useDebouncedSearch } from '@/knowledge-base/hooks/use-debounced-search';

interface MoreChatsSidebarProps {
  /** Which rail section the user opened "More" from */
  sectionType: MoreChatsSection;
  /** Called to navigate back to the main sidebar view */
  onBack: () => void;
}

/** The chat row's height, so a skeleton fills the list with as many rows as fit. */
const ROW_HEIGHT = 36;
/** Skeleton rows shown when the list's height is not known yet. */
const MIN_SKELETON_ROWS = 3;

/** The rail's lists as the other panels read them: not at all. */
const NO_ROWS: Conversation[] = [];

/** Skeleton rows that fill a list of this height. */
const skeletonRowsFor = (listHeight: number) => Math.max(MIN_SKELETON_ROWS, Math.ceil(listHeight / ROW_HEIGHT));

/** Every pinned chat of one list. The server lists pins first, so it reads pages until one holds another chat. */
async function fetchPinned(source: ConversationSource, search: string | undefined, excludeProjectChats: boolean) {
  const pinned: Conversation[] = [];
  for (let page = 1; ; page += 1) {
    const { conversations, pagination } = await ChatApi.fetchConversations(page, MORE_CHATS_PAGE_SIZE, {
      source,
      search,
      excludeProjectChats,
    });
    const pins = conversations.filter((c) => c.isPinned === true);
    pinned.push(...pins);
    if (pins.length < conversations.length || !pagination.hasNextPage) return pinned;
  }
}

/**
 * The list this panel last started and what it asked for. The store starts a new list (a new key) for every open,
 * section and sign-out, so a panel shown again (the drawer reopened) on the same key and query still has its rows.
 */
const startedList = { key: -1, query: '' };

/**
 * MoreChatsSidebar: a secondary panel that shows ALL chats for a given section, with a search that stays
 * at the top and a list that loads the next page as you scroll.
 *
 * Fetches conversations from page 1 (page size 20), so the panel shows the complete list including chats
 * already visible in the main sidebar. The rows live in the chat store, so a change made from any row
 * (here or in the rail) shows in both at once. Pinned lists every pinned chat of both lists at once; Shared chats
 * and Your chats list only their unpinned ones, so a pin or an unpin moves a row out of the panel it is in.
 *
 * Wrapped in React.memo to prevent parent-cascade re-renders.
 */
export const MoreChatsSidebar = React.memo(function MoreChatsSidebar({ sectionType, onBack }: MoreChatsSidebarProps) {
  debugLog.tick('[sidebar] [MoreChatsSidebar]');

  const router = useRouter();
  const searchParams = useSearchParams();
  const currentConversationId = searchParams.get('conversationId');

  const moveConversationToTop = useChatStore((s) => s.moveConversationToTop);
  const closeMobileSidebar = useMobileSidebarStore((s) => s.close);
  // The drawer layout: opening a chat closes the drawer.
  const isMobile = useIsBelowDesktop();

  const { t } = useTranslation();

  // Kept in the store, so the panel shown again (the drawer reopened) keeps what was typed.
  const searchInput = useChatStore((s) => s.moreChatsSearch);
  const setSearchInput = useChatStore((s) => s.setMoreChatsSearch);
  const debouncedSearch = useDebouncedSearch(searchInput, 300);
  const projectsEnabled = useFeatureFlagsStore(selectProjectsEnabled);

  const moreChats = useChatStore((s) => s.moreChats);
  const startMoreChatsList = useChatStore((s) => s.startMoreChatsList);
  const setMoreChatsPage = useChatStore((s) => s.setMoreChatsPage);
  const setMoreChatsLoadingMore = useChatStore((s) => s.setMoreChatsLoadingMore);
  const endMoreChatsLoad = useChatStore((s) => s.endMoreChatsLoad);
  const { rows, isLoading, isLoadingMore, hasNextPage, page } = moreChats;

  const searching = debouncedSearch.trim().length > 0;
  const isPinnedList = sectionType === 'pinned';
  const railConversations = useChatStore((s) => (isPinnedList ? s.conversations : NO_ROWS));
  const railSharedConversations = useChatStore((s) => (isPinnedList ? s.sharedConversations : NO_ROWS));
  const listedRows = useMemo(() => {
    if (!isPinnedList) return rows.filter((c) => c.isPinned !== true);
    // A chat pinned in the rail while the list is open joins it; a search shows only what the server matched.
    const listed = new Set(rows.map((c) => c.id));
    const pinnedInRail = searching
      ? []
      : [...railConversations, ...railSharedConversations].filter((c) => c.isPinned === true && !listed.has(c.id));
    return splitPinned([...rows, ...pinnedInRail]).pinned;
  }, [isPinnedList, rows, searching, railConversations, railSharedConversations]);
  const visibleRows = chatSlots.useVisibleConversations(listedRows);
  // Nothing shows for a load faster than the show delay; past it, the skeleton fills the list.
  const listSkeleton = useDeferredLoading(isLoading);
  const moreSkeleton = useDeferredLoading(isLoadingMore);

  const listRef = useRef<HTMLDivElement>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);
  // After a failed page, wait for the next scroll before asking again, so a failing server is not polled.
  const pausedAfterFailure = useRef(false);
  const [listHeight, setListHeight] = useState(0);
  const skeletonRows = skeletonRowsFor(listHeight);

  useEffect(() => {
    const list = listRef.current;
    if (!list || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => setListHeight(list.clientHeight));
    observer.observe(list);
    setListHeight(list.clientHeight);
    return () => observer.disconnect();
  }, []);

  const source = sectionType === 'your' ? 'owned' : 'shared';
  const excludeProjectChats = sectionType === 'your' && projectsEnabled;

  // Fetch page 1 on mount AND when search query changes
  useEffect(() => {
    // Projects decides what Your chats and Pinned leave out, so it is part of what the list asked for.
    const listQuery = [sectionType, projectsEnabled, debouncedSearch.trim()].join('|');
    const listed = useChatStore.getState().moreChats;
    // Shown again with this query's rows still listed (or its first page still loading): they stay, and page 1 loads
    // again only once they are stale, and only while it is the one page loaded: reading page 1 again under later
    // pages could leave a page missing between them.
    const sameList = listed.key === startedList.key && startedList.query === listQuery;
    const fresh = Date.now() - listed.loadedAt < SIDEBAR_LISTS_STALE_MS;
    if (sameList && (listed.isLoading || (listed.page > 0 && (fresh || listed.page > 1)))) return;
    const reuse = sameList && listed.page > 0;
    const key = reuse ? listed.key : startMoreChatsList();
    Object.assign(startedList, { key, query: listQuery });
    pausedAfterFailure.current = false;
    const search = debouncedSearch.trim() || undefined;
    const read = startListRead();

    (async () => {
      try {
        if (isPinnedList) {
          // Pinned chats are few, so both lists' pins load at once and the list has no next page.
          const [own, shared] = await Promise.all([
            fetchPinned('owned', search, projectsEnabled),
            fetchPinned('shared', search, false),
          ]);
          setMoreChatsPage(key, withHeldTitles(splitPinned([...own, ...shared]).pinned, read), 1, false);
          return;
        }
        const result = await ChatApi.fetchConversations(1, MORE_CHATS_PAGE_SIZE, {
          source,
          search,
          excludeProjectChats,
        });
        setMoreChatsPage(key, withHeldTitles(result.conversations, read), result.pagination.page, result.pagination.hasNextPage);
      } catch {
        // A refresh that fails keeps the rows on screen.
        if (reuse) useChatStore.getState().endMoreChatsLoad(key);
        else setMoreChatsPage(key, [], 1, false);
      }
    })();
  }, [debouncedSearch, isPinnedList, projectsEnabled, source, excludeProjectChats, startMoreChatsList, setMoreChatsPage]);

  const handleSelect = (id: string) => {
    if (sectionType === 'your') {
      moveConversationToTop(id);
    }
    router.push(buildChatHref({ conversationId: id }));
    onBack();
    if (isMobile) closeMobileSidebar();
  };

  const loadMore = useCallback(async () => {
    const list = useChatStore.getState().moreChats;
    if (!list.hasNextPage || list.isLoading || list.isLoadingMore || pausedAfterFailure.current) return;

    const { key } = list;
    const nextPage = list.page + 1;
    setMoreChatsLoadingMore(key, true);
    const read = startListRead();
    try {
      const result = await ChatApi.fetchConversations(nextPage, MORE_CHATS_PAGE_SIZE, {
        source,
        search: debouncedSearch.trim() || undefined,
        excludeProjectChats,
        // A later page that fails says nothing: the rows already shown stay, and scrolling asks again.
        suppressErrorToast: true,
      });
      setMoreChatsPage(key, withHeldTitles(result.conversations, read), nextPage, result.pagination.hasNextPage);
    } catch {
      // Keep the rows; scrolling again asks again.
      pausedAfterFailure.current = true;
      endMoreChatsLoad(key);
    }
  }, [source, debouncedSearch, excludeProjectChats, setMoreChatsLoadingMore, setMoreChatsPage, endMoreChatsLoad]);

  const renderRow = (conv: Conversation) => (
    <ChatSectionElement
      key={conv.id}
      conversation={conv}
      isActive={currentConversationId === conv.id}
      onClick={() => handleSelect(conv.id)}
    />
  );

  const title = isPinnedList ? t('chat.pinned') : sectionType === 'shared' ? t('chat.sharedChats') : t('chat.yourChats');
  const showFirstSkeleton = listSkeleton.visible;
  // The next page's skeleton sits under the last row, and goes the moment that page's rows arrive.
  const showMoreSkeleton = isLoadingMore && moreSkeleton.visible && !showFirstSkeleton;
  // Only real rows put the sentinel in range: under the first skeleton it would ask for page after page.
  const showSentinel = hasNextPage && !isLoading && !showFirstSkeleton;

  // Ask for the next page a list height before the end, so the rows are there before you reach them.
  // The observer is made again after each page, so a sentinel that is still in range asks for the next one.
  useEffect(() => {
    const sentinel = sentinelRef.current;
    const list = listRef.current;
    if (!sentinel || !list || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) void loadMore();
      },
      { root: list, rootMargin: '0px 0px 100% 0px' },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [loadMore, page, showSentinel, isLoadingMore]);

  // After a failed page, the next scroll asks again. A wheel or swipe counts too: a short list that does not fill
  // the panel cannot scroll, and would otherwise never ask again. The observer has already seen the sentinel in
  // range, so it will not fire again by itself.
  const retryAfterFailure = () => {
    if (!pausedAfterFailure.current) return;
    pausedAfterFailure.current = false;
    const list = listRef.current;
    const sentinel = sentinelRef.current;
    // In range is what the observer uses: no more than one list height below the list's bottom edge.
    if (list && sentinel && sentinel.getBoundingClientRect().top - list.getBoundingClientRect().bottom <= list.clientHeight) {
      void loadMore();
    }
  };

  return (
    <SecondaryPanel
      header={<SidebarBackHeader title={title} onBack={onBack} backgroundColor="var(--surface-raised)" />}
    >
      {/* A full-height column: the search stays put and only the list below it scrolls */}
      <Flex direction="column" gap="3" style={{ height: '100%', minHeight: 0 }}>
        <TextField.Root
          size="2"
          placeholder={t('nav.searchChatsPlaceholder')}
          aria-label={t('nav.searchChats')}
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          style={{ width: '100%', flexShrink: 0 }}
        >
          <TextField.Slot side="left">
            <MaterialIcon name="search" size={18} color="var(--text-secondary)" />
          </TextField.Slot>
        </TextField.Root>

        {/* Chat list: rows sit flush, one 36px row after another, like the skeleton */}
        <Flex
          ref={listRef}
          direction="column"
          className="no-scrollbar"
          aria-busy={isLoading || isLoadingMore || undefined}
          onScroll={retryAfterFailure}
          onWheel={retryAfterFailure}
          onTouchMove={retryAfterFailure}
          style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}
        >
          {showFirstSkeleton ? (
            <ListSkeleton
              variant="sidebar-row"
              count={skeletonRows}
              statusLabel={t('nav.loadingChats')}
              exiting={listSkeleton.exiting}
            />
          ) : isLoading ? null : visibleRows.length > 0 ? (
            visibleRows.map(renderRow)
          ) : hasNextPage ? null : (
            <Flex
              align="center"
              justify="center"
              style={{ padding: 'var(--space-4) var(--space-3)' }}
            >
              <Text size="2" style={{ color: 'var(--slate-11)' }}>
                {searching ? t('message.noResults') : isPinnedList ? t('chat.pinnedChatsEmpty') : t('chat.noChatsYet')}
              </Text>
            </Flex>
          )}

          {showMoreSkeleton && (
            <ListSkeleton variant="sidebar-row" count={skeletonRows} statusLabel={t('nav.loadingChats')} />
          )}

          {/* Sentinel for infinite scroll */}
          {showSentinel && <Box ref={sentinelRef} aria-hidden style={{ height: 1, flexShrink: 0 }} />}
        </Flex>
      </Flex>
    </SecondaryPanel>
  );
});
