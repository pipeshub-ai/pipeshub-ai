'use client';

import { Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

interface AnsweredViaLabelProps {
  /** Tool name of the delegate agent whose output was sent as the answer, e.g. `coding_agent`. */
  delegate: string;
}

const DELEGATE_NAME = /^[a-z0-9_]+$/;

/** Muted "via coding agent" note for an answer a delegate wrote directly instead of the assistant. */
export function AnsweredViaLabel({ delegate }: AnsweredViaLabelProps) {
  const { t } = useTranslation();
  const label = DELEGATE_NAME.test(delegate)
    ? t(`chat.answeredVia.${delegate}`, { defaultValue: t('chat.answeredVia.other', { name: delegate }) })
    : t('chat.answeredVia.other', { name: delegate });
  return (
    <Text
      as="span"
      size="1"
      data-testid="answered-via-label"
      style={{ color: 'var(--slate-11)', whiteSpace: 'nowrap', minWidth: 0, display: 'block' }}
    >
      {label}
    </Text>
  );
}
