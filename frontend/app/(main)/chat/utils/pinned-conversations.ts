import type { Conversation } from '@/chat/types';
import { getConversationLastActivityMs } from '@/lib/utils/conversation-activity';

/**
 * Sets or clears one conversation's pin. An unpinned row has no `isPinned`
 * key rather than `false`, which is what the API sends, so a row is the same
 * shape before and after a refetch. Returns the same array when nothing changes.
 */
export function withPinned(
  conversations: Conversation[],
  conversationId: string,
  pinned: boolean,
): Conversation[] {
  let changed = false;
  const next = conversations.map((conversation) => {
    if (conversation.id !== conversationId || (conversation.isPinned === true) === pinned) {
      return conversation;
    }
    changed = true;
    if (pinned) return { ...conversation, isPinned: true };
    const unpinned = { ...conversation };
    delete unpinned.isPinned;
    return unpinned;
  });
  return changed ? next : conversations;
}

/**
 * Pinned chats, newest activity first (the order the API lists them in), and
 * everyone else in the order given.
 */
export function splitPinned(conversations: Conversation[]): {
  pinned: Conversation[];
  rest: Conversation[];
} {
  const pinned = conversations
    .filter((conversation) => conversation.isPinned === true)
    .sort((a, b) => getConversationLastActivityMs(b) - getConversationLastActivityMs(a));
  const rest = conversations.filter((conversation) => conversation.isPinned !== true);
  return { pinned, rest };
}

/** Pinned chats first, then the rest. Returns the same array when none is pinned. */
export function orderPinnedFirst(conversations: Conversation[]): Conversation[] {
  const { pinned, rest } = splitPinned(conversations);
  return pinned.length === 0 ? conversations : [...pinned, ...rest];
}
