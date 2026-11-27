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
import { groupConversationsWithPinned } from './time-group';
import { splitPinned } from '@/chat/utils/pinned-conversations';
import { chatSlots } from '@/config';
import { RAIL_SHARED_UNPINNED_ROWS, RAIL_UNPINNED_ROWS } from '../constants';
import type { Conversation } from '@/chat/types';
import { readRailMemory, writeRailMemory } from './rail-memory';

/**
 * A section's chats for the rail: every pinned one, newest activity first, then its `limit` newest others,
 * and whether it needs "More" for the rest of those: past the cap here, or another page on the server. The store keeps
 * more, so the realtime watches still see every chat; only the rail shows fewer. `hasNextPage` is this section's own
 * pagination.
 */
function railRows(conversations: Conversation[], hasNextPage: boolean, limit: number) {
  const { pinned, rest } = splitPinned(conversations);
  return {
    rows: [...pinned, ...rest.slice(0, limit)],
    hasMore: rest.length > limit || (conversations.length > 0 && hasNextPage),
  };
}

/** Number of skeleton items shown while loading each section */
const YOUR_CHATS_SKELETON_COUNT = 3;

/**
 * Chat sections in the rail's own scroller: "Shared chats", then "Your chats". A chat the viewer pinned leads the list
 * it is in, so the rail still says who can read it. Shared chats shows only when it has a chat, unless the slots offer
 * a way to start one (`useSharedChatsStart`): then it stays, with a + and a line while empty. Your chats files its
 * unpinned rows under the day of their last activity (Today, Yesterday, Last 7 days, Older), showing only the days that
 * have one, under its pins. Each lists all its pins and at most RAIL_SHARED_UNPINNED_ROWS (Shared chats) or
 * RAIL_UNPINNED_ROWS (Your chats) others, then "More", which opens the rest of that section.
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
  // The first load keeps the room Shared chats took last time, its pins included, so nothing under it moves when the
  // lists arrive; from the first frame, before the effect that starts the load. A row count, never a title.
  const [lastTime] = useState(() => ({ sharedRows: readRailMemory('sharedRows') ?? 0 }));
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
    () => railRows(sharedConversations, sharedPagination?.hasNextPage ?? false, RAIL_SHARED_UNPINNED_ROWS),
    [sharedConversations, sharedPagination],
  );
  const your = useMemo(
    () => railRows(conversations, pagination?.hasNextPage ?? false, RAIL_UNPINNED_ROWS),
    [conversations, pagination],
  );
  // Days from the viewer's local midnight, which moves on by itself: a rail left open overnight regroups at midnight.
  // The pins come first, with no label of their own.
  const today = useLocalDay();
  const yourDays = useMemo(() => groupConversationsWithPinned(your.rows, today), [your.rows, today]);

  // Rows Shared chats shows, its More included.
  const sharedRowsShown = shared.rows.length + (shared.hasMore ? 1 : 0);
  const reserveShared = lastTime.sharedRows > 0 && firstLoadPending && sharedRowsShown === 0;
  // What the next first load keeps room for: what the section shows after each load, and as it changes.
  useEffect(() => {
    if (listsLoadedAt === null) return;
    writeRailMemory('sharedRows', sharedRowsShown);
  }, [listsLoadedAt, sharedRowsShown]);

  const convIds = useMemo(() => new Set(conversations.map((c) => c.id)), [conversations]);
  const activePendingConversations = usePendingForSidebar(convIds, 'global');

  return (
    <Flex direction="column" gap="4">
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

      {/* Your chats: the pinned ones, then the rest by day, with a + that starts a new chat */}
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
        emptyStateText={t('chat.yourChatsEmpty')}
      />
    </Flex>
  );
});
