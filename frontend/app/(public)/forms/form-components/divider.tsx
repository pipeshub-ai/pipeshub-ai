'use client';

import React from 'react';
import { Flex, Text, Box } from '@radix-ui/themes';

// ─── Props ────────────────────────────────────────────────────────────────────

export interface DividerProps {
  /** Centered label text. Defaults to "or continue with". */
  label?: string;
}

// ─── Component ────────────────────────────────────────────────────────────────

/**
 * Divider — a horizontal separator with centered text.
 *
 * Used between the primary sign-in action and alternative provider buttons.
 */
export default function Divider({ label = 'or continue with' }: DividerProps) {
  return (
    <Flex align="center" gap="3" style={{ width: '100%' }}>
      <Box style={{ flex: 1, height: '1px', backgroundColor: 'var(--gray-a5)' }} />
      <Text
        size="1"
        style={{ color: 'var(--gray-10)', whiteSpace: 'nowrap', fontWeight: 400 }}
      >
        {label}
      </Text>
      <Box style={{ flex: 1, height: '1px', backgroundColor: 'var(--gray-a5)' }} />
    </Flex>
  );
}
