'use client';

import React from 'react';
import { Flex, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { ConfidenceLevel } from '@/chat/types';

interface ConfidenceIndicatorProps {
  confidence: ConfidenceLevel;
  /** The space down to the answer. */
  marginBottom?: string;
  /** Keeps the badge's place, unseen, until the answer has a level: the badge then appears without moving the text. */
  placeholder?: boolean;
}

const getConfidenceColor = (confidence: ConfidenceLevel): string => {
  switch (confidence) {
    case 'Very High':
    case 'High':
      return 'var(--accent-a11)';
    case 'Medium':
      return 'var(--amber-11)';
    case 'Low':
      return 'var(--red-11)';
    default:
      return 'var(--text-secondary)';
  }
};

/** How sure the model is of its answer, as the level it gave. */
export function ConfidenceIndicator({ confidence, marginBottom = 'var(--space-2)', placeholder = false }: ConfidenceIndicatorProps) {
  const { t } = useTranslation();
  const color = getConfidenceColor(confidence);

  return (
    <Tooltip content={t('chat.confidenceLevel')}>
      <Flex
        align="center"
        gap="1"
        // Focusable so a keyboard reaches the tooltip that explains the label.
        tabIndex={placeholder ? -1 : 0}
        aria-hidden={placeholder || undefined}
        data-confidence-badge=""
        className="focus-ring-inset"
        style={{
          visibility: placeholder ? 'hidden' : undefined,
          marginBottom,
          border: 'calc(var(--space-1) / 6) solid var(--border-subtle)',
          width: 'fit-content',
          paddingBlock: 'calc(var(--space-1) * 2 / 3)',
          paddingInline: 'calc(var(--space-1) * 4 / 3)',
          borderRadius: 'calc(var(--space-1) / 2)',
          background: 'var(--surface-subtle)',
          cursor: 'default',
        }}
      >
        <span aria-hidden style={{ display: 'inline-flex' }}>
          <MaterialIcon name="auto_awesome" size={11} variant="filled" color={color} />
        </span>
        <Text size="1" weight="medium" style={{ color }}>
          {confidence}
        </Text>
      </Flex>
    </Tooltip>
  );
}
