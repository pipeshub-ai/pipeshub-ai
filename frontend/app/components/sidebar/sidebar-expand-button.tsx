'use client';

import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, IconButton, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useSidebarWidthStore } from '@/lib/store/sidebar-width-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';

interface SidebarExpandButtonProps {
  /**
   * `absolute` (default) pins to the top-left of the nearest `position: relative` page
   * container; `inline` renders just the button for placement inside a page header row;
   * `shell` is the app-layout fallback — fixed top-left, shown only when no page-level
   * (`absolute` / `inline`) control is mounted.
   */
  placement?: 'absolute' | 'inline' | 'shell';
}

/**
 * Desktop-only control to restore a collapsed nav sidebar. Renders nothing when the sidebar
 * is visible or on mobile (which has its own hamburger in the app shell).
 */
export function SidebarExpandButton({ placement = 'absolute' }: SidebarExpandButtonProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const isNavCollapsed = useSidebarWidthStore((s) => s.isNavCollapsed);
  const setNavCollapsed = useSidebarWidthStore((s) => s.setNavCollapsed);
  const pageExpandControls = useSidebarWidthStore((s) => s.pageExpandControls);
  const registerPageExpandControl = useSidebarWidthStore((s) => s.registerPageExpandControl);
  const unregisterPageExpandControl = useSidebarWidthStore((s) => s.unregisterPageExpandControl);

  const isPageLevel = placement !== 'shell';
  useEffect(() => {
    if (!isPageLevel) return;
    registerPageExpandControl();
    return unregisterPageExpandControl;
  }, [isPageLevel, registerPageExpandControl, unregisterPageExpandControl]);

  if (isMobile || !isNavCollapsed) return null;
  if (placement === 'shell' && pageExpandControls > 0) return null;

  const button = (
    <Tooltip content={t('sidebar.expand')} side="right">
      <IconButton
        variant="ghost"
        color="gray"
        size="2"
        aria-label={t('sidebar.expand')}
        onClick={() => setNavCollapsed(false)}
        style={{ margin: 0, flexShrink: 0 }}
      >
        <MaterialIcon name="menu" size={20} color="var(--gray-11)" />
      </IconButton>
    </Tooltip>
  );

  if (placement === 'inline') return button;
  if (placement === 'shell') {
    return (
      <Box style={{ position: 'fixed', top: 10, left: 12, zIndex: 100 }}>{button}</Box>
    );
  }
  return <Box style={{ position: 'absolute', top: 10, left: 12, zIndex: 25 }}>{button}</Box>;
}
