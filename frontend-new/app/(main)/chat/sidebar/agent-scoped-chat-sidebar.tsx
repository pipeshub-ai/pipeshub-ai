'use client';

import React, { useEffect, useCallback } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Flex } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ChatStarIcon } from '@/app/components/ui/chat-star-icon';
import { SidebarBase } from '@/app/components/sidebar';
import { ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useChatStore } from '@/chat/store';
import { AgentsApi } from '@/app/(main)/agents/api';
import { buildChatHref, openFreshAgentChat } from '@/chat/build-chat-url';
import { getAgentSidebarRowMenuAccess } from './agent-sidebar-row-access';
import { ChatSidebarHeader } from './header';
import { ChatSidebarFooter } from './footer';
import { ChatSection } from './chat-section';
import { groupConversationsByTime, getNonEmptyGroups } from './time-group';
import { SidebarItem } from './sidebar-item';
import { AgentMoreChatsSidebar } from './agent-more-chats-sidebar';
import { AgentsSidebar } from './agents-sidebar';
import { AGENT_CONVERSATIONS_PAGE_SIZE, MAX_VISIBLE_CHATS } from '../constants';

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
  const currentConversationId = searchParams.get('conversationId');
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
  const setAgentContextAccess = useChatStore((s) => s.setAgentContextAccess);

  const agentConversations = useChatStore((s) => s.agentConversations);
  const isAgentConversationsLoading = useChatStore((s) => s.isAgentConversationsLoading);
  const agentConversationsError = useChatStore((s) => s.agentConversationsError);
  const pendingConversations = useChatStore((s) => s.pendingConversations);

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

  const loadAgentConversations = useCallback(async () => {
    setIsAgentConversationsLoading(true);
    setAgentConversationsError(null);
    try {
      const [agentRes, conv] = await Promise.all([
        AgentsApi.getAgent(agentId),
        AgentsApi.fetchAgentConversations(agentId, { page: 1, limit: AGENT_CONVERSATIONS_PAGE_SIZE }),
      ]);
      setAgentStreamTools(null);
      setAgentContextAccess(
        agentRes.agent ? getAgentSidebarRowMenuAccess(agentRes.agent) : null,
      );
      setAgentConversations(conv.conversations);
      setAgentConversationsPagination(conv.pagination);
    } catch {
      setAgentConversationsError(t('chat.failedToLoad'));
      setAgentConversations([]);
      setAgentConversationsPagination(null);
      setAgentStreamTools(null);
      setAgentContextAccess(null);
    } finally {
      setIsAgentConversationsLoading(false);
    }
  }, [
    agentId,
    t,
    setAgentConversations,
    setAgentConversationsPagination,
    setIsAgentConversationsLoading,
    setAgentConversationsError,
    setAgentStreamTools,
    setAgentContextAccess,
  ]);

  useEffect(() => {
    loadAgentConversations();
  }, [agentId, conversationsVersion, loadAgentConversations]);

  const handleBackHome = () => {
    if (isMobile) closeMobile();
    closeAgentsSidebar();
    router.push('/chat/');
  };

  const handleNewAgentChat = () => {
    if (isMobile) closeMobile();
    openFreshAgentChat(agentId, router);
  };

  const handleSelectConversation = (id: string) => {
    if (isMobile) closeMobile();
    router.push(buildChatHref({ agentId, conversationId: id }));
  };

  const hasMoreYour = agentConversations.length > MAX_VISIBLE_CHATS;

  const visibleYour = hasMoreYour
    ? agentConversations.slice(0, MAX_VISIBLE_CHATS)
    : agentConversations;

  const yourTimeGroups = getNonEmptyGroups(groupConversationsByTime(visibleYour));

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
      <Flex direction="column" gap="6" style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
        <SidebarItem
          icon={<MaterialIcon name="chevron_left" size={ICON_SIZE_DEFAULT} />}
          label={t('chat.backToChatHome')}
          href="/chat/"
          onClick={handleBackHome}
        />

        <SidebarItem
          icon={
            <ChatStarIcon size={ICON_SIZE_DEFAULT} color="var(--accent-8)" />
          }
          label={t('chat.newChat')}
          onClick={handleNewAgentChat}
          textColor="var(--accent-8)"
          fontWeight={500}
        />

        <Flex direction="column" gap="4" style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
          <ChatSection
            title={t('chat.yourChats')}
            timeGroups={yourTimeGroups}
            isLoading={isAgentConversationsLoading}
            hasError={!!agentConversationsError}
            currentConversationId={currentConversationId}
            onSelectConversation={handleSelectConversation}
            onAdd={handleNewAgentChat}
            onNewChat={handleNewAgentChat}
            skeletonCount={YOUR_CHATS_SKELETON_COUNT}
            isScrollable
            hasMore={hasMoreYour}
            onMore={toggleAgentMoreChatsPanel}
            pendingConversations={Object.values(pendingConversations).filter((p) => p.isGenerating)}
            agentId={agentId}
          />
        </Flex>
      </Flex>
    </SidebarBase>
  );
});
