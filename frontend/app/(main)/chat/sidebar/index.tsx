'use client';

import React, { useEffect, useRef } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { Flex } from '@radix-ui/themes';
import { SidebarBase } from '@/app/components/sidebar';
import { useChatStore } from '@/chat/store';
import { debugLog } from '@/chat/debug-logger';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useFeatureFlagsStore, selectProjectsEnabled } from '@/lib/store/feature-flags-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useCommandStore } from '@/lib/store/command-store';
import { isCommandKey } from '@/lib/utils/platform';
import { ChatSidebarHeader } from './header';
import { ChatSidebarFooter } from './footer';
import { StaticNavSection } from './static-nav-section';
import { MyAgentsSection } from './my-agents-section';
import { ChatSections } from './chat-sections';
import { MoreChatsSidebar } from './more-chats-sidebar';
import { AgentsSidebar } from './agents-sidebar';
import { AgentScopedChatSidebar } from './agent-scoped-chat-sidebar';
import { ProjectConversationsSidebar } from './project-conversations-sidebar';
import { useSidebarConversations } from './use-sidebar-conversations';

/**
 * Chat sidebar — uses SidebarBase shell with header, footer, and custom content.
 * When "More Chats" is opened, the secondary panel appears to the right
 * of the main sidebar via the `secondaryPanel` prop on SidebarBase.
 *
 * Wrapped in React.memo to prevent parent-cascade re-renders from
 * Next.js parallel-route page re-rendering during navigation.
 */
function ChatSidebarInner() {
  debugLog.tick('[sidebar] [ChatSidebar]');

  const isMoreChatsPanelOpen = useChatStore((s) => s.isMoreChatsPanelOpen);
  const moreChatsSectionType = useChatStore((s) => s.moreChatsSectionType);
  const isAgentsSidebarOpen = useChatStore((s) => s.isAgentsSidebarOpen);
  const toggleMoreChatsPanel = useChatStore((s) => s.toggleMoreChatsPanel);
  const closeMoreChatsPanel = useChatStore((s) => s.closeMoreChatsPanel);
  const closeAgentsSidebar = useChatStore((s) => s.closeAgentsSidebar);

  const isMobileOpen = useMobileSidebarStore((s) => s.isOpen);
  const closeMobileSidebar = useMobileSidebarStore((s) => s.close);
  const isMobile = useIsMobile();

  const secondaryPanel = isAgentsSidebarOpen ? (
    <AgentsSidebar onBack={closeAgentsSidebar} />
  ) : isMoreChatsPanelOpen && moreChatsSectionType ? (
    <MoreChatsSidebar
      sectionType={moreChatsSectionType}
      onBack={closeMoreChatsPanel}
    />
  ) : undefined;

  return (
    <SidebarBase
      header={<ChatSidebarHeader />}
      footer={<ChatSidebarFooter />}
      secondaryPanel={secondaryPanel}
      onDismissSecondaryPanel={
        isAgentsSidebarOpen
          ? closeAgentsSidebar
          : isMoreChatsPanelOpen
            ? closeMoreChatsPanel
            : undefined
      }
      isMobile={isMobile}
      mobileOpen={isMobileOpen}
      onMobileClose={closeMobileSidebar}
    >
      <Flex direction="column" gap="3">
        <StaticNavSection />
        <MyAgentsSection />
        <ChatSections onOpenMoreChats={toggleMoreChatsPanel} />
      </Flex>
    </SidebarBase>
  );
}

/**
 * Chooses the main chat sidebar vs agent-scoped conversation list from URL.
 */
function ChatSidebarRoot() {
  useSidebarConversations();
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const agentId = searchParams.get('agentId');
  const projectsEnabled = useFeatureFlagsStore(selectProjectsEnabled);
  // A thread can't be scoped to both an agent and a project — agentId wins.
  const rawProjectId = searchParams.get('projectId');
  // `/chat/?projectId=…` is both the new-chat resting state and the
  // in-conversation state. Show the project sidebar in either case.
  const projectId =
    !agentId && projectsEnabled && rawProjectId?.trim()
      ? rawProjectId
      : null;
  const closeAgentsSidebar = useChatStore((s) => s.closeAgentsSidebar);
  const prevAgentIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (agentId) {
      closeAgentsSidebar();
    }
    const prev = prevAgentIdRef.current;
    if (prev && !agentId) {
      closeAgentsSidebar();
    }
    prevAgentIdRef.current = agentId;
  }, [agentId, closeAgentsSidebar]);

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

  if (agentId) {
    return <AgentScopedChatSidebar agentId={agentId} />;
  }
  if (projectId) {
    return <ProjectConversationsSidebar projectId={projectId} />;
  }
  return <ChatSidebarInner />;
}

export default React.memo(ChatSidebarRoot);
