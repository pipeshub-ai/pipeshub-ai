'use client';

import type { ReactNode } from 'react';
import { Text } from '@radix-ui/themes';

/**
 * The name of a group of things: small, uppercase, tracked, muted. Uppercase because it is scanned, not read, and
 * that only works while it stays small and quiet.
 */
export function GroupLabel({
  children,
  color = 'var(--text-secondary)',
}: {
  children: ReactNode;
  /** For the rare label that is about the reader: the unread line. */
  color?: string;
}) {
  return (
    <Text size="1" style={{ color, textTransform: 'uppercase', letterSpacing: '0.4px' }}>
      {children}
    </Text>
  );
}
