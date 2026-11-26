'use client';

import React from 'react';
import { Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

/** Marks a folder or collection that has nothing in it yet. */
export function EmptyBadge({ style }: { style?: React.CSSProperties }) {
  const { t } = useTranslation();
  return (
    <Text
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        height: 18,
        padding: '0 7px',
        fontSize: 11,
        lineHeight: '16px',
        fontWeight: 500,
        // gray-11: gray-a10 is under AA's 4.5:1 for 11 px text in dark.
        color: 'var(--gray-11)',
        border: '1px solid var(--gray-a5)',
        borderRadius: 999,
        boxSizing: 'border-box',
        whiteSpace: 'nowrap',
        flexShrink: 0,
        ...style,
      }}
    >
      {t('kb.emptyFolder')}
    </Text>
  );
}
