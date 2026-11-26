'use client';

import React from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FLOW_NODE_PANEL_BG } from '../flow-theme';

/** A titled section of the agent (core) card. */
export function Section({
  title,
  icon,
  titleId,
  aside,
  children,
}: {
  title: string;
  icon: string;
  titleId?: string;
  /** Shown at the end of the title row, e.g. a compact control the title labels. */
  aside?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <Box
      style={{
        display: 'flex',
        flexDirection: 'column',
        width: '100%',
        minWidth: 0,
        boxSizing: 'border-box',
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--agent-flow-node-border)',
        background: FLOW_NODE_PANEL_BG,
        boxShadow: '0 1px 0 var(--gray-a4)',
        overflow: 'visible',
        position: 'relative',
      }}
    >
      <Flex
        align="center"
        gap="2"
        px="2"
        py="1"
        style={{
          borderBottom: '1px solid var(--agent-flow-node-border)',
          background: 'var(--agent-flow-section-header-bg)',
        }}
      >
        <Box
          style={{
            width: 24,
            height: 24,
            borderRadius: 'var(--radius-1)',
            background: 'var(--gray-a2)',
            border: '1px solid var(--gray-6)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}
        >
          <MaterialIcon name={icon} size={14} color="var(--agent-flow-text-muted)" />
        </Box>
        <Text id={titleId} size="1" weight="medium" style={{ color: 'var(--agent-flow-text)', lineHeight: '18px' }}>
          {title}
        </Text>
        {aside ? <Box style={{ marginLeft: 'auto', minWidth: 0 }}>{aside}</Box> : null}
      </Flex>
      <Box px="2" py="2" style={{ display: 'flex', flexDirection: 'column', gap: 8, position: 'relative' }}>
        {children}
      </Box>
    </Box>
  );
}
