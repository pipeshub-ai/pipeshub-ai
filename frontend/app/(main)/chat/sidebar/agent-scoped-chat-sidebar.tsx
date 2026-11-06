'use client';

import React, { useEffect, useCallback, useMemo, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Flex } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ChatStarIcon } from '@/app/components/ui/chat-star-icon';
import { ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useIsBelowDesktop } from '@/lib/hooks/use-is-mobile';
import { useLocalDay } from '@/lib/hooks/use-local-day';
import { useChatStore } from '@/chat/store';
import { usePendingForSidebar } from './use-pending-for-sidebar';
import type { Conversation } from '@/chat/types';
import { chatSlots } from '@/config';
import { AgentsApi } from '@/app/(main)/agents/api';
import { openFreshAgentChat } from '@/chat/build-chat-url';
import { useAgentDetail } from '@/chat/hooks/use-agent-detail';
import { ChatSection } from './chat-section';
import { getNonEmptyGroups, groupConversationsByTime } from './time-group';
import { SidebarItem } from './sidebar-item';
import { SIDEBAR_AGENT_CONVERSATIONS_PAGE_SIZE, MAX_VISIBLE_CHATS, SIDEBAR_LISTS_STALE_MS } from '../constants';
import { getCurrentUserId } from '@/chat/utils/current-user-id';
import { startListRead, withHeldTitles } from '@/chat/utils/held-titles';

const YOUR_CHATS_SKELETON_COUNT = 3;

/** One empty list, so a store read for another agent's rail keeps the same value between renders. */
const NO_CONVERSATIONS: Conversation[] = [];

interface AgentScopedChatSidebarProps {
  agentId: string;
}

/**
 * The chat rail's middle when the URL includes agentId: a way up to All agents, new chat, then the agent's chats
 * in the main rail's own sections (Shared chats, then Your chats by day, each with More), backed by
 * GET /api/v1/agents/:agentId/conversations. The rail around it (header, footer, the More panel) is the one rail.
 */
