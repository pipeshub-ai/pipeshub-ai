'use client';

import { useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { Flex } from '@radix-ui/themes';
import { ChatStarIcon } from '@/app/components/ui/chat-star-icon';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { KBD_BADGE_PADDING, ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { useCommandStore } from '@/lib/store/command-store';
import { useTranslation } from 'react-i18next';
import { getModifierSymbol } from '@/lib/utils/platform';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { SidebarItem } from './sidebar-item';

// ========================================
// Navigation item config
// ========================================

interface NavItem {
  icon: string;
  labelKey: string;
  route: string;
}

/** Primary navigation items — labels resolved via i18n */
const MAIN_NAV_ITEMS: NavItem[] = [
  // { icon: 'search', labelKey: 'nav.searchChats', route: '/search' },
  { icon: 'folder', labelKey: 'nav.collections', route: '/knowledge-base' },
  { icon: 'inventory_2', labelKey: 'nav.allRecords', route: '/knowledge-base?view=all-records' },
];

// ========================================
// Components
// ========================================

/** Keyboard shortcut badge */
const KbdBadge = ({ children }: { children: React.ReactNode }) => (
  <span
    style={{
      background: 'var(--slate-1)',
      border: '1px solid var(--slate-3)',
      padding: KBD_BADGE_PADDING,
      borderRadius: 'var(--radius-2)',
      fontSize: 12,
      lineHeight: 'var(--line-height-1)',
      letterSpacing: '0.04px',
      color: 'var(--slate-12)',
      fontWeight: 400,
    }}
  >
    {children}
  </span>
);

/**
 * Static navigation section — "New Chat" button, Search, Collections, etc.
 */
export function StaticNavSection() {
  const router = useRouter();
  const dispatch = useCommandStore((s) => s.dispatch);
  const { t } = useTranslation();
  const modKey = useMemo(() => getModifierSymbol(), []);
  const isMobile = useIsMobile();
  const closeMobileSidebar = useMobileSidebarStore((s) => s.close);

  const handleNewChat = () => {
    if (isMobile) closeMobileSidebar();
    dispatch('newChat');
  };

  const handleOpenSearch = () => {
    dispatch('openCommandPalette');
  };

  return (
    <Flex direction="column" gap="1">
      {/* New Chat */}
      <SidebarItem
        icon={
          <ChatStarIcon
            size={ICON_SIZE_DEFAULT}
            color="var(--accent-11)"
          />
        }
        label={t('chat.newChat')}
        onClick={handleNewChat}
        textColor="var(--accent-11)"
        fontWeight={500}
      />
      {/* Search Chats — opens command palette (⌘+K) */}
        <SidebarItem
          icon={<MaterialIcon name={'search'} size={ICON_SIZE_DEFAULT} />}
          label={t('nav.searchChats')}
          onClick={handleOpenSearch}
          rightSlot={<KbdBadge>{modKey} +K</KbdBadge>}
        />

      {/* Navigation items — hidden on mobile */}
      {!isMobile &&
        MAIN_NAV_ITEMS.map((item) => (
          <SidebarItem
            key={item.route}
            icon={<MaterialIcon name={item.icon} size={ICON_SIZE_DEFAULT} />}
            label={t(item.labelKey)}
            onClick={() => router.push(item.route)}
          />
        ))}
    </Flex>
  );
}
