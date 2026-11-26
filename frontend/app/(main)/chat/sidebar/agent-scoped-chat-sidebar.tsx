'use client';

import React, { useEffect, useCallback, useId, useMemo, useState } from 'react';
import { RecentsToggle } from './recents-toggle';
import { useRouter, useSearchParams } from 'next/navigation';
import { Flex } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ChatStarIcon } from '@/app/components/ui/chat-star-icon';
import { SidebarBase } from '@/app/components/sidebar';
import { ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useChatStore, selectPendingForSidebar } from '@/chat/store';
import type { Conversation } from '@/chat/types';
import { AgentsApi } from '@/app/(main)/agents/api';
import { openFreshAgentChat } from '@/chat/build-chat-url';
import { ChatSidebarFooter } from '@/config';
import { useAgentDetail } from '@/chat/hooks/use-agent-detail';
import { ChatSidebarHeader } from './header';
import { ChatSection } from './chat-section';
import { groupConversationsByTime, getNonEmptyGroups } from './time-group';
import { SidebarItem } from './sidebar-item';
import { onJobListsRefresh } from '@/lib/harness-jobs/swr-keys';
import { JobsNavEntries } from './jobs-nav-entries';
import { AgentMoreChatsSidebar } from './agent-more-chats-sidebar';
import { AgentsSidebar } from './agents-sidebar';
import { SIDEBAR_AGENT_CONVERSATIONS_PAGE_SIZE, MAX_VISIBLE_CHATS } from '../constants';

const YOUR_CHATS_SKELETON_COUNT = 3;

interface AgentScopedChatSidebarProps {
  agentId: string;
}

/**
 * Chat sidebar when URL includes agentId — new chat, your chats
 * (with more + search panel), backed by GET /api/v1/agents/:agentId/conversations.
 */
