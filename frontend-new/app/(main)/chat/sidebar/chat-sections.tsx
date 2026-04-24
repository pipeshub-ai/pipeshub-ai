'use client';

import React from 'react';
import { useSearchParams } from 'next/navigation';
import { Flex } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useChatStore } from '@/chat/store';
import { useCommandStore } from '@/lib/store/command-store';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { debugLog } from '@/chat/debug-logger';
import { ChatSection } from './chat-section';
import { groupConversationsByTime, getNonEmptyGroups } from './time-group';

/**
 * Maximum number of chat items shown per section before
 * overflow triggers a "More" button. Chat-sidebar-specific.
 */
const MAX_VISIBLE_CHATS = 10;

/** Number of skeleton items shown while loading each section */
const SHARED_CHATS_SKELETON_COUNT = 2;
const YOUR_CHATS_SKELETON_COUNT = 3;

/**
 * Chat sections — renders "Shared Chats" and "Your Chats" with
 * time-grouped conversations and overflow "More" buttons.
 *
 * Wrapped in React.memo to prevent parent-cascade re-renders.
 */
export const ChatSections = React.memo(function ChatSections({
  onOpenMoreChats,
}: {
  onOpenMoreChats: (sectionType: 'shared' | 'your') => void;
}) {
  const searchParams = useSearchParams();
  const currentConversationId = searchParams.get('conversationId');
  const { t } = useTranslation();

  const conversations = useChatStore((s) => s.conversations);
  const sharedConversations = useChatStore((s) => s.sharedConversations);
  const isConversationsLoading = useChatStore((s) => s.isConversationsLoading);
  const conversationsError = useChatStore((s) => s.conversationsError);
  const pendingConversations = useChatStore((s) => s.pendingConversations);
  const pagination = useChatStore((s) => s.pagination);
  const sharedPagination = useChatStore((s) => s.sharedPagination);

  // ── Render-reason tracking ──────────────────────────────────────
  debugLog.tick('[sidebar] [ChatSections]');
  const prevChatSectionsRef = React.useRef<Record<string, unknown>>({});
  const currentSectionsVals: Record<string, unknown> = {
    currentConversationId, conversations, sharedConversations,
    isConversationsLoading, conversationsError, pendingConversations, pagination, sharedPagination,
  };
  const sectionsReasons: string[] = [];
  for (const [k, v] of Object.entries(currentSectionsVals)) {
    // eslint-disable-next-line react-hooks/refs -- intentional: debug render-reason tracking
    if (!Object.is(v, prevChatSectionsRef.current[k])) sectionsReasons.push(k);
  }
  if (sectionsReasons.length > 0) {
    debugLog.reason('[sidebar] [ChatSections]', sectionsReasons);
  }
  // eslint-disable-next-line react-hooks/refs -- intentional: update previous-props snapshot for next render diff
  prevChatSectionsRef.current = currentSectionsVals;

  const dispatch = useCommandStore((s) => s.dispatch);
  const closeMobileSidebar = useMobileSidebarStore((s) => s.close);
  const isMobile = useIsMobile();

  const handleNewChat = () => dispatch('newChat');
  const handleSelectConversation = () => {
    if (isMobile) closeMobileSidebar();
  };

  // Overflow detection — show "More" if there are more items than fit,
  // OR if the server indicated there are additional pages to fetch.
  const hasMoreShared = sharedConversations.length > MAX_VISIBLE_CHATS || (sharedPagination?.hasNextPage ?? false);
  const hasMoreYour = conversations.length > MAX_VISIBLE_CHATS || (pagination?.hasNextPage ?? false);

  // Slice for overflow limit
  const visibleShared = hasMoreShared
    ? sharedConversations.slice(0, MAX_VISIBLE_CHATS)
    : sharedConversations;
  const visibleYour = hasMoreYour
    ? conversations.slice(0, MAX_VISIBLE_CHATS)
    : conversations;

  // Time-group only "Your Chats"
  const yourTimeGroups = groupConversationsByTime(visibleYour);
  const yourNonEmptyGroups = getNonEmptyGroups(yourTimeGroups);

  return (
    <Flex
      direction="column"
      gap="3"
      style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}
    >
      {/* Shared Chats — flat list (no time grouping) */}
      <ChatSection
        title={t('chat.sharedChats')}
        conversations={visibleShared}
        isLoading={isConversationsLoading}
        hasError={!!conversationsError}
        currentConversationId={currentConversationId}
        onSelectConversation={handleSelectConversation}
        onNewChat={handleNewChat}
        skeletonCount={SHARED_CHATS_SKELETON_COUNT}
        hasMore={hasMoreShared}
        onMore={() => onOpenMoreChats('shared')}
        emptyStateText={t('chat.noSharedChats')}
      />

      {/* Your Chats — time-grouped */}
      <ChatSection
        title={t('chat.yourChats')}
        timeGroups={yourNonEmptyGroups}
        isLoading={isConversationsLoading}
        hasError={!!conversationsError}
        currentConversationId={currentConversationId}
        onSelectConversation={handleSelectConversation}
        onAdd={handleNewChat}
        onNewChat={handleNewChat}
        skeletonCount={YOUR_CHATS_SKELETON_COUNT}
        isScrollable
        hasMore={hasMoreYour}
        onMore={() => onOpenMoreChats('your')}
        pendingConversations={Object.values(pendingConversations).filter(p => p.isGenerating)}
      />
    </Flex>
  );
});
