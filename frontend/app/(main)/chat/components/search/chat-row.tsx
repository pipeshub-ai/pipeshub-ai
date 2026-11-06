'use client';

import React from 'react';
import { Card, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { formatConversationDateForSearch } from '@/lib/utils/formatters';
import type { Conversation } from '@/chat/types';
import { chatExtension } from '@/config';
import { CMDK_ROW_CLASS, cmdkRowStyle, cmdkRowTitleStyle, cmdkRowSubtitleStyle } from './row-styles';

interface ChatRowProps {
  conversation: Conversation;
  onClick: () => void;
  showDate?: boolean;
}

/** One chat in the search dialog's browse list: a real button, title on one line. */
export function ChatRow({ conversation, onClick, showDate }: ChatRowProps) {
  const { t } = useTranslation();
  const dateLabel = formatConversationDateForSearch(conversation.createdAt, conversation.updatedAt);
  const sharedByName = conversation.sharedBy?.name?.trim();
  const sharedByLabel =
    conversation.isOwner === false && sharedByName
      ? t('chat.sharedBy', { name: sharedByName })
      : undefined;

  return (
    <Card asChild variant="ghost">
      <button type="button" className={CMDK_ROW_CLASS} onClick={onClick} style={cmdkRowStyle}>
        <Text as="div" size="2" weight="medium" style={cmdkRowTitleStyle}>
          {chatExtension.formatConversationTitle(conversation.title)}
        </Text>
        {sharedByLabel && (
          <Text as="div" size="1" weight="medium" style={cmdkRowSubtitleStyle}>
            {sharedByLabel}
          </Text>
        )}
        {showDate && (
          <Text as="div" size="1" weight="medium" style={cmdkRowSubtitleStyle}>
            {dateLabel}
          </Text>
        )}
      </button>
    </Card>
  );
}
