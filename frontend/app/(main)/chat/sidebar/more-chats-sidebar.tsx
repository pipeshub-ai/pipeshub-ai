'use client';

import React, { useEffect, useRef, useCallback, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Flex, Box, Text, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { SecondaryPanel, SidebarBackHeader } from '@/app/components/sidebar';
import { useChatStore } from '@/chat/store';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { debugLog } from '@/chat/debug-logger';
import { ChatApi } from '@/chat/api';
import { ChatSectionElement } from './chat-section-element';
import { MORE_CHATS_PAGE_SIZE } from '../constants';
import { buildChatHref } from '@/chat/build-chat-url';
import { useDebouncedSearch } from '@/knowledge-base/hooks/use-debounced-search';
import type { Conversation } from '@/chat/types';

interface MoreChatsSidebarProps {
  /** Which section ("shared" | "your") the user opened "More" from */
  sectionType: 'shared' | 'your';
  /** Called to navigate back to the main sidebar view */
  onBack: () => void;
}

/**
 * MoreChatsSidebar — secondary panel that shows ALL chats
 * for a given section with inline search and infinite scroll.
 *
 * Fetches conversations independently from page 1 (page size 20)
 * so the panel shows the complete list including chats already
 * visible in the main sidebar.
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
  const isMobile = useIsMobile();

  const { t } = useTranslation();

  const [searchInput, setSearchInput] = useState('');
  const debouncedSearch = useDebouncedSearch(searchInput, 300);

  // Independent local state — panel fetches its own data from page 1
  const [rows, setRows] = useState<Conversation[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [pagination, setPagination] = useState<{
    page: number;
    hasNextPage: boolean;
  } | null>(null);

  const sentinelRef = useRef<HTMLDivElement>(null);
  const paginationRef = useRef(pagination);
  const debouncedSearchRef = useRef(debouncedSearch);
  useEffect(() => {
    paginationRef.current = pagination;
  }, [pagination]);
  useEffect(() => {
    debouncedSearchRef.current = debouncedSearch;
  }, [debouncedSearch]);

  const searching = debouncedSearch.trim().length > 0;

  // Fetch page 1 on mount AND when search query changes
  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);

    (async () => {
      try {
        const result = await ChatApi.fetchConversations(1, MORE_CHATS_PAGE_SIZE, {
          source: sectionType === 'your' ? 'owned' : 'shared',
          search: debouncedSearch.trim() || undefined,
        });
        if (cancelled) return;
        setRows(result.conversations);
        setPagination({
          page: result.pagination.page,
          hasNextPage: result.pagination.hasNextPage,
        });
      } catch {
        if (!cancelled) {
          setRows([]);
          setPagination(null);
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [debouncedSearch, sectionType]);

  const handleSelect = (id: string) => {
    if (sectionType === 'your') {
      moveConversationToTop(id);
    }
    router.push(buildChatHref({ conversationId: id }));
    onBack();
    if (isMobile) closeMobileSidebar();
  };

  const loadMore = useCallback(async () => {
    const pag = paginationRef.current;
    const search = debouncedSearchRef.current.trim() || undefined;
    if (!pag?.hasNextPage || isLoadingMore || isLoading) return;

    const nextPage = pag.page + 1;
    setIsLoadingMore(true);
    try {
      const result = await ChatApi.fetchConversations(nextPage, MORE_CHATS_PAGE_SIZE, {
        source: sectionType === 'your' ? 'owned' : 'shared',
        search,
      });
      setRows((prev) => {
        const seen = new Set(prev.map((c) => c.id));
        const merged = [...prev];
        for (const row of result.conversations) {
          if (!seen.has(row.id)) {
            seen.add(row.id);
            merged.push(row);
          }
        }
        return merged;
      });
      setPagination({
        page: nextPage,
        hasNextPage: result.pagination.hasNextPage,
      });
    } catch {
      // keep existing list; user can scroll to retry
    } finally {
      setIsLoadingMore(false);
    }
  }, [sectionType, isLoadingMore, isLoading]);

  // IntersectionObserver: fires loadMore when sentinel becomes visible
  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          loadMore();
        }
      },
      { threshold: 0.1 }
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [loadMore]);

  const title = sectionType === 'shared' ? t('chat.sharedChats') : t('chat.yourChats');
  const hasNextPage = pagination?.hasNextPage ?? false;
  const showSentinel = hasNextPage || isLoadingMore;

  return (
    <SecondaryPanel
      header={<SidebarBackHeader title={title} onBack={onBack} backgroundColor='var(--olive-1)'/>}
    >
      <Flex direction="column" gap="3">
        {/* Inline search */}
        <TextField.Root
          size="2"
          placeholder={t('form.searchPlaceholder')}
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          style={{ width: '100%' }}
        >
          <TextField.Slot side="left">
            <MaterialIcon name="search" size={18} color="var(--slate-11)" />
          </TextField.Slot>
        </TextField.Root>

        {/* Chat list */}
        <Flex
          direction="column"
          gap="1"
          className="no-scrollbar"
          style={{ flex: 1, overflowY: 'auto' }}
        >
          {isLoading ? (
            <Flex align="center" justify="center" style={{ padding: 'var(--space-5) var(--space-3)' }}>
              <LottieLoader variant="loader" size={28} showLabel />
            </Flex>
          ) : rows.length > 0 ? (
            rows.map((conv) => (
              <ChatSectionElement
                key={conv.id}
                conversation={conv}
                isActive={currentConversationId === conv.id}
                onClick={() => handleSelect(conv.id)}
              />
            ))
          ) : (
            <Flex
              align="center"
              justify="center"
              style={{ padding: 'var(--space-4) var(--space-3)' }}
            >
              <Text size="2" style={{ color: 'var(--slate-11)' }}>
                {searching ? t('message.noResults') : t('chat.noChatsYet')}
              </Text>
            </Flex>
          )}

          {/* Sentinel + spinner for infinite scroll */}
          {showSentinel && (
            <Box ref={sentinelRef} style={{ padding: 'var(--space-2) 0', textAlign: 'center' }}>
              {isLoadingMore && (
                <Flex align="center" justify="center" gap="2" style={{ padding: 'var(--space-1) 0' }}>
                  <LottieLoader variant="loader" size={20} showLabel />
                </Flex>
              )}
            </Box>
          )}
        </Flex>
      </Flex>
    </SecondaryPanel>
  );
});
