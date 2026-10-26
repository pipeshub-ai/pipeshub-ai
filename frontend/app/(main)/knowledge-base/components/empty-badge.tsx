'use client';

import React from 'react';
import { Text } from '@radix-ui/themes';

/** Marks a folder or collection that has nothing in it yet. */
export function EmptyBadge({ style }: { style?: React.CSSProperties }) {
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
        color: 'var(--gray-a10)',
        border: '1px solid var(--gray-a5)',
        borderRadius: 999,
        boxSizing: 'border-box',
        whiteSpace: 'nowrap',
        flexShrink: 0,
        ...style,
      }}
    >
      Empty
    </Text>
  );
}
