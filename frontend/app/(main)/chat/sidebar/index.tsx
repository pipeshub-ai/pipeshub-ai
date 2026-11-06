'use client';

import React, { useEffect } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { SidebarBase } from '@/app/components/sidebar';
import { useChatStore } from '@/chat/store';
import { debugLog } from '@/chat/debug-logger';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useFeatureFlagsStore, selectProjectsEnabled } from '@/lib/store/feature-flags-store';
import { useIsBelowDesktop } from '@/lib/hooks/use-is-mobile';
import { useCommandStore } from '@/lib/store/command-store';
import { isCommandKey } from '@/lib/utils/platform';
import { ChatSidebarFooter, chatSlots } from '@/config';
import { ChatSidebarHeader } from './header';
import { StaticNavSection } from './static-nav-section';
import { ChatSections } from './chat-sections';
import { MoreChatsSidebar } from './more-chats-sidebar';
import { AgentScopedChatSidebar } from './agent-scoped-chat-sidebar';
import { AgentMoreChatsSidebar } from './agent-more-chats-sidebar';
import { ProjectConversationsRail } from './project-conversations-sidebar';
import { useSidebarConversations } from './use-sidebar-conversations';

/**
 * The chat rail: one for every page that shows it (the app's layout keeps it, see `chat-rail-routes.ts`). Its header,
 * footer and scroll area stay; only the middle swaps with the URL: the main chats, an agent's (`agentId`) or a
 * project's (`projectId`). "More" opens a secondary panel to the right of it, through `secondaryPanel`.
 *
 * Wrapped in React.memo to prevent parent-cascade re-renders from the layout re-rendering during navigation.
 */
function ChatSidebarRoot() {
  debugLog.tick('[sidebar] [ChatSidebar]');

  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const agentId = searchParams.get('agentId');
  const projectsEnabled = useFeatureFlagsStore(selectProjectsEnabled);
  // A thread can't be scoped to both an agent and a project — agentId wins.
  const rawProjectId = searchParams.get('projectId');
  // `/chat/?projectId=…` is both the new-chat resting state and the
  // in-conversation state. Show the project's chats in either case.
  const projectId =
    !agentId && projectsEnabled && rawProjectId?.trim()
      ? rawProjectId
      : null;

  // The main lists load where they show: not for an agent's or a project's chats.
  useSidebarConversations(!agentId && !projectId);
  // `chatSlots` never changes while the app runs, so this hook runs on every render or on none.
  chatSlots.useRailEffects?.();

  const isMoreChatsPanelOpen = useChatStore((s) => s.isMoreChatsPanelOpen);
  const moreChatsSectionType = useChatStore((s) => s.moreChatsSectionType);
  const toggleMoreChatsPanel = useChatStore((s) => s.toggleMoreChatsPanel);
  const closeMoreChatsPanel = useChatStore((s) => s.closeMoreChatsPanel);
  const isAgentMoreChatsPanelOpen = useChatStore((s) => s.isAgentMoreChatsPanelOpen);
  const closeAgentMoreChatsPanel = useChatStore((s) => s.closeAgentMoreChatsPanel);

  const isMobileOpen = useMobileSidebarStore((s) => s.isOpen);
  const closeMobileSidebar = useMobileSidebarStore((s) => s.close);
  // Below desktop the sidebar is the navigation drawer.
  const isMobile = useIsBelowDesktop();

  // More stays open from page to page here; pages with a rail of their own never show it, so it closes with this one.
  useEffect(() => () => useChatStore.getState().closeMoreChatsPanel(), []);

  useEffect(() => {
    if (pathname === '/chat' || pathname === '/chat/' || pathname.startsWith('/chat/')) {
      return;
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (!isCommandKey(e)) return;
      if (e.shiftKey && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        if (!useCommandStore.getState().dispatch('newChat')) router.push('/chat/');
        return;
      }
      if (!e.shiftKey && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        if (!useCommandStore.getState().dispatch('openCommandPalette')) {
          router.push('/chat/');
        }
        return;
      }
      if (e.key === 'n') {
        e.preventDefault();
        if (!useCommandStore.getState().dispatch('newChat')) router.push('/chat/');
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [pathname, router]);

  let secondaryPanel: React.ReactNode;
  let onDismissSecondaryPanel: (() => void) | undefined;
  if (agentId) {
    if (isAgentMoreChatsPanelOpen) {
      secondaryPanel = <AgentMoreChatsSidebar agentId={agentId} onBack={closeAgentMoreChatsPanel} />;
      onDismissSecondaryPanel = closeAgentMoreChatsPanel;
    }
  } else if (!projectId && isMoreChatsPanelOpen && moreChatsSectionType) {
    secondaryPanel = <MoreChatsSidebar sectionType={moreChatsSectionType} onBack={closeMoreChatsPanel} />;
    onDismissSecondaryPanel = closeMoreChatsPanel;
  }

  const isMain = !agentId && !projectId;
  return (
    <SidebarBase
      header={<ChatSidebarHeader />}
      // The navigation rows stay put; only the lists below them scroll.
      pinned={isMain ? <StaticNavSection /> : undefined}
      footer={<ChatSidebarFooter />}
      contentKey={agentId ? `agent:${agentId}` : projectId ? `project:${projectId}` : 'main'}
      scrollEdgeDivider={isMain}
      secondaryPanel={secondaryPanel}
      onDismissSecondaryPanel={onDismissSecondaryPanel}
      isMobile={isMobile}
      mobileOpen={isMobileOpen}
      onMobileClose={closeMobileSidebar}
    >
      {agentId ? (
        <AgentScopedChatSidebar agentId={agentId} />
      ) : projectId ? (
        <ProjectConversationsRail projectId={projectId} />
      ) : (
        <ChatSections onOpenMoreChats={toggleMoreChatsPanel} />
      )}
    </SidebarBase>
  );
}

export default React.memo(ChatSidebarRoot);
