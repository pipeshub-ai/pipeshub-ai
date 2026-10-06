'use client';

import { Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { MessageAuthor } from '../../collaboration-types';

interface AnsweredAsLabelProps {
  /** Who the answer was run for; `null` is a former member. */
  asker: MessageAuthor | null;
  meUserId?: string | null;
}

/** Visible under an answer in a collaborative chat: whose question it was and whose access it used (UX-10). */
export function AnsweredAsLabel({ asker, meUserId }: AnsweredAsLabelProps) {
  const { t } = useTranslation();
  const isMe = Boolean(asker && meUserId && asker.userId === meUserId);
  const text = isMe
    ? t('chat.collab.attribution.askedByYouAnswered')
    : t('chat.collab.attribution.askedByAnswered', {
        name: asker?.displayName || t('chat.collab.attribution.formerMember'),
      });
  return (
    <Text
      as="p"
      size="1"
      data-testid="answered-as-label"
      style={{ color: 'var(--slate-11)', margin: 'var(--space-2) 0 0' }}
    >
      {text}
    </Text>
  );
}
