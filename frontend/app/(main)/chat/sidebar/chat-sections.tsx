'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Flex } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useChatStore } from '@/chat/store';
import { usePendingForSidebar } from './use-pending-for-sidebar';
import type { MoreChatsSection } from '@/chat/store';
import { useCommandStore } from '@/lib/store/command-store';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useIsBelowDesktop } from '@/lib/hooks/use-is-mobile';
import { debugLog } from '@/chat/debug-logger';
import { useLocalDay } from '@/lib/hooks/use-local-day';
import { ChatSection } from './chat-section';
import { getNonEmptyGroups, groupConversationsByTime } from './time-group';
import { splitPinned } from '@/chat/utils/pinned-conversations';
import { chatSlots } from '@/config';
import type { Conversation } from '@/chat/types';
import { readRailMemory, writeRailMemory } from './rail-memory';

/**
 * Rows each section shows before a "More" row opens the full list. The store keeps a full
 * page, so the realtime watches still see every chat; only the rail shows fewer.
 */
const MAX_VISIBLE_CHATS = 5;

/**
 * A section's unpinned chats for the rail, and whether it needs "More": past the cap here, or another page on the
 * server, whose first page may hold only pinned chats. `hasNextPage` is this section's own pagination.
 */
function railRows(conversations: Conversation[], hasNextPage: boolean) {
  const rest = conversations.filter((conversation) => conversation.isPinned !== true);
  return {
    rows: rest.slice(0, MAX_VISIBLE_CHATS),
    hasMore: rest.length > MAX_VISIBLE_CHATS || (conversations.length > 0 && hasNextPage),
  };
}

/** Number of skeleton items shown while loading each section */
const YOUR_CHATS_SKELETON_COUNT = 3;

/**
 * Chat sections in the rail's own scroller: "Pinned", then "Shared chats", then "Your chats". Pinned holds every chat
 * the viewer pinned from either list, newest activity first, and shows only while one is pinned; a pinned chat is
 * listed there alone. Shared chats shows only when it has a chat, unless the slots offer a way to start one
 * (`useSharedChatsStart`): then it stays, with a + and a line while empty. Your chats files its rows under the day of their
 * last activity (Today, Yesterday, Last 7 days, Older), showing only the days that have one. Each lists at most
 * MAX_VISIBLE_CHATS rows, then "More", which opens that section in full.
 *
 * Wrapped in React.memo to prevent parent-cascade re-renders.
 */
