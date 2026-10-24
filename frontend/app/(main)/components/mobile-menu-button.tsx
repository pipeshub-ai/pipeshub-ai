'use client';

import { useEffect } from 'react';
import { usePathname } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Box, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { touchTarget, TOUCH_TARGET_PX } from '@/app/components/ui/responsive';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';

/**
 * Fixed top-left hamburger for phones. Shown only while a sidebar that can open
 * as a drawer is mounted, so routes without one never get a dead button.
 * Also closes the drawer on every route change, including browser Back.
 */
export function MobileMenuButton() {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const hasMobileSidebar = useMobileSidebarStore((s) => s.registeredCount > 0);
  const open = useMobileSidebarStore((s) => s.open);
  const close = useMobileSidebarStore((s) => s.close);
  const pathname = usePathname();

  useEffect(() => close(), [pathname, close]);

  if (!isMobile || !hasMobileSidebar) return null;

  return (
    <Box
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        height: TOUCH_TARGET_PX,
        display: 'flex',
        alignItems: 'center',
        zIndex: 100,
      }}
    >
      <IconButton
        variant="ghost"
        color="gray"
        size="2"
        onClick={open}
        style={{ margin: 0, ...touchTarget(true) }}
        aria-label={t('sidebar.open')}
      >
        <MaterialIcon name="menu" size={22} color="var(--gray-11)" />
      </IconButton>
    </Box>
  );
}
