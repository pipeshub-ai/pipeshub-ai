'use client';

import React from 'react';
import { Flex, Text } from '@radix-ui/themes';

// ========================================
// InstanceHeader
// ========================================

export function InstanceHeader({ connectorName }: { connectorName: string }) {
  return (
    <Flex
      align="center"
      justify="between"
      style={{
        padding: 16,
        backgroundColor: 'var(--olive-2)',
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--olive-3)',
      }}
    >
      <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
        Instance
      </Text>
      <Flex align="center" gap="2">
        <Text size="2" style={{ color: 'var(--gray-12)' }}>
          {connectorName}
        </Text>
      </Flex>
    </Flex>
  );
}
