'use client';

import { useEffect } from 'react';
import { usePathname } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Box, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { touchTarget, TOUCH_TARGET_PX } from '@/app/components/ui/responsive';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { MOBILE_NAV_ID } from '@/app/components/sidebar/constants';
import { useNeedsYouCount } from '@/lib/harness-jobs/inbox-store';

/**
 * Fixed top-left hamburger for phones. Shown only while a sidebar that can open
 * as a drawer is mounted, so routes without one never get a dead button.
 * Also closes the drawer on every route change, including browser Back.
 */
export function MobileMenuButton() {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const hasMobileSidebar = useMobileSidebarStore((s) => s.registeredCount > 0);
  const isOpen = useMobileSidebarStore((s) => s.isOpen);
  const open = useMobileSidebarStore((s) => s.open);
  const close = useMobileSidebarStore((s) => s.close);
  const pathname = usePathname();
  // Zero whenever the harness is off: the count is only read with it on.
  const waiting = useNeedsYouCount();

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
      {/* Its own surface: page content scrolls underneath, and a bare icon tangled with it. */}
      <IconButton
        variant="ghost"
        color="gray"
        size="2"
        onClick={open}
        style={{
          margin: 0,
          position: 'relative',
          ...touchTarget(true),
          backgroundColor: 'var(--color-background)',
          borderRadius: '0 0 var(--radius-3) 0',
          boxShadow: '0 1px 4px rgba(0, 0, 0, 0.12)',
        }}
        aria-label={waiting > 0 ? `${t('sidebar.open')}, ${t('needsYou.badgeAria', { count: waiting })}` : t('sidebar.open')}
        aria-expanded={isOpen}
        aria-controls={MOBILE_NAV_ID}
      >
        <MaterialIcon name="menu" size={22} color="var(--gray-11)" />
        {waiting > 0 && (
          <span
            aria-hidden
            data-testid="menu-needs-you-dot"
            style={{
              position: 'absolute',
              top: 9,
              right: 9,
              width: 9,
              height: 9,
              borderRadius: '50%',
              backgroundColor: 'var(--amber-9)',
              boxShadow: '0 0 0 2px var(--color-background)',
              pointerEvents: 'none',
            }}
          />
        )}
      </IconButton>
    </Box>
  );
}
