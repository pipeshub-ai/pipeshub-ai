'use client';

import { Box, Flex } from '@radix-ui/themes';

const ROW_MIN_HEIGHT = 44;

export function AgentBuilderPaletteSkeletonList({ count = 5 }: { count?: number }) {
  return (
    <Box style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {Array.from({ length: count }, (_, i) => (
        <Flex
          key={i}
          align="center"
          style={{
            gap: 10,
            minHeight: ROW_MIN_HEIGHT,
            padding: '0 14px',
            borderRadius: 'var(--radius-2)',
            border: '1px solid var(--olive-3)',
            background: 'var(--olive-2)',
          }}
        >
          <Box
            style={{
              width: 36,
              height: 36,
              borderRadius: 'var(--radius-2)',
              backgroundColor: 'var(--slate-4)',
              flexShrink: 0,
              animation: 'shimmer-pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
            }}
          />
          <Box
            style={{
              flex: 1,
              height: 14,
              borderRadius: 'var(--radius-1)',
              backgroundColor: 'var(--slate-4)',
              animation: 'shimmer-pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
            }}
          />
        </Flex>
      ))}
    </Box>
  );
}
