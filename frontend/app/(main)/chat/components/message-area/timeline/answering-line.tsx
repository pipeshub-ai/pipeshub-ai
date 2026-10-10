'use client';

import { Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

/** Shown in an AI reply's header while it streams for someone else. */
export function AnsweringLine({ name }: { name: string }) {
  const { t } = useTranslation();
  return (
    <Text as="span" size="1" data-testid="answering-line" role="status" style={{ color: 'var(--slate-11)', whiteSpace: 'nowrap' }}>
      {t('chat.collab.attribution.answeringPerson', { name })}
      <span className="pcc-typing-dots" aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
    </Text>
  );
}
