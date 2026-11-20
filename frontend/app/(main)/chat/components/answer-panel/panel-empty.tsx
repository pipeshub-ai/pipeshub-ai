'use client';

import React, { type ReactNode } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

/**
 * A tab of the column beside a chat with nothing in it, or nothing it may show: a glyph, what the tab is, and one line
 * on what will put something there. Centred, as an empty column has nothing to sit under. The glyph and the line are
 * a step darker than the faintest text, so they keep their contrast.
 */
export function PanelEmpty({ icon, title, body, testId }: { icon: string; title: string; body?: string; testId?: string }) {
  return (
    <Flex direction="column" align="center" justify="center" gap="2" px="4" data-testid={testId} style={{ height: '100%' }}>
      <span aria-hidden style={{ display: 'inline-flex' }}>
        <MaterialIcon name={icon} size={24} color="var(--text-placeholder)" />
      </span>
      <Text size="2" weight="medium" align="center" style={{ color: 'var(--text-primary)' }}>
        {title}
      </Text>
      {body && (
        <Text as="p" size="1" align="center" style={{ color: 'var(--text-secondary)', lineHeight: 'var(--line-height-1)', margin: 0 }}>
          {body}
        </Text>
      )}
    </Flex>
  );
}

/** The muted line a tab shows before its answer lands: it says what will be here, not "empty". */
export function PanelPending({ children }: { children: ReactNode }) {
  return (
    <Text
      as="p"
      size="1"
      data-testid="panel-pending"
      style={{ color: 'var(--text-secondary)', lineHeight: 'var(--line-height-1)', margin: 0 }}
    >
      {children}
    </Text>
  );
}
