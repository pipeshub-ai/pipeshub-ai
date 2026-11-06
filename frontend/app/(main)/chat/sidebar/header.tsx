'use client';

import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import { Badge, Flex, IconButton, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useNotificationStore } from '@/app/(main)/notifications/store';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { HEADER_ELEMENT_SIZE } from '@/app/components/sidebar';
import { useSidebarWidthStore } from '@/lib/store/sidebar-width-store';
import { useIsBelowDesktop, useIsMobile } from '@/lib/hooks/use-is-mobile';
import { Link } from '@/lib/navigation';
import { getModifierSymbol } from '@/lib/utils/platform';
import { focusSidebarToggle } from '@/app/components/sidebar/sidebar-toggle-focus';
import { BrandMark } from '@/config';
import { useChatStore } from '@/chat/store';

/** The 24px square of a header control: Radix's size 1 box with the ghost's negative margin cleared. */
const HEADER_CONTROL_STYLE = { width: 'var(--space-5)', height: 'var(--space-5)', padding: 0, margin: 0 } as const;

/** A count you check rather than a place you browse to, so it rides the bar with its number on the glyph. */
function InboxButton() {
  const { t } = useTranslation();
  const unreadCount = useNotificationStore((s) => s.unreadCount);
  const togglePanel = useNotificationStore((s) => s.togglePanel);
  // The drawer layout: Inbox replaces the drawer, so the drawer closes first.
  const isDrawerLayout = useIsBelowDesktop();
  const closeMobileSidebar = useMobileSidebarStore((s) => s.close);
  const label = unreadCount > 9 ? '9+' : String(unreadCount);
  return (
    <Tooltip content={t('nav.inboxTooltip')} side="bottom">
      <IconButton
        data-ph-notifications-trigger=""
        className="touch-hit"
        variant="ghost"
        color="gray"
        size="1"
        aria-label={unreadCount > 0 ? t('nav.inboxUnread', { count: unreadCount }) : t('nav.inbox')}
        onClick={() => {
          if (isDrawerLayout) closeMobileSidebar();
          togglePanel();
        }}
        style={{ ...HEADER_CONTROL_STYLE, position: 'relative', overflow: 'visible' }}
      >
        <MaterialIcon name="inbox" size={16} color="var(--text-secondary)" />
        {unreadCount > 0 && (
          <Badge
            aria-hidden
            size="1"
            variant="solid"
            color="red"
            radius="full"
            style={{
              // A dot with a number that rides the glyph: Radix's size 1 scaled down to 14px and a 9px count.
              position: 'absolute',
              top: 'calc(var(--space-1) * -0.75)',
              right: 'calc(var(--space-1) * -0.5)',
              minWidth: 'calc(var(--space-4) * 0.875)',
              height: 'calc(var(--space-4) * 0.875)',
              paddingInline: 'calc(var(--space-1) * 0.75)',
              justifyContent: 'center',
              fontSize: 'calc(var(--font-size-1) * 0.75)',
              lineHeight: 1,
              // A ring in the rail's own ground, so the badge sits on the glyph rather than merging with it.
              boxShadow: '0 0 0 2px var(--surface-raised)',
              pointerEvents: 'none',
            }}
          >
            {label}
          </Badge>
        )}
      </IconButton>
    </Tooltip>
  );
}

/**
 * Inbox and, outside the drawer layout, the Hide sidebar toggle. Every section's sidebar shows
 * them, so notifications and a wider page are one tap away from anywhere.
 */
export function SidebarHeaderActions() {
  const { t } = useTranslation();
  const setNavCollapsed = useSidebarWidthStore((s) => s.setNavCollapsed);
  const isDrawerLayout = useIsBelowDesktop();
  const shortcut = useMemo(() => `${getModifierSymbol()} + \\`, []);

  return (
    <Flex align="center" gap="1">
      <InboxButton />
      {!isDrawerLayout && (
        <Tooltip content={t('sidebar.hideWithShortcut', { shortcut })} side="right">
          <IconButton
            data-sidebar-toggle="hide"
            className="touch-hit"
            variant="ghost"
            color="gray"
            size="1"
            aria-label={t('sidebar.hide')}
            onClick={() => {
              setNavCollapsed(true);
              focusSidebarToggle('show');
            }}
            style={HEADER_CONTROL_STYLE}
          >
            <MaterialIcon name="left_panel_close" size={16} color="var(--text-secondary)" />
          </IconButton>
        </Tooltip>
      )}
    </Flex>
  );
}

/**
 * Sidebar header: the logo, then Inbox and, on desktop, the collapse button. Your profile and
 * account live in the workspace menu at the foot of the rail, so the header carries no avatar.
 * When the sidebar is collapsed the header is not visible (sidebar is 0-wide),
 * so we only need to handle the expanded state here.
 */
export function ChatSidebarHeader() {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const closeMobileSidebar = useMobileSidebarStore((s) => s.close);
  return (
    <Flex
      align="center"
      justify="between"
      gap="2"
      style={{ height: '100%', paddingInline: 'calc(var(--space-4) + var(--space-1))' }}
    >
      <Link
        href="/chat/"
        onClick={(e) => {
          // A modified click opens Home elsewhere; this tab keeps its search.
          if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
          if (isMobile) closeMobileSidebar();
          // Search results share /chat/ with the new-chat page, so the link alone would leave them open.
          const chat = useChatStore.getState();
          if (chat.settings.mode === 'search') {
            chat.setMode('chat');
            chat.clearSearchResults();
          }
        }}
        className="touch-hit"
        aria-label={t('nav.home')}
        style={{ lineHeight: 0, display: 'inline-flex' }}
      >
        <BrandMark size={HEADER_ELEMENT_SIZE} color="var(--accent-11)" />
      </Link>
      <SidebarHeaderActions />
    </Flex>
  );
}
