'use client';

import { useRef, useCallback, useEffect } from 'react';
import { Flex, Box, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import {
  SIDEBAR_WIDTH,
  SIDEBAR_MIN_WIDTH,
  SIDEBAR_MAX_WIDTH,
  HEADER_HEIGHT,
  FOOTER_HEIGHT,
  CONTENT_PADDING,
} from './constants';
import { useSidebarWidthStore } from '@/lib/store/sidebar-width-store';
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

  const sidebarWidth = useSidebarWidthStore((s) => s.sidebarWidth);
  const setSidebarWidth = useSidebarWidthStore((s) => s.setSidebarWidth);

  const primaryRef = useRef<HTMLDivElement>(null);
  const outerRef = useRef<HTMLDivElement>(null);
  const secondaryRef = useRef<HTMLDivElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const widthRef = useRef(sidebarWidth);
  const isDragging = useRef(false);

  // Sync ref when store value changes (e.g. on hydration from localStorage)
  useEffect(() => {
    widthRef.current = sidebarWidth;
  }, [sidebarWidth]);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isDragging.current = true;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const onMouseMove = (ev: MouseEvent) => {
      const clamped = Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, ev.clientX));
      widthRef.current = clamped;
      const hasSecondary = !!secondaryRef.current;
      const clusterW = hasSecondary ? clamped + SIDEBAR_WIDTH : clamped;

      if (primaryRef.current) {
        primaryRef.current.style.width = `${clamped}px`;
      }
      if (outerRef.current) {
        outerRef.current.style.width = `${clusterW}px`;
        outerRef.current.style.minWidth = `${clusterW}px`;
      }
      if (secondaryRef.current) {
        secondaryRef.current.style.left = `${clamped}px`;
      }
      if (backdropRef.current) {
        backdropRef.current.style.left = `${clusterW}px`;
      }
    };

    const onMouseUp = () => {
      isDragging.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
      setSidebarWidth(widthRef.current);
    };

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  }, [setSidebarWidth]);

  const primarySidebar = (
    <Flex
      ref={primaryRef}
      direction="column"
      style={{
        width: `${sidebarWidth}px`,
        height: '100%',
        backgroundColor: 'var(--olive-1)',
        borderRight: '1px solid var(--olive-3)',
        flexShrink: 0,
        fontFamily: 'Manrope, sans-serif',
        position: 'relative',
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

      {/* Drag handle */}
      <Box
        onMouseDown={handleMouseDown}
        style={{
          position: 'absolute',
          top: 0,
          right: -2,
          width: 4,
          height: '100%',
          cursor: 'col-resize',
          zIndex: 20,
        }}
      >
        <Box
          className="sidebar-drag-handle"
          style={{
            position: 'absolute',
            top: 0,
            left: 1,
            width: 2,
            height: '100%',
            borderRadius: 1,
            transition: 'opacity 0.15s',
            opacity: 0,
            backgroundColor: 'var(--olive-8)',
          }}
        />
      </Box>
    </Flex>
  );

  // Reserve horizontal space for primary + secondary so the panel is not
  // clipped by the app shell's overflow:hidden (secondary is position:absolute).
  const sidebarClusterWidth = secondaryPanel ? sidebarWidth + SIDEBAR_WIDTH : sidebarWidth;

  return (
    <Box
      ref={outerRef}
      style={{
        position: 'relative',
        flexShrink: 0,
        width: `${sidebarClusterWidth}px`,
        minWidth: `${sidebarClusterWidth}px`,
        alignSelf: 'stretch',
      }}
    >
      <style>{`.sidebar-drag-handle { opacity: 0; } *:hover > .sidebar-drag-handle { opacity: 1; }`}</style>
      {primarySidebar}

      {/* Click-outside backdrop — covers the entire viewport so clicking
          anywhere outside the sidebar + panel dismisses it.
          This is a root sidebar-level concern, not page-specific. */}
      {secondaryPanel && onDismissSecondaryPanel && (
        <Box
          ref={backdropRef}
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
          ref={secondaryRef}
          style={{
            position: 'absolute',
            top: 0,
            left: `${sidebarWidth}px`,
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
