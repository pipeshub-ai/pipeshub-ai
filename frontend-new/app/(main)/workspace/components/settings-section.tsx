'use client';

import React from 'react';
import { Flex, Box, Text } from '@radix-ui/themes';

export interface SettingsSectionProps {
  title: string;
  rightAction?: React.ReactNode;
  children: React.ReactNode;
}

export function SettingsSection({ title, rightAction, children }: SettingsSectionProps) {
  return (
    <Flex
      direction="column"
      gap="4"
      style={{
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        padding: 'var(--space-4)',
        backdropFilter: 'blur(25px)',
        background: 'var(--olive-2)',
      }}
    >
      {/* Section header */}
      <Flex align="center" justify="between">
        <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {title}
        </Text>
        {rightAction}
      </Flex>
      {/* Divider */}
      <Box style={{ height: 1, backgroundColor: 'var(--gray-5)', width: '100%' }} />
      {/* Content rows with gap */}
      <Flex direction="column" gap="5">
        {children}
      </Flex>
    </Flex>
  );
}
