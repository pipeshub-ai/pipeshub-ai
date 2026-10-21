'use client';

import type { ReactNode } from 'react';
import { Flex, Text } from '@radix-ui/themes';

interface PanelSectionProps {
  title: string;
  description?: string;
  /** Rendered on the title's line, at the end (e.g. bulk-select buttons). */
  aside?: ReactNode;
  children: ReactNode;
}

/** A titled group of fields in a right panel, boxed like the Actions setup panel's configuration cards. */
export function PanelSection({ title, description, aside, children }: PanelSectionProps) {
  return (
    <Flex direction="column" gap="3" style={{ minWidth: 0 }}>
      <Flex align="end" justify="between" gap="3" wrap="wrap">
        <Flex direction="column" gap="1" style={{ minWidth: 0, flex: 1 }}>
          <Text size="2" weight="bold" style={{ color: 'var(--slate-12)' }}>
            {title}
          </Text>
          {description ? (
            <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
              {description}
            </Text>
          ) : null}
        </Flex>
        {aside}
      </Flex>
      <Flex
        direction="column"
        gap="4"
        style={{
          padding: 16,
          background: 'var(--olive-2)',
          border: '1px solid var(--olive-3)',
          borderRadius: 'var(--radius-2)',
          minWidth: 0,
        }}
      >
        {children}
      </Flex>
    </Flex>
  );
}
