'use client';

import { useState } from 'react';
import { Link } from '@/lib/navigation';
import { ELEMENT_HEIGHT } from '@/app/components/sidebar';
import { SIDEBAR_FOCUS_CLASS } from '@/app/components/sidebar/sidebar-focus-style';

interface WorkspaceSidebarItemProps {
  /** Optional left icon (MaterialIcon, Image, any ReactNode) */
  icon?: React.ReactNode;
  /** Label text displayed in the item */
  label: string;
  /** Optional content rendered on the right side */
  rightSlot?: React.ReactNode;
  /** Click handler */
  onClick?: () => void;
  /** Navigation href — renders as <a> when provided */
  href?: string;
  /** Whether this item is currently selected/active */
  isActive?: boolean;
  /** Left padding override (used for indented sub-items) */
  paddingLeft?: number;
}

/**
 * Workspace sidebar item.
 *
 * Selected (the page you are on): the muted fill, a 1px default border, and the label in the primary text colour at
 * medium weight. Hover and press: the subtle fill, one step lighter, with no border, so a hovered row never looks like
 * the selected one. Keyboard focus is the shared sidebar focus ring. Same recipe as the chat sidebar's rows.
 */
export function WorkspaceSidebarItem({
  icon,
  label,
  rightSlot,
  onClick,
  href,
  isActive = false,
  paddingLeft = 12,
}: WorkspaceSidebarItemProps) {
  const [isHovered, setIsHovered] = useState(false);

  const sharedStyle: React.CSSProperties = {
    appearance: 'none',
    margin: 0,
    font: 'inherit',
    color: 'inherit',
    textDecoration: 'none',
    display: 'flex',
    alignItems: 'center',
    gap: 'var(--space-2)',
    width: '100%',
    height: ELEMENT_HEIGHT,
    paddingLeft,
    paddingRight: 'var(--space-3)',
    boxSizing: 'border-box',
    flexShrink: 0,
    borderRadius: 'var(--radius-1)',
    backgroundColor: isActive ? 'var(--fill-muted)' : isHovered ? 'var(--surface-subtle)' : 'transparent',
    border: isActive ? '1px solid var(--border-default)' : '1px solid transparent',
    transition: 'background-color var(--motion-micro) var(--motion-ease)',
    cursor: (onClick || href) ? 'pointer' : 'default',
  };

  const hoverHandlers = {
    onMouseEnter: () => setIsHovered(true),
    onMouseLeave: () => setIsHovered(false),
  };

  const content = (
    <>
      {icon}
      <span
        style={{
          flex: 1,
          fontSize: 14,
          fontWeight: isActive ? 500 : 400,
          lineHeight: '20px',
          color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          textAlign: 'left',
        }}
      >
        {label}
      </span>
      {rightSlot}
    </>
  );

  if (href) {
    const handleLinkClick = (e: React.MouseEvent) => {
      if (e.metaKey || e.ctrlKey || e.shiftKey) return;
      onClick?.();
    };

    return (
      <Link
        href={href}
        onClick={handleLinkClick}
        aria-current={isActive ? 'page' : undefined}
        className={SIDEBAR_FOCUS_CLASS}
        style={sharedStyle}
        {...hoverHandlers}
      >
        {content}
      </Link>
    );
  }

  return (
    <button type="button" onClick={onClick} className={SIDEBAR_FOCUS_CLASS} style={sharedStyle} {...hoverHandlers}>
      {content}
    </button>
  );
}