export const AgentScopedChatSidebar = React.memo(function AgentScopedChatSidebar({
  agentId,
}: AgentScopedChatSidebarProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const currentConversationId = searchParams?.get('conversationId') ?? null;
  const { t } = useTranslation();

  const closeMobile = useMobileSidebarStore((s) => s.close);
  const isMobileOpen = useMobileSidebarStore((s) => s.isOpen);
  const isMobile = useIsMobile();

  const setAgentSidebarAgentId = useChatStore((s) => s.setAgentSidebarAgentId);
  const setAgentConversations = useChatStore((s) => s.setAgentConversations);
  const setAgentConversationsPagination = useChatStore((s) => s.setAgentConversationsPagination);
  const setIsAgentConversationsLoading = useChatStore((s) => s.setIsAgentConversationsLoading);
  const setAgentConversationsError = useChatStore((s) => s.setAgentConversationsError);
  const setAgentStreamTools = useChatStore((s) => s.setAgentStreamTools);

  const agentConversations = useChatStore((s) => s.agentConversations);
  // Kept local, not in the store's `sharedConversations`, which belongs to the global chat list.
  const [sharedAgentConversations, setSharedAgentConversations] = useState<Conversation[]>([]);
  const agentConversationsPagination = useChatStore((s) => s.agentConversationsPagination);
  const isAgentConversationsLoading = useChatStore((s) => s.isAgentConversationsLoading);
  const agentConversationsError = useChatStore((s) => s.agentConversationsError);
  const pendingConversations = useChatStore((s) => s.pendingConversations);
  const slots = useChatStore((s) => s.slots);

  const isAgentsSidebarOpen = useChatStore((s) => s.isAgentsSidebarOpen);
  const closeAgentsSidebar = useChatStore((s) => s.closeAgentsSidebar);

  const isAgentMoreChatsPanelOpen = useChatStore((s) => s.isAgentMoreChatsPanelOpen);
  const toggleAgentMoreChatsPanel = useChatStore((s) => s.toggleAgentMoreChatsPanel);
  const closeAgentMoreChatsPanel = useChatStore((s) => s.closeAgentMoreChatsPanel);

  const conversationsVersion = useChatStore((s) => s.conversationsVersion);

  useEffect(() => {
    setAgentSidebarAgentId(agentId);
    return () => setAgentSidebarAgentId(null);
  }, [agentId, setAgentSidebarAgentId]);

  // The agent lookup is allowed to fail on its own: someone a chat was shared with may have
  // no access to the agent itself, and must still see the chats shared with them. The chat
  // page reads the same cached detail and sets the agent's access from it.
  const { detail: agentResult, unavailable: agentUnavailable } = useAgentDetail(agentId);
  // False once the agent is known to be off limits: someone a chat was shared with cannot start chats on it.
  const canStartChat = !agentUnavailable && agentResult?.agent !== null;

  const loadAgentConversations = useCallback(async (quiet = false) => {
    if (!quiet) {
      setIsAgentConversationsLoading(true);
      setAgentConversationsError(null);
    }
    try {
      const conv = await AgentsApi.fetchAgentConversations(agentId, {
        page: 1,
        limit: SIDEBAR_AGENT_CONVERSATIONS_PAGE_SIZE,
      });
      if (!quiet) setAgentStreamTools(null);
      setAgentConversations(conv.conversations);
      setSharedAgentConversations(conv.sharedConversations);
      setAgentConversationsPagination(conv.pagination);
    } catch {
      // A background re-read that fails keeps the rows already shown.
      if (quiet) return;
      setAgentConversationsError(t('chat.failedToLoad'));
      setAgentConversations([]);
      setSharedAgentConversations([]);
      setAgentConversationsPagination(null);
      setAgentStreamTools(null);
    } finally {
      if (!quiet) setIsAgentConversationsLoading(false);
    }
  }, [
    agentId,
    t,
    setAgentConversations,
    setAgentConversationsPagination,
    setIsAgentConversationsLoading,
    setAgentConversationsError,
    setAgentStreamTools,
  ]);

  useEffect(() => {
    loadAgentConversations();
  }, [agentId, conversationsVersion, loadAgentConversations]);

  // Run dots move without a skeleton: the job lists' refresh re-reads this list in place.
  useEffect(() => onJobListsRefresh(() => void loadAgentConversations(true)), [loadAgentConversations]);

  const handleBackHome = () => {
    if (isMobile) closeMobile();
    closeAgentsSidebar();
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

  const yourTimeGroups = getNonEmptyGroups(groupConversationsByTime(visibleYour));
  const visibleShared = sharedAgentConversations.slice(0, MAX_VISIBLE_CHATS);

  const activePendingConversations = useMemo(() => {
    const agentConvIds = new Set(agentConversations.map((c) => c.id));
    return selectPendingForSidebar(pendingConversations, slots, agentConvIds, { agentId });
  }, [pendingConversations, slots, agentId, agentConversations]);

  const recentsId = useId();
  const [recentsCollapsed, setRecentsCollapsed] = useState(false);

  const secondaryPanel = isAgentsSidebarOpen ? (
    <AgentsSidebar onBack={closeAgentsSidebar} />
  ) : isAgentMoreChatsPanelOpen ? (
    <AgentMoreChatsSidebar agentId={agentId} onBack={closeAgentMoreChatsPanel} />
  ) : undefined;

  return (
    <SidebarBase
      header={<ChatSidebarHeader />}
      footer={<ChatSidebarFooter />}
      secondaryPanel={secondaryPanel}
      onDismissSecondaryPanel={
        isAgentsSidebarOpen
          ? closeAgentsSidebar
          : isAgentMoreChatsPanelOpen
            ? closeAgentMoreChatsPanel
            : undefined
      }
      isMobile={isMobile}
      mobileOpen={isMobileOpen}
      onMobileClose={closeMobile}
    >
      <Flex direction="column" gap="3" style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
        <SidebarItem
          icon={<MaterialIcon name="chevron_left" size={ICON_SIZE_DEFAULT} />}
          label={t('chat.backToChatHome')}
          href="/chat/"
          onClick={handleBackHome}
        />

        {canStartChat && (
          <SidebarItem
            icon={
              <ChatStarIcon size={ICON_SIZE_DEFAULT} color="var(--accent-11)" />
            }
            label={t('chat.newChat')}
            onClick={handleNewAgentChat}
            textColor="var(--accent-11)"
            fontWeight={500}
          />
        )}
        <JobsNavEntries />

        {/* Recents — collapsible wrapper for agent conversations */}
        <Flex
          direction="column"
          style={recentsCollapsed ? { flexShrink: 0 } : { flex: 1, minHeight: 0, overflow: 'hidden' }}
        >
          <RecentsToggle collapsed={recentsCollapsed} onToggle={() => setRecentsCollapsed((c) => !c)} controls={recentsId} />

          {!recentsCollapsed && (
            <Flex id={recentsId} direction="column" gap="3" style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
              {visibleShared.length > 0 && (
                <ChatSection
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
                title={visibleShared.length > 0 ? t('chat.yourChats') : undefined}
                timeGroups={yourTimeGroups}
                isLoading={isAgentConversationsLoading}
                hasError={!!agentConversationsError}
                currentConversationId={currentConversationId}
                onSelectConversation={handleSelectConversation}
                onNewChat={handleNewAgentChat}
                skeletonCount={YOUR_CHATS_SKELETON_COUNT}
                emptyStateText={canStartChat ? undefined : t('chat.noChatsYet')}
                isScrollable
                hasMore={hasMoreYour}
                onMore={toggleAgentMoreChatsPanel}
                pendingConversations={activePendingConversations}
                agentId={agentId}
              />
            </Flex>
          )}
        </Flex>
      </Flex>
    </SidebarBase>
  );
});
