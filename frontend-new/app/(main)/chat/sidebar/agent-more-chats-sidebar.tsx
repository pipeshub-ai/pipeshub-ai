'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Flex, Box, Text, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { SecondaryPanel, SidebarBackHeader } from '@/app/components/sidebar';
import { useChatStore } from '@/chat/store';
import { debugLog } from '@/chat/debug-logger';
import { AgentsApi } from '@/app/(main)/agents/api';
import { ChatSectionElement } from './chat-section-element';
import { AGENT_CONVERSATIONS_PAGE_SIZE } from '../constants';
import { buildChatHref } from '@/chat/build-chat-url';
import { useDebouncedSearch } from '@/knowledge-base/hooks/use-debounced-search';
import type { Conversation } from '@/chat/types';

interface AgentMoreChatsSidebarProps {
  agentId: string;
  onBack: () => void;
}

/**
 * Secondary panel for agent chat sidebar — shows ALL agent conversations
 * with inline search and infinite scroll.
 *
 * Fetches independently from page 1 (page size 20) so the panel shows
 * the complete list including chats already visible in the main sidebar.
 */
export const AgentMoreChatsSidebar = React.memo(function AgentMoreChatsSidebar({
  agentId,
  onBack,
}: AgentMoreChatsSidebarProps) {
  debugLog.tick('[sidebar] [AgentMoreChatsSidebar]');

  const router = useRouter();
  const searchParams = useSearchParams();
  const currentConversationId = searchParams.get('conversationId');

  const moveConversationToTop = useChatStore((s) => s.moveConversationToTop);

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
        const result = await AgentsApi.fetchAgentConversations(agentId, {
          page: 1,
          limit: AGENT_CONVERSATIONS_PAGE_SIZE,
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
  }, [agentId, debouncedSearch]);

  const handleSelect = (id: string) => {
    moveConversationToTop(id);
    router.push(buildChatHref({ agentId, conversationId: id }));
    onBack();
  };

  const loadMore = useCallback(async () => {
    const pag = paginationRef.current;
    const search = debouncedSearchRef.current.trim() || undefined;
    if (!pag?.hasNextPage || isLoadingMore || isLoading) return;

    const nextPage = pag.page + 1;
    setIsLoadingMore(true);
    try {
      const result = await AgentsApi.fetchAgentConversations(agentId, {
        page: nextPage,
        limit: AGENT_CONVERSATIONS_PAGE_SIZE,
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
  }, [agentId, isLoadingMore, isLoading]);

  // IntersectionObserver: fires loadMore when sentinel becomes visible
  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) loadMore();
      },
      { threshold: 0.1 }
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [loadMore]);

  const hasNextPage = pagination?.hasNextPage ?? false;
  const showSentinel = hasNextPage || isLoadingMore;

  return (
    <SecondaryPanel
      header={
        <SidebarBackHeader
          title={t('chat.yourChats')}
          onBack={onBack}
          backgroundColor="var(--olive-1)"
        />
      }
    >
      <Flex direction="column" gap="3">
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

        <Flex direction="column" gap="1" className="no-scrollbar" style={{ flex: 1, overflowY: 'auto' }}>
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
                agentId={agentId}
              />
            ))
          ) : (
            <Flex align="center" justify="center" style={{ padding: 'var(--space-4) var(--space-3)' }}>
              <Text size="2" style={{ color: 'var(--slate-11)' }}>
                {searching ? t('message.noResults') : t('chat.noChatsYet')}
              </Text>
            </Flex>
          )}

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
