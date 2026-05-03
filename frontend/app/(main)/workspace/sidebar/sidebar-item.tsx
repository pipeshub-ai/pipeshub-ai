'use client';

import { useState } from 'react';
import Link from 'next/link';
import { ELEMENT_HEIGHT } from '@/app/components/sidebar';

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
 * Workspace sidebar item — matches the chat SidebarItem styling exactly.
 *
 * Hover/active: olive-3 background + olive-4 border
 * Default: transparent background, transparent border
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
  const highlighted = isActive || isHovered;

  const sharedStyle: React.CSSProperties = {
    appearance: 'none',
    margin: 0,
    font: 'inherit',
    color: 'inherit',
    outline: 'none',
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
    backgroundColor: highlighted ? 'var(--olive-3)' : 'transparent',
    border: highlighted ? '1px solid var(--olive-4)' : '1px solid transparent',
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
          fontWeight: 400,
          lineHeight: '20px',
          color: 'var(--slate-11)',
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
      <Link href={href} onClick={handleLinkClick} style={sharedStyle} {...hoverHandlers}>
        {content}
      </Link>
    );
  }

  return (
    <button type="button" onClick={onClick} style={sharedStyle} {...hoverHandlers}>
      {content}
    </button>
  );
}
