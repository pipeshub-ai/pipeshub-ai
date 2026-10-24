'use client';

import { i18n } from '@/lib/i18n';
import { toast } from '@/lib/store/toast-store';
import { buildChatHref } from '../build-chat-url';
import { useChatStore } from '../store';

const TOAST_DURATION_MS = 8000;

/** True when the user can see this slot's thread right now. */
function isSlotInView(slotId: string, activeSlotId: string | null): boolean {
  if (activeSlotId !== slotId) return false;
  // The active slot stays set after leaving the chat area, so the route decides.
  return typeof window === 'undefined' || window.location.pathname.startsWith('/chat');
}

function titleOf(conversationId: string): string | undefined {
  const { conversations, sharedConversations, agentConversations } = useChatStore.getState();
  const found = [...conversations, ...agentConversations, ...sharedConversations]
    .find((c) => c.id === conversationId);
  return found?.title?.trim() || undefined;
}

/**
 * Tell the user an answer finished in a chat they are not looking at: a toast
 * that opens it, and an unread dot on its sidebar row until they do.
 *
 * Stopped runs and failures are not announced. `wasStopped` is read by the
 * caller before the run settles, because settling clears the flag.
 */
export function notifyAnswerFinishedInBackground(slotId: string, wasStopped: boolean): void {
  if (wasStopped) return;

  const { slots, activeSlotId, markConversationUnread } = useChatStore.getState();
  const slot = slots[slotId];
  if (!slot?.convId || isSlotInView(slotId, activeSlotId)) return;

  markConversationUnread(slot.convId);

  const title = titleOf(slot.convId);
  toast.info(i18n.t('chat.answerReady.title'), {
    ...(title ? { description: title } : {}),
    duration: TOAST_DURATION_MS,
    action: {
      label: i18n.t('chat.answerReady.open'),
      href: buildChatHref({
        agentId: slot.threadAgentId,
        projectId: slot.projectId,
        conversationId: slot.convId,
      }),
    },
  });
}
