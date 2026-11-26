'use client';

import React, { useId, useMemo, useState } from 'react';
import { RecentsToggle } from './recents-toggle';
import { useRouter, useSearchParams } from 'next/navigation';
import { Flex } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useChatStore, selectPendingForSidebar } from '@/chat/store';
import { useCommandStore } from '@/lib/store/command-store';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { debugLog } from '@/chat/debug-logger';
import { ChatSection } from './chat-section';
import { groupConversationsWithPinned } from './time-group';
import { orderPinnedFirst } from '@/chat/utils/pinned-conversations';
import { sidebarDot, strongestDot } from '@/lib/harness-jobs/jobs-list';
import { ChatDotMark, markWords } from './chat-section-element';

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
  const router = useRouter();
  const searchParams = useSearchParams();
  const currentConversationId = searchParams?.get('conversationId') ?? null;
  const { t } = useTranslation();

  const conversations = useChatStore((s) => s.conversations);
  const sharedConversations = useChatStore((s) => s.sharedConversations);
  const isConversationsLoading = useChatStore((s) => s.isConversationsLoading);
  const conversationsError = useChatStore((s) => s.conversationsError);
  const pendingConversations = useChatStore((s) => s.pendingConversations);
  const slots = useChatStore((s) => s.slots);
  const pagination = useChatStore((s) => s.pagination);
  const sharedPagination = useChatStore((s) => s.sharedPagination);

  // ── Render-reason tracking ──────────────────────────────────────
  debugLog.tick('[sidebar] [ChatSections]');
  const prevChatSectionsRef = React.useRef<Record<string, unknown>>({});
  const currentSectionsVals: Record<string, unknown> = {
    currentConversationId, conversations, sharedConversations,
    isConversationsLoading, conversationsError, pendingConversations, slots, pagination, sharedPagination,
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

  const recentsId = useId();
  const [recentsCollapsed, setRecentsCollapsed] = useState(true);

  const handleNewChat = () => {
    if (!dispatch('newChat')) router.push('/chat/');
  };
  const handleSelectConversation = () => {
    if (isMobile) closeMobileSidebar();
  };

  // Overflow detection — show "More" if there are more items than fit,
  // OR if the server indicated there are additional pages to fetch.
  // Only OR in pagination when this section has items: store `pagination` is shared
  // across loads, so "your" chats' hasNext must not show "More" on empty Shared.
  const hasMoreShared =
    sharedConversations.length > MAX_VISIBLE_CHATS ||
    (sharedConversations.length > 0 && (sharedPagination?.hasNextPage ?? false));
  const hasMoreYour =
    conversations.length > MAX_VISIBLE_CHATS ||
    (conversations.length > 0 && (pagination?.hasNextPage ?? false));

  // Slice for overflow limit
  const visibleShared = hasMoreShared
    ? sharedConversations.slice(0, MAX_VISIBLE_CHATS)
    : sharedConversations;
  // Pinned chats take the visible slots first, so the cap never hides one
  // behind "More" while an unpinned chat is shown.
  const orderedYour = useMemo(() => orderPinnedFirst(conversations), [conversations]);
  const visibleYour = hasMoreYour
    ? orderedYour.slice(0, MAX_VISIBLE_CHATS)
    : orderedYour;

  // Group only "Your Chats": Pinned first, then by time
  const yourNonEmptyGroups = groupConversationsWithPinned(visibleYour);

  // Folded, Recents still says when a chat in it needs the person or is working (CJ14-FU-2), from the list it holds.
  const foldedMark = useMemo(
    () =>
      recentsCollapsed
        ? strongestDot(
            [...conversations, ...sharedConversations].map((c) =>
              sidebarDot(c.harnessState, { streaming: false, unread: false, active: c.id === currentConversationId }),
            ),
          )
        : null,
    [recentsCollapsed, conversations, sharedConversations, currentConversationId],
  );

  const activePendingConversations = useMemo(() => {
    const convIds = new Set(conversations.map((c) => c.id));
    return selectPendingForSidebar(pendingConversations, slots, convIds, 'global');
  }, [pendingConversations, slots, conversations]);

  return (
    <Flex
      direction="column"
      gap="3"
      style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}
    >
      {/* Recents — collapsible wrapper for both Shared and Your Chats */}
      <Flex
        direction="column"
        style={recentsCollapsed ? undefined : { flex: 1, minHeight: 0, overflow: 'hidden' }}
      >
        {/* Recents header with collapse toggle */}
        <RecentsToggle
          collapsed={recentsCollapsed}
          onToggle={() => setRecentsCollapsed((c) => !c)}
          controls={recentsId}
          mark={foldedMark ? <ChatDotMark dot={foldedMark.dot} label={markWords(t, foldedMark)} /> : null}
        />

        {!recentsCollapsed && (
          <Flex
            id={recentsId}
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
              onNewChat={handleNewChat}
              skeletonCount={YOUR_CHATS_SKELETON_COUNT}
              isScrollable
              hasMore={hasMoreYour}
              onMore={() => onOpenMoreChats('your')}
              pendingConversations={activePendingConversations}
            />
          </Flex>
        )}
      </Flex>
    </Flex>
  );
});