export const ChatSections = React.memo(function ChatSections({
  onOpenMoreChats,
}: {
  onOpenMoreChats: (sectionType: MoreChatsSection) => void;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const currentConversationId = searchParams?.get('conversationId') ?? null;
  const { t } = useTranslation();

  const conversations = chatSlots.useVisibleConversations(useChatStore((s) => s.conversations));
  const sharedConversations = chatSlots.useVisibleConversations(useChatStore((s) => s.sharedConversations));
  const isConversationsLoading = useChatStore((s) => s.isConversationsLoading);
  const conversationsError = useChatStore((s) => s.conversationsError);
  const pagination = useChatStore((s) => s.pagination);
  const sharedPagination = useChatStore((s) => s.sharedPagination);
  // The first load keeps the room Pinned and Shared chats took last time, so nothing under them moves when the lists
  // arrive; from the first frame, before the effect that starts the load. A row count each, never a title.
  const [lastTime] = useState(() => ({
    pinnedRows: Math.min(readRailMemory('pinnedRows') ?? 0, MAX_VISIBLE_CHATS + 1),
    sharedRows: Math.min(readRailMemory('sharedRows') ?? 0, MAX_VISIBLE_CHATS + 1),
  }));
  const sharedStart = chatSlots.useSharedChatsStart?.() ?? null;
  const listsLoadedAt = useChatStore((s) => s.conversationsLoadedAt);
  const firstLoadPending = useChatStore((s) => s.conversationsLoadedAt === null && !s.conversationsError);

  // ── Render-reason tracking ──────────────────────────────────────
  debugLog.tick('[sidebar] [ChatSections]');
  const prevChatSectionsRef = React.useRef<Record<string, unknown>>({});
  const currentSectionsVals: Record<string, unknown> = {
    currentConversationId, conversations, sharedConversations,
    isConversationsLoading, conversationsError, pagination, sharedPagination,
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
  // The drawer layout: opening a chat closes the drawer.
  const isMobile = useIsBelowDesktop();

  const handleNewChat = () => {
    if (!dispatch('newChat')) router.push('/chat/');
  };
  const handleSelectConversation = () => {
    if (isMobile) closeMobileSidebar();
  };

  // Each section ORs in only its own pagination, and only when it has items.
  const shared = useMemo(
    () => railRows(sharedConversations, sharedPagination?.hasNextPage ?? false),
    [sharedConversations, sharedPagination],
  );
  const your = useMemo(() => railRows(conversations, pagination?.hasNextPage ?? false), [conversations, pagination]);
  // Days from the viewer's local midnight, which moves on by itself: a rail left open overnight regroups at midnight.
  const today = useLocalDay();
  const yourDays = useMemo(() => getNonEmptyGroups(groupConversationsByTime(your.rows, today)), [your.rows, today]);
  // Both lists' pins in one order, newest activity first; the server lists a chat in one of the two only.
  const pinned = useMemo(() => {
    const all = splitPinned([...conversations, ...sharedConversations]).pinned;
    return { rows: all.slice(0, MAX_VISIBLE_CHATS), hasMore: all.length > MAX_VISIBLE_CHATS };
  }, [conversations, sharedConversations]);

  // Rows each section shows, its More included.
  const pinnedRowsShown = pinned.rows.length + (pinned.hasMore ? 1 : 0);
  const sharedRowsShown = shared.rows.length + (shared.hasMore ? 1 : 0);
  const reservePinned = lastTime.pinnedRows > 0 && firstLoadPending && pinnedRowsShown === 0;
  const reserveShared = lastTime.sharedRows > 0 && firstLoadPending && sharedRowsShown === 0;
  // What the next first load keeps room for: what the sections show after each load, and as they change.
  useEffect(() => {
    if (listsLoadedAt === null) return;
    writeRailMemory('pinnedRows', pinnedRowsShown);
    writeRailMemory('sharedRows', sharedRowsShown);
  }, [listsLoadedAt, pinnedRowsShown, sharedRowsShown]);

  const convIds = useMemo(() => new Set(conversations.map((c) => c.id)), [conversations]);
  const activePendingConversations = usePendingForSidebar(convIds, 'global');

  return (
    <Flex direction="column" gap="4">
      {/* Pinned: earned by a pinned chat, so hidden while nothing is pinned */}
      {(pinned.rows.length > 0 || reservePinned) && (
        <ChatSection
          variant="rail"
          title={t('chat.pinned')}
          conversations={pinned.rows}
          isLoading={isConversationsLoading || reservePinned}
          hasError={!!conversationsError}
          currentConversationId={currentConversationId}
          onSelectConversation={handleSelectConversation}
          onNewChat={handleNewChat}
          // Its rows and its More: the ones it showed last time while the first load is out, then the ones it is
          // about to show while the skeleton fades, so the section keeps one height.
          skeletonCount={reservePinned ? lastTime.pinnedRows : pinnedRowsShown}
          hasMore={pinned.hasMore}
          onMore={() => onOpenMoreChats('pinned')}
        />
      )}

      {/* Shared chats: earned by a chat being in it, so hidden while empty, unless it can start one */}
      {(sharedRowsShown > 0 || reserveShared || sharedStart) && (
        <ChatSection
          variant="rail"
          title={t('chat.sharedChats')}
          conversations={shared.rows}
          // Rows to come keep their room as a skeleton; a section with none keeps its line, so it is the same height.
          isLoading={reserveShared || (isConversationsLoading && sharedRowsShown > 0)}
          hasError={!!conversationsError}
          currentConversationId={currentConversationId}
          onSelectConversation={handleSelectConversation}
          onAdd={sharedStart?.onAdd}
          addLabel={sharedStart?.addLabel}
          onNewChat={handleNewChat}
          // Its rows and its More: the ones it showed last time while the first load is out, then the ones it is
          // about to show while the skeleton fades, so the section keeps one height.
          skeletonCount={reserveShared ? lastTime.sharedRows : sharedRowsShown}
          hasMore={shared.hasMore}
          onMore={() => onOpenMoreChats('shared')}
          emptyStateText={sharedStart?.emptyText ?? t('chat.noSharedChats')}
        />
      )}

      {/* Your chats: the unpinned ones, by day, with a + that starts a new chat */}
      <ChatSection
        variant="rail"
        title={t('chat.yourChats')}
        timeGroups={yourDays}
        isLoading={isConversationsLoading}
        hasError={!!conversationsError}
        currentConversationId={currentConversationId}
        onSelectConversation={handleSelectConversation}
        onAdd={handleNewChat}
        onNewChat={handleNewChat}
        skeletonCount={YOUR_CHATS_SKELETON_COUNT}
        hasMore={your.hasMore}
        onMore={() => onOpenMoreChats('your')}
        pendingConversations={activePendingConversations}
        // Empty only because every chat of yours is pinned: they are right above, so no line says chats will appear.
        emptyStateText={conversations.length > 0 ? null : t('chat.yourChatsEmpty')}
      />
    </Flex>
  );
});
