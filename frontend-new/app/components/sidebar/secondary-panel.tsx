'use client';

import { Flex, Box } from '@radix-ui/themes';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import {
  SIDEBAR_WIDTH,
  HEADER_HEIGHT,
  CONTENT_PADDING,
} from './constants';
import type { SecondaryPanelProps } from './types';

/**
 * SecondaryPanel — a secondary sidebar panel that opens to the right
 * of the primary sidebar. Used for overflow lists ("More Chats", etc.).
 *
 * Has the same width as the primary sidebar, its own header slot, and
 * a scrollable content area. No footer slot — secondary panels are
 * for browsing/searching, not persistent UI.
 *
 * On mobile, renders full-width to fill the parent full-screen container
 * provided by SidebarBase. Background matches the primary mobile sidebar.
 *
 * Reusable across Chat, KB, and other sidebars.
 */
export function SecondaryPanel({ header, children }: SecondaryPanelProps) {
  const isMobile = useIsMobile();

  return (
    <Flex
      direction="column"
      style={{
        width: isMobile ? '100%' : `${SIDEBAR_WIDTH}px`,
        height: '100%',
        backgroundColor: 'var(--olive-1)',
        ...(isMobile ? {} : { borderRight: '1px solid var(--olive-3)' }),
        flexShrink: 0,
        fontFamily: 'Manrope, sans-serif',
      }}
    >
      {/* Header — fixed height */}
      <Box
        style={{
          height: `${HEADER_HEIGHT}px`,
          flexShrink: 0,
          backgroundColor: 'var(--olive-1)',
        }}
      >
        {header}
      </Box>

      {/* Scrollable content area */}
      <Box
        className="no-scrollbar"
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: CONTENT_PADDING,
          backgroundColor: 'var(--olive-1)',
        }}
      >
        {children}
      </Box>
    </Flex>
  );
}
