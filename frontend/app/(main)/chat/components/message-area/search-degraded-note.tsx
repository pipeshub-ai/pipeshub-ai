'use client';

import React from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

/** Small, non-alarming note under an answer or search results when the search was answered from a partial search. */
export function SearchDegradedNote() {
  const { t } = useTranslation();
  return (
    <Flex align="center" gap="1" role="status" style={{ marginBottom: 'var(--space-3)', color: 'var(--slate-11)' }}>
      <MaterialIcon name="info" size={14} color="var(--slate-11)" />
      <Text size="1">{t('chat.searchDegraded')}</Text>
    </Flex>
  );
}
