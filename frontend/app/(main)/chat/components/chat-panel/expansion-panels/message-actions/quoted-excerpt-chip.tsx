'use client';

import React from 'react';
import { Flex, IconButton, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { useTranslation } from 'react-i18next';
import { previewQuotedSelection } from '@/chat/utils/quoted-selection';

interface QuotedExcerptChipProps {
  excerpt: string;
  truncated?: boolean;
  onDismiss: () => void;
}

export function QuotedExcerptChip({ excerpt, truncated, onDismiss }: QuotedExcerptChipProps) {
  const { t } = useTranslation();
  const preview = previewQuotedSelection(excerpt);

  return (
    <Flex
      data-testid="quoted-excerpt-chip"
      align="center"
      justify="between"
      gap="2"
      style={{
        width: '100%',
        minWidth: 0,
        padding: 'var(--space-2) 0',
      }}
    >
      <Flex
        align="center"
        gap="2"
        style={{
          minWidth: 0,
          flex: 1,
          padding: 'var(--space-1) var(--space-2)',
          borderRadius: 'var(--radius-2)',
          backgroundColor: 'var(--olive-3)',
          borderLeft: '3px solid var(--olive-8)',
        }}
      >
        <MaterialIcon name="format_quote" size={16} color="var(--slate-11)" />
        <Text
          size="1"
          title={excerpt}
          style={{
            color: 'var(--slate-11)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            minWidth: 0,
          }}
        >
          {truncated
            ? t('chat.quotedExcerptTruncated', 'Quoted (trimmed)')
            : t('chat.quotedExcerpt', 'Quoted')}
          {': '}
          {preview}
        </Text>
      </Flex>
      <IconButton
        variant="ghost"
        color="gray"
        size="1"
        onClick={onDismiss}
        aria-label={t('chat.dismissQuote', 'Remove quote')}
        style={{ margin: 0, cursor: 'pointer', flexShrink: 0 }}
      >
        <MaterialIcon name="close" size={ICON_SIZES.SECONDARY} color="var(--slate-11)" />
      </IconButton>
    </Flex>
  );
}
