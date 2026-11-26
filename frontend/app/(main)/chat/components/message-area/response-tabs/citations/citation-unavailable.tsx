'use client';

import React from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useTranslation } from 'react-i18next';

/**
 * A citation of a source the viewer of a shared conversation cannot read.
 * Only its number is known; nothing about the record is shown or linked.
 */
export function CitationUnavailableBadge({ chunkIndex }: { chunkIndex: number }) {
  const { t } = useTranslation();
  const label = t('chat.citationUnavailable');
  return (
    <Flex
      as="span"
      align="center"
      gap="1"
      data-testid="citation-unavailable"
      title={label}
      style={{
        display: 'inline-flex',
        backgroundColor: 'var(--slate-a2)',
        border: '1px dashed var(--slate-a6)',
        padding: '2px var(--space-1)',
        borderRadius: 'var(--radius-1)',
        verticalAlign: 'middle',
        marginLeft: 'var(--space-1)',
        marginRight: '2px',
        minHeight: 'var(--space-5)',
      }}
    >
      <MaterialIcon name="lock" size={12} color="var(--slate-11)" />
      <Text size="1" style={{ color: 'var(--slate-11)', lineHeight: 1 }}>
        {label}
      </Text>
      <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', lineHeight: 1 }}>
        {chunkIndex}
      </Text>
    </Flex>
  );
}

/** The Citations tab row of an unavailable source. */
export function CitationUnavailableCard({ chunkIndex }: { chunkIndex: number }) {
  const { t } = useTranslation();
  return (
    <Flex
      align="center"
      gap="2"
      data-testid="citation-unavailable-card"
      style={{
        padding: 'var(--space-3)',
        border: '1px dashed var(--slate-a6)',
        borderRadius: 'var(--radius-2)',
        backgroundColor: 'var(--slate-a2)',
      }}
    >
      <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
        {chunkIndex}
      </Text>
      <MaterialIcon name="lock" size={14} color="var(--slate-11)" />
      <Text size="2" style={{ color: 'var(--slate-11)' }}>
        {t('chat.citationUnavailable')}
      </Text>
    </Flex>
  );
}
