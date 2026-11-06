'use client';

import { useTranslation } from 'react-i18next';

import { useRef, useCallback, useEffect, useLayoutEffect, useState } from 'react';
import { Flex, Box, Dialog, IconButton, VisuallyHidden } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import {
  SIDEBAR_WIDTH,
  SIDEBAR_MIN_WIDTH,
  SIDEBAR_MAX_WIDTH,
  HEADER_HEIGHT,
  FOOTER_HEIGHT,
  CONTENT_PADDING,
  ELEMENT_HEIGHT,
} from './constants';
import { useSidebarWidthStore, useAppliedSidebarWidth, clampSidebarWidth } from '@/lib/store/sidebar-width-store';
import { useMobileSidebarRegistration } from '@/lib/store/mobile-sidebar-store';
import { BELOW_DESKTOP_MAX_PX } from '@/lib/hooks/use-is-mobile';
import { SidebarFocusStyle } from './sidebar-focus-style';
import type { SidebarBaseProps } from './types';

/** Marks the navigation drawer's dialog, for its scoped rules below. */
const NAV_DRAWER_CLASS = 'nav-drawer';

/**
 * The navigation drawer: a left sheet over a scrim, entering from 16px to the left.
 * - The scrim is the same --black-a4 the command palette uses.
 * - Motion rides the motion roles; under reduced motion there is no animation.
 * - The workspace menu's phone sheet is portalled into the app's root theme, which paints
 *   below this dialog's portal. While a sheet is up the drawer steps aside (as the menu card
 *   does), and the sheet keeps its own pointer events despite the modal's pointer lock.
 *   Closing the sheet brings the drawer back.
 */
const NAV_DRAWER_CSS = `
.rt-DialogOverlay:has(.${NAV_DRAWER_CLASS})::before {
  background-color: var(--black-a4);
}
.rt-BaseDialogContent.${NAV_DRAWER_CLASS}[data-state='open'] {
  animation: nav-drawer-in var(--motion-surface) var(--motion-ease);
}
.rt-BaseDialogContent.${NAV_DRAWER_CLASS}[data-state='closed'] {
  animation: nav-drawer-out var(--motion-control) var(--motion-ease) forwards;
}
@keyframes nav-drawer-in {
  from { opacity: 0; transform: translateX(-16px); }
  to { opacity: 1; transform: translateX(0); }
}
@keyframes nav-drawer-out {
  from { opacity: 1; transform: translateX(0); }
  to { opacity: 0; transform: translateX(-16px); }
}
@media (prefers-reduced-motion: reduce) {
  .rt-BaseDialogContent.${NAV_DRAWER_CLASS}[data-state] {
    animation: none;
  }
}
body:has(.radix-themes > div[role='dialog'][aria-modal='true']) .rt-DialogOverlay:has(.${NAV_DRAWER_CLASS}) {
  visibility: hidden;
}
body:has(.${NAV_DRAWER_CLASS}) .radix-themes > div[role='dialog'][aria-modal='true'],
body:has(.${NAV_DRAWER_CLASS}) .radix-themes > div:has(+ div[role='dialog'][aria-modal='true']) {
  pointer-events: auto;
}`;

const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

/** Where each named scroll area (`contentKey`) was left, so a list shown again opens where it was, not at the top. */
const savedScrollTops = new Map<string, number>();

/**
 * A 1px line across the top of the scroll area, shown only while the content is scrolled, so rows
 * do not slide under the fixed block above with no edge. It sits right before the scroll area and
 * pulls it back up by its own pixel, so it lies over the content's first pixel and never moves the layout.
 */
