'use client';

import React from 'react';
import { Flex, Box, Text } from '@radix-ui/themes';

export interface SettingsRowProps {
  label: string;
  description?: string;
  children: React.ReactNode;
}

export function SettingsRow({ label, description, children }: SettingsRowProps) {
  return (
    <Flex
      direction={{ initial: 'column', sm: 'row' }}
      align={{ initial: 'stretch', sm: 'center' }}
      justify="between"
      gap={{ initial: '2', sm: '0' }}
      style={{ width: '100%' }}
    >
      {/* Left: label + description */}
      <Box style={{ flex: 1, minWidth: 0 }}>
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', display: 'block' }}>
          {label}
        </Text>
        {description && (
          <Text
            size="1"
            style={{
              color: 'var(--slate-11)',
              display: 'block',
              marginTop: 2,
              lineHeight: '16px',
              fontWeight: 300,
            }}
          >
            {description}
          </Text>
        )}
      </Box>
      {/* Right: input — proportional width matching Figma */}
      <Box width={{ initial: '100%', sm: '38%' }} minWidth={{ initial: '0', sm: '200px' }} style={{ flexShrink: 0 }}>
        {children}
      </Box>
    </Flex>
  );
}
