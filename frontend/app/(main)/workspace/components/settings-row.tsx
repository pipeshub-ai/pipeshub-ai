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
    <Flex align="center" justify="between" style={{ width: '100%' }}>
      {/* Left: label + description */}
      <Box style={{ flex: 1 }}>
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
      <Box style={{ flex: '0 0 38%', minWidth: 200 }}>{children}</Box>
    </Flex>
  );
}
