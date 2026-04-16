'use client';

import React, { useEffect, useRef, useCallback, useMemo } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Flex, Box, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { SecondaryPanel, SidebarBackHeader } from '@/app/components/sidebar';
import { ICON_SIZE_DEFAULT, KBD_BADGE_PADDING } from '@/app/components/sidebar';
import { useChatStore } from '@/chat/store';
import { useCommandStore } from '@/lib/store/command-store';
import { getModifierSymbol } from '@/lib/utils/platform';
import { SidebarItem } from './sidebar-item';
import { debugLog } from '@/chat/debug-logger';
import { ChatApi } from '@/chat/api';
import { ChatSectionElement } from './chat-section-element';
import { MAX_VISIBLE_CHATS, MORE_CHATS_PAGE_SIZE } from '../constants';
import { buildChatHref } from '@/chat/build-chat-url';

interface MoreChatsSidebarProps {
  /** Which section ("shared" | "your") the user opened "More" from */
  sectionType: 'shared' | 'your';
  /** Called to navigate back to the main sidebar view */
  onBack: () => void;
}

/**
 * MoreChatsSidebar — secondary panel that shows all chats
 * for a given section with local search filtering and infinite scroll.
 *
 * Renders inside SecondaryPanel (opens to the right of the main sidebar).
 * When a chat is selected, it is moved to the top of the list
 * (ChatGPT-style reordering) and the panel is closed.
 *
 * Wrapped in React.memo to prevent parent-cascade re-renders.
 */
