'use client';

import { useState } from 'react';
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
  isActive = false,
  paddingLeft = 12,
}: WorkspaceSidebarItemProps) {
  const [isHovered, setIsHovered] = useState(false);
  const highlighted = isActive || isHovered;

  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        // Reset native button defaults
        appearance: 'none',
        margin: 0,
        font: 'inherit',
        color: 'inherit',
        outline: 'none',
        textDecoration: 'none',
        // Layout
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        width: '100%',
        height: ELEMENT_HEIGHT,
        paddingLeft,
        paddingRight: 12,
        boxSizing: 'border-box',
        flexShrink: 0,
        // Visual
        borderRadius: 'var(--radius-1)',
        backgroundColor: highlighted ? 'var(--olive-3)' : 'transparent',
        border: highlighted ? '1px solid var(--olive-4)' : '1px solid transparent',
        cursor: onClick ? 'pointer' : 'default',
      }}
    >
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
    </button>
  );
}
