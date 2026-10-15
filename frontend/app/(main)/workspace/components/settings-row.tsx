'use client';

import React from 'react';
import { Flex, Box, Text } from '@radix-ui/themes';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';

export interface SettingsRowProps {
  label: string;
  description?: string;
  children: React.ReactNode;
}

export function SettingsRow({ label, description, children }: SettingsRowProps) {
  const isMobile = useIsMobile();
  return (
    <Flex
      direction={isMobile ? 'column' : 'row'}
      align={isMobile ? 'stretch' : 'center'}
      justify="between"
      gap={isMobile ? '2' : '0'}
      style={{ width: '100%' }}
    >
      {/* Left: label + description */}
      <Box style={{ flex: isMobile ? undefined : 1, minWidth: 0 }}>
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
      <Box style={isMobile ? { minWidth: 0 } : { flex: '0 0 38%', minWidth: 200 }}>{children}</Box>
    </Flex>
  );
}
