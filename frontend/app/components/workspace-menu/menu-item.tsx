'use client';

import { useState } from 'react';
import Link from 'next/link';
import { Box } from '@radix-ui/themes';
import { ITEM_HEIGHT } from './types';

// ============================================
// Divider
// ============================================

/** Horizontal divider between menu sections */
export function Divider() {
  return (
    <Box
      style={{
        height: 1,
        width: '100%',
        backgroundColor: 'var(--slate-a3)',
        flexShrink: 0,
      }}
    />
  );
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
}

/** Single interactive row in the workspace menu */
export function MenuItem({
  icon,
  label,
  rightSlot,
  onClick,
  href,
  textColor = 'var(--slate-11)',
  isActive = false,
}: MenuItemProps) {
  const [isHovered, setIsHovered] = useState(false);

  const isExternal = href?.startsWith('http');

  const sharedStyle: React.CSSProperties = {
    appearance: 'none',
    margin: 0,
    font: 'inherit',
    color: 'inherit',
    outline: 'none',
    textDecoration: 'none',
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    width: '100%',
    height: ITEM_HEIGHT,
    padding: '0 12px',
    boxSizing: 'border-box',
    flexShrink: 0,
    borderRadius: 'var(--radius-1)',
    backgroundColor: isActive
      ? 'var(--slate-a3)'
      : isHovered
        ? 'var(--olive-3)'
        : 'transparent',
    border: isActive
      ? '1px solid var(--slate-a4)'
      : isHovered
        ? '1px solid var(--olive-4)'
        : '1px solid transparent',
    cursor: (onClick || href) ? 'pointer' : 'default',
  };

  const hoverHandlers = {
    onMouseEnter: () => setIsHovered(true),
    onMouseLeave: () => setIsHovered(false),
  };

  const content = (
    <>
      {icon}
      <div
        style={{
          flex: 1,
          fontSize: 14,
          fontWeight: 400,
          lineHeight: '20px',
          color: isActive ? 'var(--slate-12)' : textColor,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          textAlign: 'left',
        }}
      >
        {label}
      </div>
      {rightSlot}
    </>
  );

  if (href && isExternal) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        onClick={onClick}
        style={sharedStyle}
        {...hoverHandlers}
      >
        {content}
      </a>
    );
  }

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
