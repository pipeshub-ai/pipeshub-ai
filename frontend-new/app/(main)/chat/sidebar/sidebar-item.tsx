'use client';

import { useState } from 'react';
import Link from 'next/link';
import { CHAT_ITEM_HEIGHT } from '@/app/components/sidebar';

interface SidebarItemProps {
  /** Optional left icon (MaterialIcon, Image, any ReactNode) */
  icon?: React.ReactNode;
  /** Label text or ReactNode displayed in the item */
  label: React.ReactNode;
  /** Optional content rendered on the right side (e.g., keyboard shortcut badge) */
  rightSlot?: React.ReactNode;
  /** Click handler */
  onClick?: () => void;
  /** Navigation href — renders as <a> when provided */
  href?: string;
  /** Whether this item is currently selected/active */
  isActive?: boolean;
  /** Text color (default: 'var(--slate-11)') */
  textColor?: string;
  /** Font weight (default: 400) */
  fontWeight?: number;
  /** Force the highlighted (hovered) visual state (e.g. when a child dropdown is open) */
  forceHighlight?: boolean;
  /** Callback when hover state changes */
  onHoverChange?: (isHovered: boolean) => void;
}

/**
 * Unified interactive sidebar item.
 *
 * All clickable sidebar elements (nav links, chat items, action buttons)
 * share this component for consistent sizing, spacing, and hover treatment.
 *
 * Hover / active: olive-3 background + olive-4 border
 * Default: transparent background, transparent border
 */
export function SidebarItem({
  icon,
  label,
  rightSlot,
  onClick,
  href,
  isActive = false,
  textColor = 'var(--slate-11)',
  fontWeight = 400,
  forceHighlight = false,
  onHoverChange,
}: SidebarItemProps) {
  const [isHovered, setIsHovered] = useState(false);
  const highlighted = isActive || isHovered || forceHighlight;

  const handleMouseEnter = () => {
    setIsHovered(true);
    onHoverChange?.(true);
  };

  const handleMouseLeave = () => {
    setIsHovered(false);
    onHoverChange?.(false);
  };

  const sharedStyle: React.CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    gap: 'var(--space-2)',
    width: '100%',
    height: CHAT_ITEM_HEIGHT,
    padding: '0 var(--space-3)',
    boxSizing: 'border-box',
    flexShrink: 0,
    borderRadius: 'var(--radius-1)',
    backgroundColor: highlighted ? 'var(--olive-3)' : 'transparent',
    border: highlighted ? '1px solid var(--olive-4)' : '1px solid transparent',
    cursor: (onClick || href) ? 'pointer' : 'default',
    userSelect: 'none',
    textDecoration: 'none',
    color: 'inherit',
  };

  const labelContent = typeof label === 'string' ? (
    <span
      style={{
        flex: 1,
        fontSize: 14,
        fontWeight,
        lineHeight: 'var(--line-height-2)',
        color: textColor,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
        textAlign: 'left',
      }}
    >
      {label}
    </span>
  ) : (
    <div
      style={{
        flex: 1,
        fontSize: 14,
        fontWeight,
        lineHeight: 'var(--line-height-2)',
        color: textColor,
        overflow: 'hidden',
        textAlign: 'left',
      }}
    >
      {label}
    </div>
  );

  const content = (
    <>
      {icon}
      {labelContent}
      {rightSlot}
    </>
  );

  if (href) {
    const handleLinkClick = (e: React.MouseEvent) => {
      if (e.metaKey || e.ctrlKey || e.shiftKey) return;
      onClick?.();
    };

    // Never nest interactive controls (e.g. menu triggers) inside <a> — invalid HTML
    // and clicks can still activate the link / full navigation in the browser.
    if (rightSlot) {
      return (
        <div
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
          style={sharedStyle}
        >
          <Link
            href={href}
            onClick={handleLinkClick}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--space-2)',
              flex: 1,
              minWidth: 0,
              textDecoration: 'none',
              color: 'inherit',
              cursor: 'pointer',
            }}
          >
            {icon}
            {labelContent}
          </Link>
          <span
            style={{ flexShrink: 0, display: 'inline-flex', alignItems: 'center' }}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
            }}
          >
            {rightSlot}
          </span>
        </div>
      );
    }

    return (
      <Link
        href={href}
        onClick={handleLinkClick}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        style={sharedStyle}
      >
        {content}
      </Link>
    );
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onClick?.();
    }
  };

  return (
    <div
      role="button"
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={handleKeyDown}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      style={sharedStyle}
    >
      {content}
    </div>
  );
}