function ScrollEdgeDivider({ visible }: { visible: boolean }) {
  return (
    <Box
      aria-hidden
      data-sidebar-scroll-edge=""
      data-visible={visible ? 'true' : 'false'}
      style={{
        position: 'relative',
        flexShrink: 0,
        height: 1,
        marginBottom: -1,
        backgroundColor: 'var(--border-subtle)',
        visibility: visible ? 'visible' : 'hidden',
        pointerEvents: 'none',
        zIndex: 1,
      }}
    />
  );
}

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
export function SidebarBase({
  header,
  pinned,
  children,
  contentKey,
  footer,
  footerHeight = FOOTER_HEIGHT,
  secondaryPanel,
  onDismissSecondaryPanel,
  isMobile,
  mobileOpen,
  onMobileClose,
  scrollEdgeDivider = false,
}: SidebarBaseProps & {
  /** Opt in to a 1px divider at the top of the scroll area while it is scrolled. Off by default. */
  scrollEdgeDivider?: boolean;
}) {
  const { t } = useTranslation();
  // Every hook runs before the mobile branch returns: isMobile flips after mount
  // and on resize, and a render with fewer hooks than the last one crashes React.
  const sidebarWidth = useAppliedSidebarWidth();
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

  const [dragHandleHovered, setDragHandleHovered] = useState(false);

  // Scroll edge: whether the scroll area is off its top. Re-read whenever a scroll area mounts
  // (drawer reopened, back from a secondary panel), so a remounted list never starts with a stale line.
  const [isScrolled, setIsScrolled] = useState(false);
  // A named list mounting again (the rail back from another page, a view swapped back, the drawer reopened) goes back
  // to where it was left. A ref callback runs before the browser paints, so it never shows at the top first.
  const scrollAreaRef = useCallback(
    (el: HTMLDivElement | null) => {
      if (!el) return;
      const saved = contentKey === undefined ? undefined : savedScrollTops.get(contentKey);
      if (saved !== undefined) el.scrollTop = saved;
      if (scrollEdgeDivider) setIsScrolled(el.scrollTop > 0);
    },
    [scrollEdgeDivider, contentKey],
  );
  const handleScroll =
    scrollEdgeDivider || contentKey !== undefined
      ? (e: React.UIEvent<HTMLDivElement>) => {
          if (contentKey !== undefined) savedScrollTops.set(contentKey, e.currentTarget.scrollTop);
          if (scrollEdgeDivider) setIsScrolled(e.currentTarget.scrollTop > 0);
        }
      : undefined;

  // The pinned block stays put under the header. The gap below it is outside the scroll area, so the
  // lists are cut where they start and the rest of the layout matches the unpinned one.
  const pinnedBlock = pinned && (
    <Box style={{ flexShrink: 0, padding: 'var(--space-4) var(--space-2) var(--space-3)' }}>{pinned}</Box>
  );
  // A pinned block can outgrow a short rail (a small window, high zoom). The lists then keep three
  // rows and the whole column scrolls, so no row and not the footer is cut off. The scroll area
  // holds its rows' visually hidden controls, which would otherwise stretch that scroll past the footer.
  const contentLayout = pinned
    ? ({ padding: '0 var(--space-2) var(--space-4)', minHeight: ELEMENT_HEIGHT * 3, position: 'relative' } as const)
    : ({ padding: CONTENT_PADDING, minHeight: 0 } as const);
  const columnOverflowY = pinned ? 'auto' : undefined;

  const handlePointerDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    isDragging.current = true;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const onPointerMove = (ev: PointerEvent) => {
      const clamped = clampSidebarWidth(
        Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, ev.clientX)),
        window.innerWidth,
      );
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

    const onPointerUp = () => {
      isDragging.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', onPointerUp);
      document.removeEventListener('pointercancel', onPointerUp);
      // Below desktop the dragged value is already capped; saving it would replace the saved desktop width.
      if (window.innerWidth > BELOW_DESKTOP_MAX_PX) setSidebarWidth(widthRef.current);
    };

    document.addEventListener('pointermove', onPointerMove);
    document.addEventListener('pointerup', onPointerUp);
    document.addEventListener('pointercancel', onPointerUp);
  }, [setSidebarWidth]);

  // The shell shows its hamburger only while a sidebar that can open as a drawer is mounted.
  useMobileSidebarRegistration(!!isMobile);

  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  // The drawer has no Radix trigger (the shell's hamburger opens it through the store), so the
  // control that had focus when it opened is remembered here and gets focus back on close.
  const openerRef = useRef<HTMLElement | null>(null);
  const drawerOpen = !!isMobile && !!mobileOpen;
  useLayoutEffect(() => {
    if (drawerOpen && document.activeElement instanceof HTMLElement && document.activeElement !== document.body) {
      openerRef.current = document.activeElement;
    }
  }, [drawerOpen]);

  // ── Navigation drawer (below desktop) ─────────────────────────
  // A Radix Dialog: it traps focus, hides the page from assistive technology, closes on
  // Escape and on the scrim, and gives focus back to Open sidebar when it closes.
  if (isMobile) {
    const content = secondaryPanel ? (
      // A secondary panel (More chats) replaces the primary view inside the drawer.
      secondaryPanel
    ) : (
      <>
        {/* Header: logo area + close button */}
        {header && (
          <Box
            style={{
              height: `${HEADER_HEIGHT}px`,
              flexShrink: 0,
              position: 'relative',
            }}
          >
            <Box style={{ height: '100%', paddingRight: onMobileClose ? 'var(--space-7)' : undefined }}>
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
                  ref={closeButtonRef}
                  className="touch-hit"
                  variant="ghost"
                  color="gray"
                  size="2"
                  onClick={onMobileClose}
                  // The 32px box it had, kept explicit: the ghost button sizes to its glyph, and the panel glyph is smaller.
                  style={{ margin: 0, width: 'var(--space-6)', height: 'var(--space-6)', boxSizing: 'border-box' }}
                  aria-label={t('sidebar.close')}
                >
                  {/* The same panel glyph, size and colour as the desktop Hide sidebar control. */}
                  <MaterialIcon name="left_panel_close" size={16} color="var(--text-secondary)" />
                </IconButton>
              </Box>
            )}
          </Box>
        )}
        {pinnedBlock}

        {/* Scrollable content */}
        {scrollEdgeDivider && <ScrollEdgeDivider visible={isScrolled} />}
        <Box
          key={contentKey}
          ref={scrollAreaRef}
          onScroll={handleScroll}
          className="no-scrollbar"
          style={{
            flex: 1,
            overflowY: 'auto',
            ...contentLayout,
          }}
        >
          {children}
        </Box>

        {/* Footer */}
        {footer && (
          <Box
            style={{
              height: footerHeight === 'auto' ? undefined : `${footerHeight}px`,
              flexShrink: 0,
            }}
          >
            {footer}
          </Box>
        )}
      </>
    );

    return (
      <Dialog.Root
        open={!!mobileOpen}
        onOpenChange={(open) => {
          if (!open) onMobileClose?.();
        }}
      >
        <Dialog.Content
          ref={dialogRef}
          className={NAV_DRAWER_CLASS}
          aria-modal="true"
          aria-describedby={undefined}
          onOpenAutoFocus={(e) => {
            e.preventDefault();
            // The secondary panel view has no close button: its first control instead.
            (closeButtonRef.current ?? dialogRef.current?.querySelector<HTMLElement>(FOCUSABLE) ?? dialogRef.current)?.focus();
          }}
          onCloseAutoFocus={(e) => {
            e.preventDefault();
            const opener = openerRef.current;
            openerRef.current = null;
            // Inbox opened from the drawer keeps the focus; Open sidebar gets it back when Inbox closes.
            if (document.querySelector('[data-ph-notifications-panel]')) return;
            if (opener?.isConnected) opener.focus();
          }}
          onEscapeKeyDown={(e) => {
            // Escape in an inline rename cancels the rename, not the drawer.
            if (document.activeElement?.closest('[data-inline-rename]')) e.preventDefault();
          }}
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            width: `min(${SIDEBAR_WIDTH}px, calc(100vw - var(--space-8)))`,
            maxWidth: 'none',
            height: '100dvh',
            maxHeight: 'none',
            margin: 0,
            padding: 0,
            borderRadius: 0,
            display: 'flex',
            flexDirection: 'column',
            overflowX: 'hidden',
            overflowY: columnOverflowY ?? 'hidden',
            backgroundColor: 'var(--surface-raised)',
            fontFamily: 'Manrope, sans-serif',
          }}
        >
          <style>{NAV_DRAWER_CSS}</style>
          <SidebarFocusStyle />
          <VisuallyHidden>
            <Dialog.Title>{t('sidebar.navigation')}</Dialog.Title>
          </VisuallyHidden>
          {content}
        </Dialog.Content>
      </Dialog.Root>
    );
  }

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
        overflowX: pinned ? 'hidden' : undefined,
        overflowY: columnOverflowY,
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

      {pinnedBlock}

      {/* Scrollable content area */}
      {scrollEdgeDivider && <ScrollEdgeDivider visible={isScrolled} />}
      <Box
        key={contentKey}
        ref={scrollAreaRef}
        onScroll={handleScroll}
        className="no-scrollbar"
        style={{
          flex: 1,
          overflowY: 'auto',
          ...contentLayout,
        }}
      >
        {children}
      </Box>

      {/* Optional footer — fixed height */}
      {footer && (
        <Box
          style={{
            height: footerHeight === 'auto' ? undefined : `${footerHeight}px`,
            flexShrink: 0,
          }}
        >
          {footer}
        </Box>
      )}

      {/* Drag handle — kept fully inside the sidebar; the app shell clips the
          slot (overflow: hidden) so anything past the border edge is unreachable. */}
      <Box
        onPointerDown={handlePointerDown}
        onMouseEnter={() => setDragHandleHovered(true)}
        onMouseLeave={() => setDragHandleHovered(false)}
        style={{
          position: 'absolute',
          top: 0,
          right: 0,
          width: 4,
          height: '100%',
          cursor: 'col-resize',
          touchAction: 'none',
          zIndex: 20,
        }}
      >
        <Box
          style={{
            position: 'absolute',
            top: 0,
            right: 0,
            width: 2,
            height: '100%',
            borderRadius: 1,
            transition: 'opacity 0.15s',
            opacity: dragHandleHovered ? 1 : 0,
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
        // alignSelf:'stretch' works when this is a direct flex child (old layout).
        // height:'100%' handles the new layout where this sits inside a wrapper Box
        // that carries explicit height from the 100vh flex parent.
        alignSelf: 'stretch',
        height: '100%',
      }}
    >
      <SidebarFocusStyle />
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
