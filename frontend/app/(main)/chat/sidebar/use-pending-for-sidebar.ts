'use client';

import { useShallow } from 'zustand/react/shallow';
import { useChatStore, selectPendingForSidebar, type PendingConversation } from '@/chat/store';

/**
 * The pending rows ("Generating title…") a sidebar list shows, as `selectPendingForSidebar` picks them.
 *
 * It subscribes to those rows, not to `slots`: a slot changes on every streaming update, many times a second, and a
 * list that re-rendered with it would draw every chat row again (with its menus and closed dialogs) for each one,
 * while the answer it streams needs that time to draw.
 */
export function usePendingForSidebar(
  resolvedConvIds: ReadonlySet<string>,
  scope: Parameters<typeof selectPendingForSidebar>[3],
): PendingConversation[] {
  return useChatStore(
    useShallow((s) => selectPendingForSidebar(s.pendingConversations, s.slots, resolvedConvIds, scope)),
  );
}
