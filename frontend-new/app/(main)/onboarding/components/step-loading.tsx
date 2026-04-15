'use client';

import React from 'react';
import { Flex, Text } from '@radix-ui/themes';

export function StepLoading() {
  return (
    <Flex
      direction="column"
      align="center"
      justify="center"
      gap="3"
      style={{
        flex: 1,
        minHeight: '60vh',
      }}
    >
      <Text
        size="4"
        weight="medium"
        style={{ color: 'var(--gray-12)' }}
      >
        Loading...
      </Text>
    </Flex>
  );
}
