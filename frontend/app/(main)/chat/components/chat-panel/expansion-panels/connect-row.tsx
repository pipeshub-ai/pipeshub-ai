'use client';

import React from 'react';
import { Flex, Text } from '@radix-ui/themes';

// Radix's size-1 checkbox: the connected rows' names line up with these.
const CHECKBOX_SPACE = 'calc(var(--space-4) * 0.875)';

interface ConnectRowProps {
  icon: React.ReactNode;
  name: string;
  rowStyle: React.CSSProperties;
  /** The Connect button, or a badge saying why it can't be connected here. */
  action: React.ReactNode;
  testId: string;
}

/** A "not connected" row in the chat's tool filter, lined up with the connected rows above it. */
export function ConnectRow({ icon, name, rowStyle, action, testId }: ConnectRowProps) {
  return (
    <Flex
      align="center"
      justify="between"
      gap="2"
      data-testid={testId}
      style={{ ...rowStyle, padding: 'var(--space-2)' }}
    >
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        <span aria-hidden style={{ width: CHECKBOX_SPACE, flexShrink: 0 }} />
        {icon}
        <Text size="2" weight="medium" style={{ color: 'var(--gray-11)' }} truncate>
          {name}
        </Text>
      </Flex>
      {action}
    </Flex>
  );
}
