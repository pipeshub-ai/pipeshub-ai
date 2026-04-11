'use client';

import { Flex, Box, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import {
  SIDEBAR_WIDTH,
  HEADER_HEIGHT,
  FOOTER_HEIGHT,
  CONTENT_PADDING,
} from './constants';
import type { SidebarBaseProps } from './types';

/**
 * SidebarBase — shared layout shell for all navigation sidebars.
 *
 * Provides consistent width, background, border, and scroll behavior.
 * Pages supply header, children (scrollable content), and footer slots.
 *
 * When `secondaryPanel` is provided, both the primary sidebar and the
 * secondary panel render side-by-side in a horizontal flex container.
 * This is used for "More Chats", "More items", etc.
 */
export function SidebarBase({ header, children, footer, secondaryPanel, onDismissSecondaryPanel, isMobile, mobileOpen, onMobileClose }: SidebarBaseProps) {
  // ── Mobile full-screen drawer ─────────────────────────────────
  // On mobile, render nothing when closed; a fixed full-width panel when open.
  if (isMobile) {
    if (!mobileOpen) return null;

    // When a secondary panel is active (e.g. More Chats), render it full-screen
    // as an in-place replacement of the primary sidebar — no side-by-side on mobile.
    if (secondaryPanel) {
      return (
        <Box
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 200,
            fontFamily: 'Manrope, sans-serif',
          }}
        >
          {secondaryPanel}
        </Box>
      );
    }

    return (
      <Flex
        direction="column"
        style={{
          position: 'fixed',
          inset: 0,
          zIndex: 200,
          backgroundColor: 'var(--olive-1)',
          fontFamily: 'Manrope, sans-serif',
        }}
      >
        {/* Mobile header: logo area + × close button */}
        {header && (
          <Box
            style={{
              height: `${HEADER_HEIGHT}px`,
              flexShrink: 0,
              position: 'relative',
            }}
          >
            <Box style={{ paddingRight: onMobileClose ? '40px' : undefined }}>
              {header}
            </Box>
            {onMobileClose && (
              <Box
                style={{
                  position: 'absolute',
                  top: '50%',
                  right: 'var(--space-3)',
                  transform: 'translateY(-50%)',
                }}
              >
                <IconButton
                  variant="ghost"
                  color="gray"
                  size="2"
                  onClick={onMobileClose}
                  style={{ margin: 0 }}
                  aria-label="Close sidebar"
                >
                  <MaterialIcon name="close" size={20} color="var(--gray-11)" />
                </IconButton>
              </Box>
            )}
          </Box>
        )}

        {/* Scrollable content */}
        <Box
          className="no-scrollbar"
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: CONTENT_PADDING,
          }}
        >
          {children}
        </Box>

        {/* Footer */}
        {footer && (
          <Box
            style={{
              height: `${FOOTER_HEIGHT}px`,
              flexShrink: 0,
            }}
          >
            {footer}
          </Box>
        )}
      </Flex>
    );
  }

  const primarySidebar = (
    <Flex
      direction="column"
      style={{
        width: `${SIDEBAR_WIDTH}px`,
        height: '100%',
        backgroundColor: 'var(--olive-1)',
        borderRight: '1px solid var(--olive-3)',
        flexShrink: 0,
        fontFamily: 'Manrope, sans-serif',
      }}
    >
      {/* Optional header — fixed height */}
      {header && (
        <Box
          style={{
            height: `${HEADER_HEIGHT}px`,
            flexShrink: 0,
          }}
        >
          {header}
        </Box>
      )}

      {/* Scrollable content area */}
      <Box
        className="no-scrollbar"
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: CONTENT_PADDING,
        }}
      >
        {children}
      </Box>

      {/* Optional footer — fixed height */}
      {footer && (
        <Box
          style={{
            height: `${FOOTER_HEIGHT}px`,
            flexShrink: 0,
          }}
        >
          {footer}
        </Box>
      )}
    </Flex>
  );

  // Reserve horizontal space for primary + secondary so the panel is not
  // clipped by the app shell's overflow:hidden (secondary is position:absolute).
  const sidebarClusterWidth = secondaryPanel ? SIDEBAR_WIDTH * 2 : SIDEBAR_WIDTH;

  return (
    <Box
      style={{
        position: 'relative',
        flexShrink: 0,
        width: `${sidebarClusterWidth}px`,
        minWidth: `${sidebarClusterWidth}px`,
        alignSelf: 'stretch',
      }}
    >
      {primarySidebar}

      {/* Click-outside backdrop — covers the entire viewport so clicking
          anywhere outside the sidebar + panel dismisses it.
          This is a root sidebar-level concern, not page-specific. */}
      {secondaryPanel && onDismissSecondaryPanel && (
        <Box
          onClick={onDismissSecondaryPanel}
          style={{
            position: 'fixed',
            top: 0,
            right: 0,
            bottom: 0,
            // Cover main content only — a full-viewport layer sat above the primary
            // sidebar and swallowed clicks (e.g. New Chat) while More Chats was open.
            left: `${sidebarClusterWidth}px`,
            zIndex: 9,
          }}
        />
      )}

      {/* Secondary panel — absolutely positioned to float over main content */}
      {secondaryPanel && (
        <Box
          style={{
            position: 'absolute',
            top: 0,
            left: `${SIDEBAR_WIDTH}px`,
            height: '100%',
            zIndex: 10,
          }}
        >
          {secondaryPanel}
        </Box>
      )}
    </Box>
  );
}
