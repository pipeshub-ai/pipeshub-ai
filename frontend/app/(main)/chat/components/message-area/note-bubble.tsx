'use client';

import React from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { AuthorChip } from '../collaboration/author-chip';
import type { MessageAuthor } from '../../collaboration-types';
import { MentionText } from './mention-text';

export interface NoteBubbleProps {
  content: string;
  /** `null` is a former member; `undefined` is a note whose author the feed did not send. */
  author?: MessageAuthor | null;
  createdAt?: string;
  meUserId?: string | null;
  /** Rendered after the author, e.g. a formatted time. */
  timeLabel?: string;
}

/** A note: a message to people that asks the AI nothing, drawn lighter than a question and its answer. */
export function NoteBubble({ content, author, meUserId, timeLabel }: NoteBubbleProps) {
  const { t } = useTranslation();
  return (
    <Box
      data-testid="note-bubble"
      role="note"
      aria-label={t('chat.mentions.note.aria', { defaultValue: 'Note, not sent to the assistant' })}
      style={{
        background: 'var(--slate-2)',
        border: '1px dashed var(--slate-a6)',
        borderRadius: 'var(--radius-3)',
        padding: 'var(--space-3)',
        maxWidth: '100%',
      }}
    >
      <Flex align="center" gap="2" wrap="wrap" style={{ marginBottom: 'var(--space-1)' }}>
        <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
          {t('chat.mentions.note.label', { defaultValue: 'Note' })}
        </Text>
        {author !== undefined ? <AuthorChip author={author} meUserId={meUserId} /> : null}
        {timeLabel ? (
          <Text size="1" style={{ color: 'var(--slate-11)' }}>
            {timeLabel}
          </Text>
        ) : null}
      </Flex>
      <Text
        size="2"
        data-testid="note-text"
        style={{ color: 'var(--slate-12)', whiteSpace: 'pre-wrap', wordBreak: 'break-word', display: 'block' }}
      >
        <MentionText text={content} />
      </Text>
    </Box>
  );
}
