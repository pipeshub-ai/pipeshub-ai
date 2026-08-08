'use client';

import React, { useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';

export function StatCard({
  label,
  value,
  subtitle,
  valueColor,
  onClick,
}: {
  label: string;
  value: number;
  subtitle: string;
  valueColor?: string;
  onClick?: () => void;
}) {
  const [isHovered, setIsHovered] = useState(false);
  const isClickable = !!onClick;

  return (
    <Flex
      direction="column"
      align="center"
      justify="center"
      gap="2"
      onClick={onClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        flex: 1,
        padding: 'var(--space-6) var(--space-4)',
        backgroundColor: isHovered && isClickable ? 'var(--olive-3)' : 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        cursor: isClickable ? 'pointer' : 'default',
        transition: 'background-color 150ms ease',
      }}
    >
      <Text
        size="2"
        weight="medium"
        style={{
          color: 'var(--gray-12)',
          textAlign: 'center',
          width: '100%',
        }}
      >
        {label}
      </Text>
      <Flex direction="column" align="center" gap="2" style={{ width: '100%' }}>
        <Text
          size="6"
          weight="medium"
          style={{ color: valueColor || 'var(--gray-12)', textAlign: 'center', width: '100%' }}
        >
          {value}
        </Text>
        <Text
          size="1"
          style={{ color: 'var(--gray-10)', textAlign: 'center', width: '100%' }}
        >
          {subtitle}
        </Text>
      </Flex>
    </Flex>
  );
}
