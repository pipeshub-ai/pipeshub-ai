'use client';

import React from 'react';
import { Box } from '@radix-ui/themes';
import { useIsBelowDesktop } from '@/lib/hooks/use-is-mobile';
import { PAGE_PADDING_X, pagePaddingX } from '@/app/components/ui/responsive';

/** Top padding below 1024px, under the shell's 40px Open sidebar row: the title starts 56px down. */
export const MOBILE_PAGE_PADDING_TOP = 'var(--space-4)';

/** Top padding of every settings page on desktop (--space-7), list and detail alike. */
export const PAGE_PADDING_TOP = 32;

/**
 * A settings page's top padding: `desktop` from 1024px up, MOBILE_PAGE_PADDING_TOP below, where the
 * Open sidebar row already sits above the page. Keyed on the drawer layout, not the phone width, so a
 * tablet title does not drop by the row's height.
 */
export function usePagePaddingTop(desktop: React.CSSProperties['paddingTop']): React.CSSProperties['paddingTop'] {
  return useIsBelowDesktop() ? MOBILE_PAGE_PADDING_TOP : desktop;
}

/**
 * Inline padding for a full-width scroll surface whose content is capped at `maxWidth`
 * (keeps the scrollbar at the viewport edge, unlike a centered max-width box).
 */
export const pageInlinePadding = (maxWidth: number): string =>
  `max(${PAGE_PADDING_X}, calc((100% - ${maxWidth}px) / 2))`;

export interface SettingsPageContainerProps {
  children: React.ReactNode;
  /** Caps the box width; settings pages fill the content area when it is omitted. */
  maxWidth?: number;
}

export function SettingsPageContainer({ children, maxWidth }: SettingsPageContainerProps) {
  const paddingTop = usePagePaddingTop(PAGE_PADDING_TOP);
  return (
    <Box
      style={{
        width: '100%',
        maxWidth,
        marginInline: 'auto',
        paddingInline: pagePaddingX(100),
        paddingTop,
        paddingBottom: 64,
      }}
    >
      {children}
    </Box>
  );
}
