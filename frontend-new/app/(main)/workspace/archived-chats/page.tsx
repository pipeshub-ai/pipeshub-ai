'use client';

import React, { useEffect, useCallback, useState, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { Flex, Box } from '@radix-ui/themes';
import {
  ArchivedChatsSidebar,
  ArchivedChatView,
  ArchivedChatsEmptyState,
  ArchivedChatSearch,
} from './components';
import { useArchivedChatsStore } from './store';
import type { Conversation } from './types';

/**
 * /workspace/archived-chats
 *
 * Layout: fixed-width left sidebar (archived chat list) + flex content area.
 *
 * URL pattern: /workspace/archived-chats?conversationId=xxx[&agentKey=yyy]
 * - No conversationId → empty state ("Select an archived chat to preview")
 * - conversationId only → assistant archived chat
 * - conversationId + agentKey → agent archived chat
 *
 * After Restore (assistant): navigate to /chat?conversationId=xxx
 * After Restore (agent): navigate to /chat?conversationId=xxx&agentId=yyy
 * After Permanent Delete: select the next conversation in the list,
 *   or show empty state if none remain.
 */
function ArchivedChatsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const conversationId = searchParams.get('conversationId');
  const agentKey = searchParams.get('agentKey');
  const [isSearchOpen, setIsSearchOpen] = useState(false);

  // Store slices
  const conversations = useArchivedChatsStore((s) => s.conversations);
  const agentGroups = useArchivedChatsStore((s) => s.agentGroups);
  const agentGroupsEpoch = useArchivedChatsStore((s) => s.agentGroupsEpoch);
  const isLoadingList = useArchivedChatsStore((s) => s.isLoadingList);
  const isLoadingAgentGroups = useArchivedChatsStore((s) => s.isLoadingAgentGroups);
  const messagesMap = useArchivedChatsStore((s) => s.messagesMap);
  const selected = useArchivedChatsStore((s) => s.selected);
  const isLoadingConversation = useArchivedChatsStore((s) => s.isLoadingConversation);
  const conversationError = useArchivedChatsStore((s) => s.conversationError);

  // Pagination state
  const assistantPagination = useArchivedChatsStore((s) => s.assistantPagination);
  const agentGroupsPagination = useArchivedChatsStore((s) => s.agentGroupsPagination);

  const loadArchivedConversations = useArchivedChatsStore((s) => s.loadArchivedConversations);
  const loadAgentArchivedConversations = useArchivedChatsStore((s) => s.loadAgentArchivedConversations);
  const loadConversation = useArchivedChatsStore((s) => s.loadConversation);
  const loadMoreForAgent = useArchivedChatsStore((s) => s.loadMoreForAgent);
  const loadMoreAssistantChats = useArchivedChatsStore((s) => s.loadMoreAssistantChats);
  const loadMoreAgentGroups = useArchivedChatsStore((s) => s.loadMoreAgentGroups);
  const removeConversation = useArchivedChatsStore((s) => s.removeConversation);
  const clearSelection = useArchivedChatsStore((s) => s.clearSelection);

  // ── On mount: load both assistant and agent archived conversations ─────
  useEffect(() => {
    loadArchivedConversations();
    loadAgentArchivedConversations();
    return () => clearSelection();
  }, []);

  // ── URL → store sync: load conversation when URL params change ──────
  useEffect(() => {
    if (!conversationId) {
      clearSelection();
      return;
    }
    loadConversation(conversationId, '', agentKey ?? null);
  }, [conversationId, agentKey]);

  // ── Race-condition guard for assistant conversations ───────────────────
  useEffect(() => {
    if (!conversationId || agentKey) return;
    if (selected?.id === conversationId) return;
    if (isLoadingConversation) return;
    if (Object.keys(messagesMap).length === 0) return;
    loadConversation(conversationId, '');
  }, [messagesMap]);

  // ── ⌘+K to open search modal ────────────────────────────────────────
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setIsSearchOpen((prev) => !prev);
      }
    }
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, []);

  // ── Handlers ─────────────────────────────────────────────────────────

  const handleSelect = useCallback(
    (conversation: Conversation, selectedAgentKey?: string | null) => {
      const params = new URLSearchParams();
      params.set('conversationId', conversation.id);
      if (selectedAgentKey) params.set('agentKey', selectedAgentKey);
      router.push(`/workspace/archived-chats?${params.toString()}`);
    },
    [router]
  );

  const handleRestored = useCallback(
    (restoredId: string) => {
      removeConversation(restoredId, agentKey ?? null);
      if (agentKey) {
        router.push(`/chat?conversationId=${restoredId}&agentId=${agentKey}`);
      } else {
        router.push(`/chat?conversationId=${restoredId}`);
      }
    },
    [router, removeConversation, agentKey]
  );

  const handleDeleted = useCallback(
    (deletedId: string) => {
      removeConversation(deletedId, agentKey ?? null);

      // Find the next conversation to show — prefer same section
      if (agentKey) {
        const group = agentGroups.find((g) => g.agentKey === agentKey);
        const remaining = (group?.conversations ?? []).filter((c) => c.id !== deletedId);
        if (remaining.length > 0) {
          const deletedIdx = (group?.conversations ?? []).findIndex((c) => c.id === deletedId);
          const nextIdx = Math.min(deletedIdx, remaining.length - 1);
          const params = new URLSearchParams();
          params.set('conversationId', remaining[nextIdx].id);
          params.set('agentKey', agentKey);
          router.push(`/workspace/archived-chats?${params.toString()}`);
          return;
        }
        // No more in this agent group — check other sections
      }

      const remaining = conversations.filter((c) => c.id !== deletedId);
      if (remaining.length > 0) {
        const deletedIndex = conversations.findIndex((c) => c.id === deletedId);
        const nextIndex = Math.min(deletedIndex, remaining.length - 1);
        router.push(
          `/workspace/archived-chats?conversationId=${remaining[nextIndex].id}`
        );
      } else {
        router.push('/workspace/archived-chats');
      }
    },
    [router, conversations, agentGroups, removeConversation, agentKey]
  );

  // Flatten all conversations for the search modal
  const allConversations = [
    ...conversations,
    ...agentGroups.flatMap((g) => g.conversations),
  ];

  // ── Render ────────────────────────────────────────────────────────────

  return (
    <Flex style={{ height: '100%', width: '100%', overflow: 'hidden' }}>
      {/* Left: archived chats list */}
      <ArchivedChatsSidebar
        conversations={conversations}
        agentGroups={agentGroups}
        agentGroupsEpoch={agentGroupsEpoch}
        isLoading={isLoadingList}
        isLoadingAgentGroups={isLoadingAgentGroups}
        selectedConversationId={conversationId}
        selectedAgentKey={agentKey}
        onSelect={handleSelect}
        onSearchClick={() => setIsSearchOpen(true)}
        onLoadMoreForAgent={loadMoreForAgent}
        assistantChatsTotalCount={assistantPagination.totalCount}
        assistantChatsHasMore={assistantPagination.hasMore}
        isLoadingMoreAssistantChats={assistantPagination.isLoadingMore}
        onLoadMoreAssistantChats={loadMoreAssistantChats}
        agentGroupsTotalCount={agentGroupsPagination.totalCount}
        agentGroupsHasMore={agentGroupsPagination.hasMore}
        isLoadingMoreAgentGroups={agentGroupsPagination.isLoadingMore}
        onLoadMoreAgentGroups={loadMoreAgentGroups}
      />

      {/* Right: conversation preview or empty state */}
      <Box style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        {!conversationId ? (
          <ArchivedChatsEmptyState />
        ) : (
          <ArchivedChatView
            conversationId={conversationId}
            conversationTitle={selected?.title ?? ''}
            messages={selected?.messages ?? []}
            isLoading={isLoadingConversation || selected?.id !== conversationId}
            error={conversationError}
            agentKey={agentKey}
            onRestored={handleRestored}
            onDeleted={handleDeleted}
          />
        )}
      </Box>

      {/* Search modal */}
      <ArchivedChatSearch
        open={isSearchOpen}
        onClose={() => setIsSearchOpen(false)}
        conversations={allConversations}
        isLoading={isLoadingList || isLoadingAgentGroups}
        onSelect={(id) => {
          // Find if it's an agent conversation
          for (const group of agentGroups) {
            const found = group.conversations.find((c) => c.id === id);
            if (found) {
              router.push(
                `/workspace/archived-chats?conversationId=${id}&agentKey=${group.agentKey}`
              );
              return;
            }
          }
          router.push(`/workspace/archived-chats?conversationId=${id}`);
        }}
      />
    </Flex>
  );
}

export default function ArchivedChatsPage() {
  return (
    <Suspense>
      <ArchivedChatsPageContent />
    </Suspense>
  );
}
