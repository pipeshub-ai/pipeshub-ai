'use client';

import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { MessageAuthor } from '../../collaboration-types';

interface AuthorChipProps {
  /** `null` is a person who has left the organization. */
  author: MessageAuthor | null;
  meUserId?: string | null;
}

/** Who sent a turn: an initial and a name, "You" for the viewer, "Former member" for `null`. */
export function AuthorChip({ author, meUserId }: AuthorChipProps) {
  const { t } = useTranslation();
  const isMe = Boolean(author && meUserId && author.userId === meUserId);
  const name = isMe
    ? t('chat.collab.attribution.you')
    : author?.displayName || t('chat.collab.attribution.formerMember');
  return (
    <Flex
      align="center"
      gap="1"
      data-testid="author-chip"
      aria-label={t('chat.collab.attribution.sentBy', { name })}
      style={{ minWidth: 0 }}
    >
      <span
        aria-hidden="true"
        style={{
          width: 18,
          height: 18,
          borderRadius: '50%',
          background: 'var(--accent-4)',
          color: 'var(--accent-11)',
          fontSize: 10,
          fontWeight: 600,
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
        }}
      >
        {Array.from(name)[0]?.toUpperCase()}
      </span>
      <Text
        size="1"
        weight="medium"
        style={{ color: 'var(--slate-11)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
      >
        {name}
      </Text>
    </Flex>
  );
}
