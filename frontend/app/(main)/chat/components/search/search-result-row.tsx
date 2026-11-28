'use client';

import React from 'react';
import type { Conversation } from '@/chat/types';
import { ChatRow } from './chat-row';

interface SearchResultRowProps {
  conversation: Conversation;
  onClick: () => void;
}

/** One search hit in the search dialog: the browse list's row, over its date, with who shared it as the list says. */
export function SearchResultRow({ conversation, onClick }: SearchResultRowProps) {
  return <ChatRow conversation={conversation} onClick={onClick} showDate />;
}
