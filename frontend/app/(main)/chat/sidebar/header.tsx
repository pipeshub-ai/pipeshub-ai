'use client';

import { useTranslation } from 'react-i18next';

import { Flex, IconButton, Text, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useNotificationStore } from '@/app/(main)/notifications/store';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { Link } from '@/lib/navigation';
import { HEADER_ELEMENT_SIZE } from '@/app/components/sidebar';
import { UserAvatar } from '@/app/components/ui/user-avatar';
import { useUserStore } from '@/lib/store/user-store';
import { useSidebarWidthStore } from '@/lib/store/sidebar-width-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { BrandMark } from '@/config';

/** A count you check rather than a place you browse to, so it rides the bar with its number on the glyph. */
function InboxButton() {
  const { t } = useTranslation();
  const unreadCount = useNotificationStore((s) => s.unreadCount);
  const togglePanel = useNotificationStore((s) => s.togglePanel);
  const isMobile = useIsMobile();
  const closeMobileSidebar = useMobileSidebarStore((s) => s.close);
  const label = unreadCount > 9 ? '9+' : String(unreadCount);
  return (
    <Tooltip content={t('nav.inbox')} side="bottom">
      <IconButton
        data-ph-notifications-trigger=""
        className="touch-hit"
        variant="ghost"
        color="gray"
        size="1"
        aria-label={unreadCount > 0 ? `${t('nav.inbox')}, ${unreadCount}` : t('nav.inbox')}
        onClick={() => {
          if (isMobile) closeMobileSidebar();
          togglePanel();
        }}
        style={{ position: 'relative', width: 24, height: 24, padding: 0, margin: 0, cursor: 'pointer', overflow: 'visible' }}
      >
        <MaterialIcon name="inbox" size={16} color="var(--neutral-11)" />
        {unreadCount > 0 && (
          <Flex
            align="center"
            justify="center"
            aria-hidden
            style={{
              position: 'absolute',
              top: -3,
              right: -2,
              minWidth: 14,
              height: 14,
              paddingInline: 3,
              borderRadius: 999,
              backgroundColor: 'var(--red-9)',
              // A ring in the rail's own ground so the badge sits on the glyph rather than merging with it.
              boxShadow: '0 0 0 2px var(--olive-1)',
              boxSizing: 'border-box',
              pointerEvents: 'none',
            }}
          >
            <Text style={{ fontSize: 9, lineHeight: 1, fontWeight: 500, color: 'var(--red-contrast)' }}>{label}</Text>
          </Flex>
        )}
      </IconButton>
    </Tooltip>
  );
}

/**
 * Inbox and, on desktop, the collapse toggle. Every section's sidebar shows
 * them, so notifications and a wider page are one tap away from anywhere.
 */
export function SidebarHeaderActions() {
  const { t } = useTranslation();
  const setNavCollapsed = useSidebarWidthStore((s) => s.setNavCollapsed);
  const isMobile = useIsMobile();

  return (
    <Flex align="center" gap="1">
      <InboxButton />
      {!isMobile && (
        <Tooltip content={t('sidebar.collapse')} side="right">
          <IconButton
            className="touch-hit"
            variant="ghost"
            color="gray"
            size="1"
            aria-label={t('sidebar.collapse')}
            onClick={() => setNavCollapsed(true)}
            style={{ width: 24, height: 24, padding: 0, margin: 0, cursor: 'pointer' }}
          >
            <MaterialIcon name="left_panel_close" size={16} color="var(--neutral-11)" />
          </IconButton>
        </Tooltip>
      )}
    </Flex>
  );
}

/**
 * Sidebar header — logo, user avatar, inbox and a desktop collapse button.
 * When the sidebar is collapsed the header is not visible (sidebar is 0-wide),
 * so we only need to handle the expanded state here.
 */
export function ChatSidebarHeader() {
  const { t } = useTranslation();
  const profile = useUserStore((s) => s.profile);

  const avatar = (
    <UserAvatar
      fullName={profile?.fullName}
      firstName={profile?.firstName}
      lastName={profile?.lastName}
      email={profile?.email}
      src={profile?.avatarUrl}
      size={HEADER_ELEMENT_SIZE}
      radius="small"
    />
  );

  return (
    <Flex align="center" justify="between" gap="2" style={{ height: '100%', padding: 'var(--space-4)' }}>
      <BrandMark size={HEADER_ELEMENT_SIZE} color="var(--accent-11)" />
      <Flex align="center" gap="1">
        <Link href="/workspace/profile/" className="touch-hit" aria-label={t('common.openProfile')} style={{ textDecoration: 'none', lineHeight: 0, display: 'inline-flex' }}>
          {avatar}
        </Link>
        <SidebarHeaderActions />
      </Flex>
    </Flex>
  );
}
