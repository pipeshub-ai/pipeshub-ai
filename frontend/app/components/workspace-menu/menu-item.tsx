'use client';

import { Link } from '@/lib/navigation';
import { Button, Flex, Separator, Text } from '@radix-ui/themes';
import { ITEM_HEIGHT } from './types';
import { SIDEBAR_FOCUS_CLASS, SidebarFocusStyle } from '@/app/components/sidebar/sidebar-focus-style';

// ============================================
// Divider
// ============================================

/** The rule between menu sections: a Radix separator, so it is announced as one. */
export function Divider() {
  return <Separator size="4" decorative={false} style={{ flexShrink: 0 }} />;
}

// ============================================
// MenuItem
// ============================================

interface MenuItemProps {
  icon?: React.ReactNode;
  label: React.ReactNode;
  rightSlot?: React.ReactNode;
  onClick?: () => void;
  href?: string;
  textColor?: string;
  /** Highlighted state (e.g. when a sub-panel is open) */
  isActive?: boolean;
  /** Paints the row as destructive (Log out). */
  destructive?: boolean;
  /** For a row that opens a sub-panel: whether that panel is open. */
  expanded?: boolean;
  /** For a row that opens a sub-panel: that panel's title, so focus can come back here when it closes. */
  opensPanel?: string;
}

/**
 * Single row in the workspace menu: a Radix ghost Button the menu's width,
 * label left and any trailing mark right. Links render the same Button as an
 * anchor, so every row has the same hover, press and focus ring.
 */
export function MenuItem({
  icon,
  label,
  rightSlot,
  onClick,
  href,
  textColor,
  isActive = false,
  destructive = false,
  expanded,
  opensPanel,
}: MenuItemProps) {
  const isExternal = href?.startsWith('http');
  const labelColor = destructive
    ? 'var(--red-11)'
    : isActive
      ? 'var(--text-primary)'
      : textColor ?? 'var(--text-secondary)';

  const content = (
    <>
      <Flex as="span" align="center" gap="2" style={{ flex: 1, minWidth: 0 }}>
        {icon}
        <Text
          as="span"
          size="2"
          weight="regular"
          style={{
            flex: 1,
            minWidth: 0,
            color: labelColor,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            textAlign: 'left',
          }}
        >
          {label}
        </Text>
      </Flex>
      {rightSlot}
    </>
  );

  const buttonProps = {
    variant: 'ghost' as const,
    color: destructive ? ('red' as const) : ('gray' as const),
    size: '2' as const,
    // One ring for every row: Radix would paint Log out's ring red and the rest gray.
    className: SIDEBAR_FOCUS_CLASS,
    'data-active': isActive ? 'true' : undefined,
    'data-opens-panel': opensPanel,
    style: {
      margin: 0,
      width: '100%',
      // Radix sizes a ghost button content-box (height plus its vertical padding);
      // border-box keeps the row at the menu's 32px.
      boxSizing: 'border-box',
      height: ITEM_HEIGHT,
      paddingInline: 'var(--space-3)',
      justifyContent: 'space-between',
      gap: 'var(--space-2)',
      borderRadius: 'var(--radius-1)',
      flexShrink: 0,
      backgroundColor: isActive ? 'var(--fill-ghost-hover)' : undefined,
    } satisfies React.CSSProperties,
  };

  if (href && isExternal) {
    return (
      <>
        <SidebarFocusStyle />
        <Button asChild {...buttonProps}>
          <a href={href} target="_blank" rel="noopener noreferrer" onClick={onClick}>
            {content}
          </a>
        </Button>
      </>
    );
  }

  if (href) {
    const handleLinkClick = (e: React.MouseEvent) => {
      if (e.metaKey || e.ctrlKey || e.shiftKey) return;
      onClick?.();
    };

    return (
      <>
        <SidebarFocusStyle />
        <Button asChild {...buttonProps}>
          <Link href={href} onClick={handleLinkClick}>
            {content}
          </Link>
        </Button>
      </>
    );
  }

  return (
    <>
      <SidebarFocusStyle />
      <Button type="button" onClick={onClick} aria-expanded={expanded} {...buttonProps}>
        {content}
      </Button>
    </>
  );
}
