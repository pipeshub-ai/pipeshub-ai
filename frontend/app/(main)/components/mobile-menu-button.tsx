'use client';

import { useEffect, useRef } from 'react';
import { usePathname } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Box, Flex, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { OpenSidebarButton } from '@/app/components/sidebar/sidebar-expand-button';
import { useIsBelowDesktop } from '@/lib/hooks/use-is-mobile';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useSidebarWidthStore } from '@/lib/store/sidebar-width-store';
import { useNotificationStore } from '@/app/(main)/notifications/store';

/** Chat pages keep a band clear in their top bar for the button, so there it floats in that band. */
export function isChatRoute(pathname: string | null): boolean {
  return pathname === '/chat' || (pathname?.startsWith('/chat/') ?? false);
}

/**
 * Open sidebar control for the drawer layout (below desktop). Shown only while a sidebar that can
 * open as a drawer is mounted, so routes without one never get a dead button. A page whose header
 * row holds the control (an inline SidebarExpandButton) draws it there; any other page gets it in a
 * row of its own above the page, in the flow, so it never sits over the page's title or content.
 * Also closes the drawer on every route change, including browser Back.
 */
export function MobileMenuButton() {
  const { t } = useTranslation();
  const isDrawerLayout = useIsBelowDesktop();
  const hasMobileSidebar = useMobileSidebarStore((s) => s.registeredCount > 0);
  const isOpen = useMobileSidebarStore((s) => s.isOpen);
  const open = useMobileSidebarStore((s) => s.open);
  const close = useMobileSidebarStore((s) => s.close);
  const isInboxOpen = useNotificationStore((s) => s.isPanelOpen);
  const pageHasToggle = useSidebarWidthStore((s) => s.pageExpandControls > 0);
  const pathname = usePathname();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const wasInboxOpen = useRef(isInboxOpen);

  useEffect(() => close(), [pathname, close]);

  // Inbox opened from the drawer has no Inbox button on screen to go back to when it closes:
  // focus comes here, to the control that opens the drawer again, instead of the page body.
  useEffect(() => {
    const closed = wasInboxOpen.current && !isInboxOpen;
    wasInboxOpen.current = isInboxOpen;
    if (!closed || !buttonRef.current) return;
    const active = document.activeElement;
    const focusWasInInbox =
      active === null || active === document.body || active.closest('[data-ph-notifications-panel]') !== null;
    if (focusWasInInbox) buttonRef.current.focus();
  }, [isInboxOpen]);

  if (!isDrawerLayout || !hasMobileSidebar) return null;

  if (!isChatRoute(pathname)) {
    if (pageHasToggle) return null;
    // A header row's height, inset and bottom edge, so the control sits where a page's own header row puts it.
    return (
      <Flex
        align="center"
        style={{
          height: 'var(--space-7)',
          padding: '0 var(--space-3)',
          borderBottom: '1px solid var(--border-subtle)',
          flexShrink: 0,
        }}
      >
        <OpenSidebarButton ref={buttonRef} />
      </Flex>
    );
  }

  return (
    // The bare panel glyph in a clear 40px box, as the design draws it. Never a shadow or border;
    // the ghost button gives the hover tint and the focus ring.
    <Box
      style={{
        position: 'fixed',
        top: 'var(--space-2)',
        left: 'var(--space-2)',
        width: 'var(--space-7)',
        height: 'var(--space-7)',
        zIndex: 100,
        borderRadius: 'var(--radius-3)',
      }}
    >
      <IconButton
        ref={buttonRef}
        className="touch-hit"
        variant="ghost"
        color="gray"
        size="3"
        onClick={open}
        aria-label={t('sidebar.open')}
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        style={{ margin: 0, padding: 0, boxSizing: 'border-box', width: '100%', height: '100%', borderRadius: 'var(--radius-3)' }}
      >
        {/* The same panel glyph, size and colour as the desktop Show sidebar control. */}
        <MaterialIcon name="left_panel_open" size={16} color="var(--text-secondary)" />
      </IconButton>
    </Box>
  );
}
