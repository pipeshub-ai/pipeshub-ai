'use client';

import React from 'react';
import { Flex, Box, Text, Switch } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

// ========================================
// Types
// ========================================

interface SendImagesRowProps {
  enabled: boolean;
  onToggle: (enabled: boolean) => void;
}

// ========================================
// Component
// ========================================

export function SendImagesRow({ enabled, onToggle }: SendImagesRowProps) {
  return (
    <Flex
      align="center"
      gap="3"
      style={{
        padding: '12px 14px',
        border: '1px solid var(--slate-4)',
        borderRadius: 'var(--radius-2)',
        backgroundColor: 'var(--slate-1)',
      }}
    >
      {/* Icon */}
      <Flex
        align="center"
        justify="center"
        style={{
          width: 36,
          height: 36,
          borderRadius: 'var(--radius-2)',
          backgroundColor: 'var(--slate-3)',
          flexShrink: 0,
        }}
      >
        <MaterialIcon name="image" size={18} color="var(--slate-11)" />
      </Flex>

      {/* Label + description */}
      <Box style={{ flex: 1, minWidth: 0 }}>
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', display: 'block' }}>
          Send images to LLM
        </Text>
        <Text
          size="1"
          style={{
            color: 'var(--slate-10)',
            display: 'block',
            marginTop: 2,
            fontWeight: 300,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          Control whether images are sent to the LLM during web search
        </Text>
      </Box>

      {/* Toggle */}
      <Flex align="center" style={{ flexShrink: 0 }}>
        <Switch
          color="jade"
          size="2"
          checked={enabled}
          onCheckedChange={onToggle}
        />
      </Flex>
    </Flex>
  );
}