export const MoreChatsSidebar = React.memo(function MoreChatsSidebar({ sectionType, onBack }: MoreChatsSidebarProps) {
  debugLog.tick('[sidebar] [MoreChatsSidebar]');

  const router = useRouter();
  const searchParams = useSearchParams();
  const currentConversationId = searchParams.get('conversationId');

  const conversations = useChatStore((s) => s.conversations);
  const sharedConversations = useChatStore((s) => s.sharedConversations);
  const moveConversationToTop = useChatStore((s) => s.moveConversationToTop);
  const pagination = useChatStore((s) => s.pagination);
  const moreChatsPagination = useChatStore((s) => s.moreChatsPagination);
  const setMoreChatsPagination = useChatStore((s) => s.setMoreChatsPagination);
  const appendConversations = useChatStore((s) => s.appendConversations);
  const appendSharedConversations = useChatStore((s) => s.appendSharedConversations);

  const { t } = useTranslation();
  const dispatch = useCommandStore((s) => s.dispatch);
  const modKey = useMemo(() => getModifierSymbol(), []);

  const sentinelRef = useRef<HTMLDivElement>(null);
  // Stable ref to the latest moreChatsPagination to avoid stale closures in the observer
  const moreChatsPaginationRef = useRef(moreChatsPagination);
  useEffect(() => {
    moreChatsPaginationRef.current = moreChatsPagination;
  }, [moreChatsPagination]);

  // Slice off the first MAX_VISIBLE_CHATS items — those are already shown in the
  // main sidebar, so "More Chats" only displays the overflow (index 10+) to avoid duplicates.
  const allSourceConversations = sectionType === 'shared' ? sharedConversations : conversations;
  const sourceConversations = allSourceConversations.slice(MAX_VISIBLE_CHATS);

  const handleOpenSearch = () => dispatch('openCommandPalette');

  const handleSelect = (id: string) => {
    // Move selected chat to top (ChatGPT-style behavior)
    if (sectionType === 'your') {
      moveConversationToTop(id);
    }
    router.push(buildChatHref({ conversationId: id }));
    onBack();
  };

  // Initialize moreChatsPagination from the main pagination on first open
  useEffect(() => {
    console.log('[MoreChats] mount — pagination from store:', pagination, '| moreChatsPagination:', moreChatsPagination);
    if (moreChatsPagination === null && pagination) {
      const init = { page: pagination.page, hasNextPage: pagination.hasNextPage, isLoadingMore: false };
      console.log('[MoreChats] initializing moreChatsPagination →', init);
      setMoreChatsPagination(init);
    } else if (!pagination) {
      console.warn('[MoreChats] no main pagination available yet — infinite scroll disabled');
    }
  }, []);

  const loadMore = useCallback(async () => {
    const current = moreChatsPaginationRef.current;
    console.log('[MoreChats] loadMore called — current:', current);
    if (!current) { console.log('[MoreChats] skip: moreChatsPagination not initialized'); return; }
    if (current.isLoadingMore) { console.log('[MoreChats] skip: already loading'); return; }
    if (!current.hasNextPage) { console.log('[MoreChats] skip: no next page'); return; }

    const nextPage = current.page + 1;
    console.log(`[MoreChats] fetching page ${nextPage}, limit ${MORE_CHATS_PAGE_SIZE}`);
    setMoreChatsPagination({ ...current, isLoadingMore: true });

    try {
      const result = await ChatApi.fetchConversations(nextPage, MORE_CHATS_PAGE_SIZE);
      console.log(`[MoreChats] page ${nextPage} response — conversations:`, result.conversations.length, '| sharedConversations:', result.sharedConversations.length, '| pagination:', result.pagination);
      if (sectionType === 'your') {
        appendConversations(result.conversations);
      } else {
        appendSharedConversations(result.sharedConversations);
      }
      const next = { page: nextPage, hasNextPage: result.pagination.hasNextPage, isLoadingMore: false };
      console.log('[MoreChats] updating moreChatsPagination →', next);
      setMoreChatsPagination(next);
    } catch (err) {
      console.error('[MoreChats] fetch failed:', err);
      // On error, restore the previous pagination state so the user can retry
      setMoreChatsPagination({ ...current, isLoadingMore: false });
    }
  }, [sectionType, appendConversations, appendSharedConversations, setMoreChatsPagination]);

  // IntersectionObserver: fires loadMore when sentinel becomes visible
  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) {
      console.log('[MoreChats] observer: no sentinel (hasNextPage=false or searching)');
      return;
    }
    console.log('[MoreChats] observer: attaching to sentinel');
    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        console.log('[MoreChats] sentinel intersection:', entry?.isIntersecting, '| ratio:', entry?.intersectionRatio);
        if (entry?.isIntersecting) {
          loadMore();
        }
      },
      { threshold: 0.1 }
    );

    observer.observe(sentinel);
    return () => { console.log('[MoreChats] observer: disconnecting'); observer.disconnect(); };
  }, [loadMore]);

  const title = sectionType === 'shared' ? t('chat.sharedChats') : t('chat.yourChats');
  const isLoadingMore = moreChatsPagination?.isLoadingMore ?? false;
  const hasNextPage = moreChatsPagination?.hasNextPage ?? false;

  return (
    <SecondaryPanel
      header={<SidebarBackHeader title={title} onBack={onBack} backgroundColor='var(--olive-1)'/>}
    >
      <Flex direction="column" gap="4">
        {/* Search Chats — opens command palette */}
        <SidebarItem
          icon={<MaterialIcon name="search" size={ICON_SIZE_DEFAULT} />}
          label={t('nav.searchChats')}
          onClick={handleOpenSearch}
          rightSlot={
            <span
              style={{
                background: 'var(--slate-1)',
                border: '1px solid var(--slate-3)',
                padding: KBD_BADGE_PADDING,
                borderRadius: 'var(--radius-2)',
                fontSize: 12,
                lineHeight: 'var(--line-height-1)',
                letterSpacing: '0.04px',
                color: 'var(--slate-12)',
                fontWeight: 400,
              }}
            >
              {modKey} +K
            </span>
          }
        />

        {/* Flat chat list */}
        <Flex
          direction="column"
          gap="1"
          className="no-scrollbar"
          style={{ flex: 1, overflowY: 'auto' }}
        >
          {sourceConversations.length > 0 ? (
            sourceConversations.map((conv) => (
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
                {t('chat.noChatsYet')}
              </Text>
            </Flex>
          )}

          {/* Sentinel + spinner: only shown when more pages exist */}
          {(hasNextPage || isLoadingMore) && (
            <Box ref={sentinelRef} style={{ padding: '8px 0', textAlign: 'center' }}>
              {isLoadingMore && (
                <Flex align="center" justify="center" gap="2" style={{ padding: '4px 0' }}>
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