export const AgentScopedChatSidebar = React.memo(function AgentScopedChatSidebar({
  agentId,
}: AgentScopedChatSidebarProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const currentConversationId = searchParams?.get('conversationId') ?? null;
  const { t } = useTranslation();

  const closeMobile = useMobileSidebarStore((s) => s.close);
  const isMobile = useIsBelowDesktop();
  const today = useLocalDay();

  const setAgentSidebarAgentId = useChatStore((s) => s.setAgentSidebarAgentId);
  const setIsAgentConversationsLoading = useChatStore((s) => s.setIsAgentConversationsLoading);
  const setAgentConversationsError = useChatStore((s) => s.setAgentConversationsError);
  const setAgentStreamTools = useChatStore((s) => s.setAgentStreamTools);

  // The lists stay in the store after the rail goes, so they are this agent's only when the store says so.
  const listed = useChatStore((s) => s.agentConversationsAgentId === agentId);
  const agentConversations = chatSlots.useVisibleConversations(
    useChatStore((s) => (s.agentConversationsAgentId === agentId ? s.agentConversations : NO_CONVERSATIONS)),
  );
  // Apart from the store's `sharedConversations`, which belongs to the global chat list.
  const sharedAgentConversations = useChatStore((s) =>
    s.agentConversationsAgentId === agentId ? s.agentSharedConversations : NO_CONVERSATIONS,
  );
  // The agent lookup is allowed to fail on its own: someone a chat was shared with may have
  // no access to the agent itself, and must still see the chats shared with them. The chat
  // page reads the same cached detail and sets the agent's access from it.
  const { detail: agentResult, unavailable: agentUnavailable } = useAgentDetail(agentId);
  // False once the agent is known to be off limits: someone a chat was shared with cannot start chats on it.
  const canStartChat = !agentUnavailable && agentResult?.agent !== null;
  const sharedForMe = chatSlots.useAgentSharedConversations(sharedAgentConversations);
  const agentConversationsPagination = useChatStore((s) =>
    s.agentConversationsAgentId === agentId ? s.agentConversationsPagination : null,
  );
  // A skeleton only while nothing of this agent's is listed yet.
  const isAgentConversationsLoading = useChatStore((s) => s.isAgentConversationsLoading) && !listed;
  const agentConversationsError = useChatStore((s) => s.agentConversationsError);

  const toggleAgentMoreChatsPanel = useChatStore((s) => s.toggleAgentMoreChatsPanel);

  const conversationsVersion = useChatStore((s) => s.conversationsVersion);

  useEffect(() => {
    setAgentSidebarAgentId(agentId);
    return () => setAgentSidebarAgentId(null);
  }, [agentId, setAgentSidebarAgentId]);

  // When this agent's chats last arrived: the window coming back into focus reloads them once they are stale.
  const loadedAt = useRef(0);

  const loadAgentConversations = useCallback(async () => {
    // Coming back to the same agent shows its chats at once and swaps them when the load lands.
    const first = useChatStore.getState().agentConversationsAgentId !== agentId;
    // The answer is for this person and this agent's rail: dropped if either changed while it was out.
    const userId = getCurrentUserId();
    const stillWanted = () => getCurrentUserId() === userId && useChatStore.getState().agentSidebarAgentId === agentId;
    const read = startListRead();
    if (first) setIsAgentConversationsLoading(true);
    setAgentConversationsError(null);
    try {
      const conv = await AgentsApi.fetchAgentConversations(agentId, {
        page: 1,
        limit: SIDEBAR_AGENT_CONVERSATIONS_PAGE_SIZE,
      });
      if (!stillWanted()) return;
      loadedAt.current = Date.now();
      setAgentStreamTools(null);
      useChatStore.setState({
        agentConversations: withHeldTitles(conv.conversations, read),
        agentSharedConversations: withHeldTitles(conv.sharedConversations, read),
        agentConversationsAgentId: agentId,
        agentConversationsPagination: conv.pagination,
      });
    } catch {
      if (!stillWanted()) return;
      setAgentStreamTools(null);
      // A refresh that fails keeps the rows on screen.
      if (first) {
        setAgentConversationsError(t('chat.failedToLoad'));
        useChatStore.setState({
          agentConversations: [],
          agentSharedConversations: [],
          agentConversationsAgentId: null,
          agentConversationsPagination: null,
        });
      }
    } finally {
      if (first && stillWanted()) setIsAgentConversationsLoading(false);
    }
  }, [
    agentId,
    t,
    setIsAgentConversationsLoading,
    setAgentConversationsError,
    setAgentStreamTools,
  ]);

  // Read again whenever the rail shows this agent, its rows on screen meanwhile.
  useEffect(() => {
    loadAgentConversations();
  }, [agentId, conversationsVersion, loadAgentConversations]);

  useEffect(() => {
    const refreshIfStale = () => {
      if (Date.now() - loadedAt.current >= SIDEBAR_LISTS_STALE_MS) void loadAgentConversations();
    };
    const onVisible = () => {
      if (document.visibilityState === 'visible') refreshIfStale();
    };
    window.addEventListener('focus', refreshIfStale);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.removeEventListener('focus', refreshIfStale);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [loadAgentConversations]);

  const handleBackToAgents = () => {
    if (isMobile) closeMobile();
  };

  const handleNewAgentChat = () => {
    if (isMobile) closeMobile();
    openFreshAgentChat(agentId, router);
  };

  const handleSelectConversation = () => {
    if (isMobile) closeMobile();
  };

  const hasMoreYour = agentConversations.length > MAX_VISIBLE_CHATS || (agentConversationsPagination?.hasNextPage ?? false);

  const visibleYour = hasMoreYour
    ? agentConversations.slice(0, MAX_VISIBLE_CHATS)
    : agentConversations;

  const yourTimeGroups = getNonEmptyGroups(groupConversationsByTime(visibleYour, today));
  const visibleShared = sharedForMe.slice(0, MAX_VISIBLE_CHATS);

  const agentConvIds = useMemo(() => new Set(agentConversations.map((c) => c.id)), [agentConversations]);
  const activePendingConversations = usePendingForSidebar(agentConvIds, { agentId });

  return (
    <Flex direction="column" gap="3" style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
      {/* Up one level, whatever the way in: an agent's chats sit under All agents. */}
      <SidebarItem
        icon={<MaterialIcon name="chevron_left" size={ICON_SIZE_DEFAULT} />}
        label={t('chat.backToAgents')}
        href="/agents/"
        onClick={handleBackToAgents}
      />

      {canStartChat && (
        <SidebarItem
          icon={
            <ChatStarIcon size={ICON_SIZE_DEFAULT} color="var(--accent-8)" />
          }
          label={t('chat.newChat')}
          onClick={handleNewAgentChat}
          textColor="var(--accent-8)"
          fontWeight={500}
        />
      )}

      {/* The main rail's sections: Shared chats only when it has one, then Your chats by day. */}
      <Flex direction="column" gap="4" style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
        {visibleShared.length > 0 && (
          <ChatSection
            variant="rail"
            title={t('chat.sharedChats')}
            conversations={visibleShared}
            isLoading={isAgentConversationsLoading}
            hasError={!!agentConversationsError}
            currentConversationId={currentConversationId}
            onSelectConversation={handleSelectConversation}
            onNewChat={handleNewAgentChat}
            skeletonCount={2}
            agentId={agentId}
          />
        )}
        <ChatSection
          variant="rail"
          title={t('chat.yourChats')}
          timeGroups={yourTimeGroups}
          isLoading={isAgentConversationsLoading}
          hasError={!!agentConversationsError}
          currentConversationId={currentConversationId}
          onSelectConversation={handleSelectConversation}
          onAdd={canStartChat ? handleNewAgentChat : undefined}
          onNewChat={handleNewAgentChat}
          skeletonCount={YOUR_CHATS_SKELETON_COUNT}
          // Someone who may not chat with this agent gets no promise that chats will appear.
          emptyStateText={canStartChat ? t('chat.yourChatsEmpty') : t('chat.noChatsYet')}
          isScrollable
          hasMore={hasMoreYour}
          onMore={toggleAgentMoreChatsPanel}
          pendingConversations={activePendingConversations}
          agentId={agentId}
        />
      </Flex>
    </Flex>
  );
});
