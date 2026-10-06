'use client';

import { Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { MessageAuthor } from '../../collaboration-types';

interface AnsweredAsLabelProps {
  /** Who the answer was run for; `null` is a former member. */
  asker: MessageAuthor | null;
  meUserId?: string | null;
}

/**
 * Whose access an answer used (UX-10). Compact, for the reply header; nothing when the viewer asked, since
 * they know. The full sentence stays in the tooltip and the accessible name.
 */
export function AnsweredAsLabel({ asker, meUserId }: AnsweredAsLabelProps) {
  const { t } = useTranslation();
  if (asker && meUserId && asker.userId === meUserId) return null;
  const name = asker?.displayName || t('chat.collab.attribution.formerMember');
  const full = t('chat.collab.attribution.askedByAnswered', { name });
  return (
    <Tooltip content={full}>
      <Text
        as="span"
        size="1"
        tabIndex={0}
        aria-label={full}
        title={full}
        data-testid="answered-as-label"
        style={{ color: 'var(--slate-11)', whiteSpace: 'nowrap' }}
      >
        {t('chat.collab.attribution.forPerson', { name })}
      </Text>
    </Tooltip>
  );
}
