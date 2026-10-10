'use client';

import React, { useEffect, useState } from 'react';
import { Box, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { MessageAuthor } from '../../../collaboration-types';
import { useUserStore } from '@/lib/store/user-store';
import { MentionText, truncateKeepingTokens } from '../mention-text';
import { QUESTION_CHAR_LIMIT, QueryActions, ToggleButton } from '../expandable-user-query';
import { MessageRow } from './message-row';

export function useAuthorName(author: MessageAuthor | null | undefined, meUserId: string | null): string {
  const { t } = useTranslation();
  if (author === undefined) return t('chat.collab.busy.someone');
  if (author === null) return t('chat.collab.attribution.formerMember');
  if (author.displayName) return author.displayName;
  return meUserId && author.userId === meUserId ? t('chat.collab.attribution.you') : t('chat.collab.busy.someone');
}

interface HumanMessageProps {
  text: string;
  /** `null` is a former member; `undefined` a row whose author the feed did not send. */
  author?: MessageAuthor | null;
  meUserId: string | null;
  time?: string;
  showHeader: boolean;
  /** The row can be edited (a question the viewer may still change). */
  messageId?: string;
  isStreaming?: boolean;
  onEdit?: () => void;
  /** Filter and attachment chips, drawn under the text. */
  children?: React.ReactNode;
}

/** A message from a person, to the AI or to people: avatar, name and time, then the text. */
export function HumanMessage({ text, author, meUserId, time, showHeader, messageId, isStreaming = false, onEdit, children }: HumanMessageProps) {
  const name = useAuthorName(author, meUserId);
  const myAvatar = useUserStore((s) => s.profile?.avatarUrl ?? null);
  const isMe = Boolean(author && meUserId && author.userId === meUserId);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => setExpanded(false), [text]);

  const isLong = Boolean(text.trim()) && text.length > QUESTION_CHAR_LIMIT;
  const shown = isLong && !expanded ? truncateKeepingTokens(text, QUESTION_CHAR_LIMIT).trimEnd() + '…' : text;
  const showEdit = Boolean(!isStreaming && messageId && onEdit);

  return (
    <MessageRow
      testId="human-message"
      name={name}
      tone="person"
      avatarSrc={isMe ? myAvatar : null}
      time={time}
      showHeader={showHeader}
      actions={(active) => <QueryActions question={text} showEdit={showEdit} onEdit={onEdit} visible={active} />}
    >
      <Text
        as="div"
        size="2"
        data-testid="message-text"
        style={{ color: 'var(--slate-12)', whiteSpace: 'pre-wrap', wordBreak: 'break-word', lineHeight: 1.55 }}
      >
        <MentionText text={shown} />
      </Text>
      {isLong ? (
        <Box style={{ marginTop: 'var(--space-1)' }}>
          <ToggleButton expanded={expanded} onToggle={() => setExpanded((v) => !v)} />
        </Box>
      ) : null}
      {children}
    </MessageRow>
  );
}
