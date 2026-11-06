'use client';

import { forwardRef, useEffect, useLayoutEffect, useMemo } from 'react';
import { usePathname } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Box, IconButton, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useSidebarWidthStore } from '@/lib/store/sidebar-width-store';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useIsBelowDesktop } from '@/lib/hooks/use-is-mobile';
import { getModifierSymbol, isCommandKey } from '@/lib/utils/platform';
import { useNavHidden } from './use-nav-hidden';
import { focusSidebarToggle } from './sidebar-toggle-focus';

interface SidebarExpandButtonProps {
  /**
   * `absolute` (default) pins to the top-left of the nearest `position: relative` page
   * container; `inline` renders just the button for placement inside a page header row, and
   * there it is also the drawer layout's Open sidebar; `shell` is the app-layout fallback —
   * fixed top-left, shown only when no page-level (`absolute` / `inline`) control is mounted.
   */
  placement?: 'absolute' | 'inline' | 'shell';
}

/** Where a floating Show sidebar sits: on the rail's own column, 20px in and 8px down. */
const FLOATING_POSITION = { top: 'var(--space-2)', left: 'calc(var(--space-4) + var(--space-1))' } as const;

/** Show sidebar and the drawer's Open sidebar are one control: a 24px ghost box holding the panel glyph. */
const TOGGLE_STYLE = { width: 'var(--space-5)', height: 'var(--space-5)', padding: 0, margin: 0, flexShrink: 0 } as const;
const toggleGlyph = <MaterialIcon name="left_panel_open" size={16} color="var(--text-secondary)" />;

/** The drawer layout's Open sidebar, for a page header row or the shell's own row. */
export const OpenSidebarButton = forwardRef<HTMLButtonElement>(function OpenSidebarButton(_props, ref) {
  const { t } = useTranslation();
  const isOpen = useMobileSidebarStore((s) => s.isOpen);
  const open = useMobileSidebarStore((s) => s.open);
  return (
    <IconButton
      ref={ref}
      className="touch-hit"
      variant="ghost"
      color="gray"
      size="1"
      onClick={open}
      aria-label={t('sidebar.open')}
      aria-haspopup="dialog"
      aria-expanded={isOpen}
      style={TOGGLE_STYLE}
    >
      {toggleGlyph}
    </IconButton>
  );
});

/** A modal (Cmd+K, the navigation drawer, any confirm) owns the keyboard while it is open. */
function isModalOpen(): boolean {
  return document.querySelector('.rt-BaseDialogOverlay, [aria-modal="true"]') !== null;
}

/**
 * Ctrl+\ (⌘\ on macOS) hides and shows the sidebar. One listener, held by the app shell's
 * instance, which is always mounted. Not in the drawer layout and not in workspace settings,
 * where the sidebar does not collapse.
 */
function useSidebarShortcut(enabled: boolean): void {
  const isDrawerLayout = useIsBelowDesktop();
  const pathname = usePathname() ?? '';
  const inWorkspace = pathname.startsWith('/workspace');
  useEffect(() => {
    if (!enabled || isDrawerLayout || inWorkspace) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.repeat || e.shiftKey || e.altKey || !isCommandKey(e)) return;
      if (e.key !== '\\' && e.code !== 'Backslash') return;
      if (isModalOpen()) return;
      e.preventDefault();
      const { isNavCollapsed, setNavCollapsed } = useSidebarWidthStore.getState();
      const active = document.activeElement;
      if (isNavCollapsed) {
        const onShowControl = active instanceof HTMLElement && active.dataset.sidebarToggle === 'show';
        setNavCollapsed(false);
        // Focus stays where it was, unless it was on Show sidebar, which goes away.
        if (onShowControl) focusSidebarToggle('hide');
      } else {
        const inRail = active instanceof Element && active.closest('[data-ph-sidebar-slot]') !== null;
        setNavCollapsed(true);
        // A hidden rail cannot hold focus: it moves to Show sidebar.
        if (inRail) focusSidebarToggle('show');
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [enabled, isDrawerLayout, inWorkspace]);
}

/**
 * Control to restore a collapsed nav sidebar on desktop. In the drawer layout an `inline` one
 * opens the drawer instead, from the page's header row; the other placements render nothing
 * there and leave it to the app shell's own control.
 */
export function SidebarExpandButton({ placement = 'absolute' }: SidebarExpandButtonProps) {
  const { t } = useTranslation();
  const navHidden = useNavHidden();
  const setNavCollapsed = useSidebarWidthStore((s) => s.setNavCollapsed);
  const pageExpandControls = useSidebarWidthStore((s) => s.pageExpandControls);
  const registerPageExpandControl = useSidebarWidthStore((s) => s.registerPageExpandControl);
  const unregisterPageExpandControl = useSidebarWidthStore((s) => s.unregisterPageExpandControl);
  const shortcut = useMemo(() => `${getModifierSymbol()} + \\`, []);
  const isDrawerLayout = useIsBelowDesktop();
  const hasDrawer = useMobileSidebarStore((s) => s.registeredCount > 0);

  const isPageLevel = placement !== 'shell';
  useSidebarShortcut(placement === 'shell');
  // Before paint, so the shell never draws its own control for a frame on a page that has one.
  useLayoutEffect(() => {
    if (!isPageLevel) return;
    registerPageExpandControl();
    return unregisterPageExpandControl;
  }, [isPageLevel, registerPageExpandControl, unregisterPageExpandControl]);

  if (isDrawerLayout) return placement === 'inline' && hasDrawer ? <OpenSidebarButton /> : null;
  if (!navHidden) return null;
  if (placement === 'shell' && pageExpandControls > 0) return null;

  const button = (
    <Tooltip content={t('sidebar.showWithShortcut', { shortcut })} side="right">
      <IconButton
        data-sidebar-toggle="show"
        className="touch-hit"
        variant="ghost"
        color="gray"
        size="1"
        aria-label={t('sidebar.show')}
        onClick={() => {
          setNavCollapsed(false);
          focusSidebarToggle('hide');
        }}
        style={TOGGLE_STYLE}
      >
        {toggleGlyph}
      </IconButton>
    </Tooltip>
  );

  if (placement === 'inline') return button;
  if (placement === 'shell') {
    return <Box style={{ position: 'fixed', ...FLOATING_POSITION, zIndex: 100 }}>{button}</Box>;
  }
  return <Box style={{ position: 'absolute', ...FLOATING_POSITION, zIndex: 25 }}>{button}</Box>;
}
