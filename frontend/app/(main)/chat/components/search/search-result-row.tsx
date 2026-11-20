'use client';

import React from 'react';
import { Card, Text } from '@radix-ui/themes';
import { formatConversationDateForSearch } from '@/lib/utils/formatters';
import type { Conversation } from '@/chat/types';
import { chatExtension } from '@/config';
import { CMDK_ROW_CLASS, cmdkRowStyle, cmdkRowTitleStyle, cmdkRowSubtitleStyle } from './row-styles';

interface SearchResultRowProps {
  conversation: Conversation;
  onClick: () => void;
}

/** One search hit in the search dialog: a real button, title over its date. */
export function SearchResultRow({ conversation, onClick }: SearchResultRowProps) {
  const dateLabel = formatConversationDateForSearch(conversation.createdAt, conversation.updatedAt);

  return (
    <Card asChild variant="ghost">
      <button type="button" className={CMDK_ROW_CLASS} onClick={onClick} style={cmdkRowStyle}>
        <Text as="div" size="2" weight="medium" style={cmdkRowTitleStyle}>
          {chatExtension.formatConversationTitle(conversation.title)}
        </Text>
        <Text as="div" size="1" weight="medium" style={cmdkRowSubtitleStyle}>
          {dateLabel}
        </Text>
      </button>
    </Card>
  );
}
