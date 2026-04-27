'use client';

import React from 'react';
import { Box } from '@radix-ui/themes';

// ─── Props ────────────────────────────────────────────────────────────────────

export interface FormPanelProps {
  children: React.ReactNode;
  /** When true, form sits in the right column beside AuthHero (viewport ≥ md). */
  splitLayout: boolean;
}

// ─── Component ────────────────────────────────────────────────────────────────

/**
 * FormPanel — the right-side panel wrapper that centres any auth form vertically.
 *
 * Used by login/page.tsx and reset-password/page.tsx.
 */
export default function FormPanel({ children, splitLayout }: FormPanelProps) {
  return (
    <Box
      style={{
        flex: splitLayout ? '0 0 43%' : 1,
        position: 'relative',
        backgroundColor: 'var(--color-background)',
        height: '100dvh',
        maxHeight: '100dvh',
        overflowY: 'auto',
        width: splitLayout ? undefined : '100%',
      }}
    >
      <Box
        style={{
          minHeight: '100%',
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: splitLayout ? '40px' : '24px 20px',
        }}
      >
        {children}
      </Box>
    </Box>
  );
}
