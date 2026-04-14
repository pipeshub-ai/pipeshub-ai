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
import { MAX_VISIBLE_CHATS, AGENT_CONVERSATIONS_PAGE_SIZE } from '../constants';
import { buildChatHref } from '@/chat/build-chat-url';
import { useDebouncedSearch } from '@/knowledge-base/hooks/use-debounced-search';
import type { Conversation } from '@/chat/types';

interface AgentMoreChatsSidebarProps {
  agentId: string;
  onBack: () => void;
}

/**
 * Secondary panel for agent chat sidebar — overflow list + search (API `search` param)
 * + infinite scroll, mirroring MoreChatsSidebar for the main chat list.
 */
export const AgentMoreChatsSidebar = React.memo(function AgentMoreChatsSidebar({
  agentId,
  onBack,
}: AgentMoreChatsSidebarProps) {
  debugLog.tick('[sidebar] [AgentMoreChatsSidebar]');

  const router = useRouter();
  const searchParams = useSearchParams();
  const currentConversationId = searchParams.get('conversationId');

  const agentConversations = useChatStore((s) => s.agentConversations);
  const agentPagination = useChatStore((s) => s.agentConversationsPagination);
  const moveConversationToTop = useChatStore((s) => s.moveConversationToTop);
  const agentMoreChatsPagination = useChatStore((s) => s.agentMoreChatsPagination);
  const setAgentMoreChatsPagination = useChatStore((s) => s.setAgentMoreChatsPagination);
  const appendAgentConversations = useChatStore((s) => s.appendAgentConversations);

  const { t } = useTranslation();

  const [searchInput, setSearchInput] = useState('');
  const debouncedSearch = useDebouncedSearch(searchInput, 300);
  const [searchRows, setSearchRows] = useState<Conversation[]>([]);
  const [searchPagination, setSearchPagination] = useState<{
    page: number;
    hasNextPage: boolean;
    isLoadingMore: boolean;
  } | null>(null);

  const sentinelRef = useRef<HTMLDivElement>(null);
  const agentMoreChatsPaginationRef = useRef(agentMoreChatsPagination);
  const debouncedSearchRef = useRef(debouncedSearch);
  const searchPaginationRef = useRef(searchPagination);
  useEffect(() => {
    agentMoreChatsPaginationRef.current = agentMoreChatsPagination;
  }, [agentMoreChatsPagination]);
  useEffect(() => {
    debouncedSearchRef.current = debouncedSearch;
  }, [debouncedSearch]);
  useEffect(() => {
    searchPaginationRef.current = searchPagination;
  }, [searchPagination]);

  const searching = debouncedSearch.trim().length > 0;

  // Reset search UI when panel opens
  useEffect(() => {
    setSearchInput('');
    setSearchRows([]);
    setSearchPagination(null);
  }, [agentId]);

  // Server search: page 1 when debounced query changes
  useEffect(() => {
    if (!searching) {
      setSearchRows([]);
      setSearchPagination(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const result = await AgentsApi.fetchAgentConversations(agentId, {
          page: 1,
          limit: AGENT_CONVERSATIONS_PAGE_SIZE,
          search: debouncedSearch.trim(),
        });
        if (cancelled) return;
        setSearchRows(result.conversations);
        setSearchPagination({
          page: result.pagination.page,
          hasNextPage: result.pagination.hasNextPage,
          isLoadingMore: false,
        });
      } catch {
        if (!cancelled) {
          setSearchRows([]);
          setSearchPagination(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [agentId, debouncedSearch, searching]);

  const sourceConversations = searching
    ? searchRows
    : agentConversations.slice(MAX_VISIBLE_CHATS);

  const handleSelect = (id: string) => {
    moveConversationToTop(id);
    router.push(buildChatHref({ agentId, conversationId: id }));
    onBack();
  };

  useEffect(() => {
    if (searching) return;
    if (agentMoreChatsPagination === null && agentPagination) {
      setAgentMoreChatsPagination({
        page: agentPagination.page,
        hasNextPage: agentPagination.hasNextPage,
        isLoadingMore: false,
      });
    }
  }, [searching, agentMoreChatsPagination, agentPagination, setAgentMoreChatsPagination]);

  const loadMoreNonSearch = useCallback(async () => {
    const current = agentMoreChatsPaginationRef.current;
    if (!current || current.isLoadingMore || !current.hasNextPage) return;

    const nextPage = current.page + 1;
    setAgentMoreChatsPagination({ ...current, isLoadingMore: true });

    try {
      const result = await AgentsApi.fetchAgentConversations(agentId, {
        page: nextPage,
        limit: AGENT_CONVERSATIONS_PAGE_SIZE,
      });
      appendAgentConversations(result.conversations);
      setAgentMoreChatsPagination({
        page: nextPage,
        hasNextPage: result.pagination.hasNextPage,
        isLoadingMore: false,
      });
    } catch {
      setAgentMoreChatsPagination({ ...current, isLoadingMore: false });
    }
  }, [agentId, appendAgentConversations, setAgentMoreChatsPagination]);

  const loadMoreSearch = useCallback(async () => {
    const sp = searchPaginationRef.current;
    if (!sp || sp.isLoadingMore || !sp.hasNextPage) return;
    const q = debouncedSearchRef.current.trim();
    if (!q) return;

    setSearchPagination({ ...sp, isLoadingMore: true });
    try {
      const nextPage = sp.page + 1;
      const result = await AgentsApi.fetchAgentConversations(agentId, {
        page: nextPage,
        limit: AGENT_CONVERSATIONS_PAGE_SIZE,
        search: q,
      });
      setSearchRows((prev) => {
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
      setSearchPagination({
        page: nextPage,
        hasNextPage: result.pagination.hasNextPage,
        isLoadingMore: false,
      });
    } catch {
      setSearchPagination({ ...sp, isLoadingMore: false });
    }
  }, [agentId]);

  const loadMore = useCallback(() => {
    if (searching) {
      loadMoreSearch();
    } else {
      loadMoreNonSearch();
    }
  }, [searching, loadMoreSearch, loadMoreNonSearch]);

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

  const isLoadingMore = searching
    ? searchPagination?.isLoadingMore ?? false
    : agentMoreChatsPagination?.isLoadingMore ?? false;
  const hasNextPage = searching
    ? searchPagination?.hasNextPage ?? false
    : agentMoreChatsPagination?.hasNextPage ?? false;

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
          {sourceConversations.length > 0 ? (
            sourceConversations.map((conv) => (
              <ChatSectionElement
                key={conv.id}
                conversation={conv}
                isActive={currentConversationId === conv.id}
                onClick={() => handleSelect(conv.id)}
                agentId={agentId}
              />
            ))
          ) : (
            <Flex align="center" justify="center" style={{ padding: '16px 12px' }}>
              <Text size="2" style={{ color: 'var(--slate-11)' }}>
                {searching ? t('message.noResults') : t('chat.noChatsYet')}
              </Text>
            </Flex>
          )}

          {showSentinel && (
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
