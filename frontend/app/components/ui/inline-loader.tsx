'use client';

import React from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { Spinner } from './spinner';

export interface InlineLoaderProps {
  /** Optional label shown next to the spinner */
  label?: string;
  /** Spinner size. Default: 16 */
  size?: number;
  /** Alignment. Default: 'center' */
  align?: 'start' | 'center';
  /** Extra styles on the wrapping Flex */
  style?: React.CSSProperties;
}

/**
 * InlineLoader — spinner + optional label for "loading more…" / inline list
 * indicators and small empty-state placeholders.
 */
export function InlineLoader({
  label,
  size = 16,
  align = 'center',
  style,
}: InlineLoaderProps) {
  return (
    <Flex
      align="center"
      justify={align}
      gap="2"
      style={{
        padding: 'var(--space-3)',
        color: 'var(--slate-11)',
        ...style,
      }}
    >
      <Spinner size={size} />
      {label ? (
        <Text size="2" style={{ color: 'var(--slate-11)' }}>
          {label}
        </Text>
      ) : null}
    </Flex>
  );
}
