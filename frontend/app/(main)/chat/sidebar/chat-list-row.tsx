'use client';

import React, { useLayoutEffect, useRef } from 'react';
import type { Conversation } from '@/chat/types';
import { ChatSectionElement } from './chat-section-element';

/** A list's click handler as it is now, for its rows: they are drawn again only when their own chat changes. */
export function useRowSelect(onSelect: (id: string) => void): { readonly current: (id: string) => void } {
  const select = useRef(onSelect);
  useLayoutEffect(() => {
    select.current = onSelect;
  }, [onSelect]);
  return select;
}

/**
 * A row of a chat list, drawn again only when its chat, whether it is open, or its agent changes. A list draws again
 * whenever one of its chats changes (a chat joins it as a run starts and ends), and every row, with its menus and
 * closed dialogs, would otherwise be drawn again too, in the frame that shows the finished answer. A click goes to the
 * list's handler as it is now (`select`), never an earlier one.
 */
export const ChatListRow = React.memo(function ChatListRow({
  conversation,
  isActive,
  agentId,
  select,
}: {
  conversation: Conversation;
  isActive: boolean;
  agentId?: string;
  select: { readonly current: (id: string) => void };
}) {
  return (
    <ChatSectionElement
      conversation={conversation}
      isActive={isActive}
      onClick={() => select.current(conversation.id)}
      agentId={agentId}
    />
  );
});
