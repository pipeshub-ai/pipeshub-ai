'use client';

import React from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

/** Server limits (`modules/projects/constants/constants.ts`); the inputs stop at them so a save never fails on length. */
export const PROJECT_NAME_MAX = 100;
export const PROJECT_DESCRIPTION_MAX = 1000;
export const PROJECT_INSTRUCTIONS_MAX = 8000;

/** "120 / 1000", turning amber in the last 10% so the limit isn't a surprise. */
export function CharCount({ id, length, max }: { id?: string; length: number; max: number }) {
  const { t } = useTranslation();
  const nearLimit = length >= max * 0.9;
  return (
    <Text
      id={id}
      size="1"
      aria-live={nearLimit ? 'polite' : undefined}
      style={{ color: nearLimit ? 'var(--amber-11)' : 'var(--slate-10)', flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}
    >
      {t('chat.projects.charCount', { length, max })}
    </Text>
  );
}

interface LabeledFieldProps {
  id: string;
  label: string;
  /** Rendered under the input; space for it is always kept so the dialog doesn't jump when it appears. */
  error?: string | null;
  errorId?: string;
  counter?: React.ReactNode;
  children: React.ReactNode;
}

export function LabeledField({ id, label, error, errorId, counter, children }: LabeledFieldProps) {
  return (
    <Flex direction="column" gap="1">
      <Flex align="end" justify="between" gap="2">
        <Text as="label" htmlFor={id} size="2" weight="medium" style={{ color: 'var(--olive-12)' }}>
          {label}
        </Text>
        {counter}
      </Flex>
      {children}
      {errorId !== undefined && (
        <Text
          id={errorId}
          size="1"
          role={error ? 'alert' : undefined}
          style={{ color: 'var(--red-11)', minHeight: 'var(--line-height-1)', visibility: error ? 'visible' : 'hidden' }}
        >
          {error || ' '}
        </Text>
      )}
    </Flex>
  );
}
