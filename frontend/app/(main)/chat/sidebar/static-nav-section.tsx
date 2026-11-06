'use client';

import { useEffect, useMemo, useState, type CSSProperties, type MouseEvent, type ReactNode } from 'react';
import { Badge, Box, Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZE_DEFAULT, KbdChip } from '@/app/components/sidebar';
import { SIDEBAR_ROW_TINT_CLASS } from '@/app/components/sidebar/sidebar-focus-style';
import { useCommandStore } from '@/lib/store/command-store';
import { useTranslation } from 'react-i18next';
import { getModifierSymbol } from '@/lib/utils/platform';
import { useIsBelowDesktop } from '@/lib/hooks/use-is-mobile';
import { Link } from '@/lib/navigation';
import { useCanHover } from '@/lib/hooks/use-can-hover';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { useNotificationStore } from '@/app/(main)/notifications/store';
import { useFeatureFlagsStore, selectProjectsEnabled } from '@/lib/store/feature-flags-store';
import { usePathname, useRouter } from 'next/navigation';
import { readRailMemory, writeRailMemory } from './rail-memory';

// ========================================
// Navigation item config
// ========================================

interface NavItem {
  icon: string;
  labelKey: string;
  route: string;
  beta?: boolean;
}

/** Primary navigation items — labels resolved via i18n */
const MAIN_NAV_ITEMS: NavItem[] = [
  // { icon: 'search', labelKey: 'nav.searchChats', route: '/search' },
  { icon: 'folder', labelKey: 'nav.collections', route: '/knowledge-base/' },
  { icon: 'inventory_2', labelKey: 'nav.allRecords', route: '/knowledge-base/?view=all-records' },
  { icon: 'attachment', labelKey: 'nav.allArtifacts', route: '/artifacts/', beta: true },
  { icon: 'smart_toy', labelKey: 'nav.agents', route: '/agents/' },
];

/** Projects nav item — gated behind `ENABLE_PROJECTS`, rendered separately below. */
const PROJECTS_NAV_ITEM: NavItem = { icon: 'files', labelKey: 'nav.projects', route: '/projects/' };

// ========================================
// Components
// ========================================

