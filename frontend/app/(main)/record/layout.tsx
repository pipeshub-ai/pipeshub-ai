'use client';

import type { ReactNode } from 'react';
import { Box } from '@radix-ui/themes';

/**
 * Fills the main app column and prevents the global `app-main-scroll` region
 * from scrolling; record pages manage their own internal panes.
 */
export default function RecordSectionLayout({ children }: { children: ReactNode }) {
  return (
    <Box
      style={{
        height: '100%',
        minHeight: 0,
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {children}
    </Box>
  );
}
