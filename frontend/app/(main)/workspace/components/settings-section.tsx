'use client';

import React from 'react';
import { Flex, Box, Text } from '@radix-ui/themes';

export interface SettingsSectionProps {
  title?: string;
  description?: string;
  rightAction?: React.ReactNode;
  children: React.ReactNode;
}

export function SettingsSection({ title, description, rightAction, children }: SettingsSectionProps) {
  return (
    <Flex
      direction="column"
      gap="4"
      data-testid="settings-section"
      style={{
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        padding: 'var(--space-4)',
        backdropFilter: 'var(--frost-filter)',
        background: 'var(--olive-2)',
      }}
    >
      {/* Section header — only rendered when title is provided */}
      {title && (
        <>
          <Flex align="center" justify="between" wrap="wrap" gap="3">
            <Flex direction="column" gap="1" style={{ flex: '1 1 240px', minWidth: 0 }}>
              <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
                {title}
              </Text>
              {description && (
                <Text size="1" style={{ color: 'var(--slate-11)', fontWeight: 300, lineHeight: '16px' }}>
                  {description}
                </Text>
              )}
            </Flex>
            {rightAction}
          </Flex>
          {/* Divider */}
          <Box style={{ height: 1, backgroundColor: 'var(--gray-5)', width: '100%' }} />
        </>
      )}
      {/* Content rows with gap */}
      <Flex direction="column" gap="5">
        {children}
      </Flex>
    </Flex>
  );
}
