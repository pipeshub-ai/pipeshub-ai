'use client';

import React, { useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { formatConversationDateForSearch } from '@/lib/utils/formatters';
import type { Conversation } from '@/chat/types';

interface SearchResultRowProps {
  conversation: Conversation;
  onClick: () => void;
}

export function SearchResultRow({ conversation, onClick }: SearchResultRowProps) {
  const [isHovered, setIsHovered] = useState(false);
  const dateLabel = formatConversationDateForSearch(conversation.createdAt, conversation.updatedAt);

  return (
    <Flex
      direction="column"
      gap="1"
      onClick={onClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        padding: '6px var(--space-2)',
        borderRadius: 'var(--radius-1)',
        cursor: 'pointer',
        backgroundColor: isHovered ? 'var(--slate-a3)' : 'transparent',
        transition: 'background-color 120ms ease',
      }}
    >
      <Text
        size="2"
        style={{
          color: 'var(--slate-12)',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {conversation.title}
      </Text>
      <Text
        size="1"
        style={{
          color: 'var(--slate-a9)',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {dateLabel}
      </Text>
    </Flex>
  );
}