/** Inline beta chip so the row stays one link, matching Projects. */
function BetaNavLabel({ text }: { text: string }) {
  const { t } = useTranslation();
  return (
    <Flex align="center" gap="2" style={{ minWidth: 0, width: '100%' }}>
      <span
        style={{
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {text}
      </span>
      <Badge size="1" color="amber" variant="soft" style={{ flexShrink: 0 }}>
        {t('nav.beta', { defaultValue: 'Beta' })}
      </Badge>
    </Flex>
  );
}

/** The geometry every nav row shares: a ghost Button stretched to the rail, its negative ghost margin cleared, 32px tall. */
const NAV_ROW_STYLE: CSSProperties = {
  width: '100%',
  height: 'var(--space-6)',
  margin: 0,
  paddingInline: 'var(--space-3)',
  gap: 'var(--space-2)',
  justifyContent: 'space-between',
  borderRadius: 'var(--radius-1)',
  boxSizing: 'border-box',
};

interface NavRowProps {
  icon: string;
  label: ReactNode;
  /** A route: the row is a link. Otherwise it is a button. */
  href?: string;
  onClick?: () => void;
  /** New chat: the accent row. */
  accent?: boolean;
  /** This row is the page you are on. */
  selected?: boolean;
  rightSlot?: ReactNode;
}

/**
 * One row of the navigation stack: a Radix ghost Button (a link for a route), so hover,
 * press and the focus ring come from Radix. Label and glyph are the secondary text role;
 * the row you are on takes the muted fill and the primary text.
 */
function NavRow({ icon, label, href, onClick, accent = false, selected = false, rightSlot }: NavRowProps) {
  const color = accent ? 'var(--accent-a11)' : selected ? 'var(--text-primary)' : 'var(--text-secondary)';
  const weight = accent || selected ? 'medium' : 'regular';
  const labelStyle: CSSProperties = {
    flex: 1,
    minWidth: 0,
    textAlign: 'left',
    color,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  };
  const inner = (
    <>
      <Flex align="center" gap="2" style={{ flex: 1, minWidth: 0 }}>
        <MaterialIcon name={icon} size={ICON_SIZE_DEFAULT} color={color} />
        {typeof label === 'string' ? (
          <Text size="2" weight={weight} style={labelStyle}>
            {label}
          </Text>
        ) : (
          <Box
            style={{
              ...labelStyle,
              fontSize: 'var(--font-size-2)',
              lineHeight: 'var(--line-height-2)',
              fontWeight: `var(--font-weight-${weight})`,
            }}
          >
            {label}
          </Box>
        )}
      </Flex>
      {rightSlot}
    </>
  );
  const style: CSSProperties = {
    ...NAV_ROW_STYLE,
    // Selected is the chat rows' look: the muted fill and a 1px default border (hover is lighter, see the class).
    border: `1px solid ${selected ? 'var(--border-default)' : 'transparent'}`,
    ...(selected ? { backgroundColor: 'var(--fill-muted)' } : {}),
  };

  if (href) {
    const handleLinkClick = (e: MouseEvent) => {
      if (e.metaKey || e.ctrlKey || e.shiftKey) return;
      onClick?.();
    };
    return (
      <Button
        asChild
        variant="ghost"
        color={accent ? undefined : 'gray'}
        size="2"
        className={SIDEBAR_ROW_TINT_CLASS}
        style={style}
      >
        <Link href={href} onClick={handleLinkClick} aria-current={selected ? 'page' : undefined}>
          {inner}
        </Link>
      </Button>
    );
  }
  return (
    <Button
      type="button"
      variant="ghost"
      color={accent ? undefined : 'gray'}
      size="2"
      className={SIDEBAR_ROW_TINT_CLASS}
      onClick={onClick}
      aria-current={selected ? 'page' : undefined}
      style={style}
    >
      {inner}
    </Button>
  );
}

/**
 * Static navigation section — "New Chat" button, Search, Collections, etc.
 */
export function StaticNavSection() {
  const dispatch = useCommandStore((s) => s.dispatch);
  const { t } = useTranslation();
  const modKey = useMemo(() => getModifierSymbol(), []);
  // The drawer layout: following a row closes the drawer.
  const isMobile = useIsBelowDesktop();
  const canHover = useCanHover();
  const closeMobileSidebar = useMobileSidebarStore((s) => s.close);
  // Until the flags are read, the row shows as it did last time, so the rows under it do not move when they arrive.
  const projectsFlag = useFeatureFlagsStore((s) => (s.flags === null ? null : selectProjectsEnabled(s)));
  const [projectsLastTime] = useState(() => readRailMemory('projects') === 1);
  const projectsEnabled = projectsFlag ?? projectsLastTime;
  useEffect(() => {
    if (projectsFlag !== null) writeRailMemory('projects', projectsFlag ? 1 : 0);
  }, [projectsFlag]);
  const pathname = usePathname();
  const router = useRouter();

  // Covers links whose pathname does not change (All Records only adds a query), which the shell's route watcher misses.
  const closeOnMobile = isMobile ? closeMobileSidebar : undefined;

  const handleNewChat = () => {
    if (isMobile) closeMobileSidebar();
    useNotificationStore.getState().closePanel();
    if (!dispatch('newChat')) router.push('/chat/');
  };

  const handleOpenSearch = () => {
    if (isMobile) closeMobileSidebar();
    if (!dispatch('openCommandPalette')) router.push('/chat/');
  };

  return (
    <Flex direction="column" gap="1">
      <NavRow icon="assistant" label={t('chat.newChat')} onClick={handleNewChat} accent />
      {/* Search chats opens the command palette (⌘ + K) */}
      <NavRow
        icon="search"
        label={t('nav.searchChats')}
        onClick={handleOpenSearch}
        rightSlot={canHover ? <KbdChip keys={[modKey, 'K']} /> : undefined}
      />

      {projectsEnabled && (
        <NavRow
          icon={PROJECTS_NAV_ITEM.icon}
          label={<BetaNavLabel text={t(PROJECTS_NAV_ITEM.labelKey)} />}
          href={PROJECTS_NAV_ITEM.route}
          onClick={closeOnMobile}
        />
      )}
      {MAIN_NAV_ITEMS.map((item) => (
        <NavRow
          key={item.route}
          icon={item.icon}
          label={item.beta ? <BetaNavLabel text={t(item.labelKey)} /> : t(item.labelKey)}
          href={item.route}
          onClick={closeOnMobile}
          selected={
            (item.route.startsWith('/artifacts') && pathname.startsWith('/artifacts')) ||
            // Only the index itself: the builder and an agent's chat are other pages.
            (item.route === '/agents/' && pathname.replace(/\/$/, '') === '/agents')
          }
        />
      ))}
    </Flex>
  );
}
