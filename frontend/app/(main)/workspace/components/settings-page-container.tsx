'use client';

import React from 'react';
import { Box } from '@radix-ui/themes';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { PAGE_PADDING_X, SETTINGS_MAX_WIDTH } from '@/app/components/ui/responsive';

/** Top padding that clears the fixed mobile hamburger. */
export const MOBILE_PAGE_PADDING_TOP = 'max(56px, var(--space-8))';

/**
 * Inline padding for a full-width scroll surface whose content is capped at `maxWidth`
 * (keeps the scrollbar at the viewport edge, unlike a centered max-width box).
 */
export const pageInlinePadding = (maxWidth: number): string =>
  `max(${PAGE_PADDING_X}, calc((100% - ${maxWidth}px) / 2))`;

export interface SettingsPageContainerProps {
  children: React.ReactNode;
  maxWidth?: number;
}

export function SettingsPageContainer({ children, maxWidth = SETTINGS_MAX_WIDTH }: SettingsPageContainerProps) {
  const isMobile = useIsMobile();
  return (
    <Box
      style={{
        width: '100%',
        maxWidth,
        marginInline: 'auto',
        paddingInline: PAGE_PADDING_X,
        paddingTop: isMobile ? MOBILE_PAGE_PADDING_TOP : 64,
        paddingBottom: 64,
      }}
    >
      {children}
    </Box>
  );
}
