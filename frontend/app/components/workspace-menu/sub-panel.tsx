'use client';

import React, { useState } from 'react';
import { Box } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

// ============================================================
// SubPanel — shared floating container for expanded sub-panels
// ============================================================

interface SubPanelProps {
  isOpen: boolean;
  children: React.ReactNode;
}

/**
 * Floating panel that appears to the right of the workspace menu.
 * Renders nothing when closed.
 */
export function SubPanel({ isOpen, children }: SubPanelProps) {
  if (!isOpen) return null;

  return (
    <Box
      style={{
        position: 'absolute',
        left: '100%',
        marginLeft: 8,
        top: 0,
        borderRadius: 'var(--radius-1)',
        border: '1px solid var(--olive-3)',
        background: 'var(--effects-translucent)',
        backdropFilter: 'blur(25px)',
        boxShadow: '0 8px 24px rgba(0, 0, 0, 0.15)',
        padding: 4,
        display: 'flex',
        flexDirection: 'column',
        gap: 2,
        fontFamily: 'Manrope, sans-serif',
      }}
    >
      {children}
    </Box>
  );
}

// ============================================================
// SubPanelItem — a single row within a SubPanel
// ============================================================

interface SubPanelItemProps {
  label: string;
  /** Optional Material Icon name shown before the label. */
  icon?: string;
  isActive?: boolean;
  onClick: () => void;
}

/**
 * A row inside a SubPanel.
 *
 * Key layout guarantees:
 *  - Content is left-aligned regardless of icon presence.
 *  - The checkmark slot is always rendered (visibility: hidden when
 *    inactive) so the panel width never shifts as selections change.
 *  - `whiteSpace: nowrap` lets the panel size itself to the longest label.
 */
export function SubPanelItem({
  label,
  icon,
  isActive = false,
  onClick,
}: SubPanelItemProps) {
  const [isHovered, setIsHovered] = useState(false);

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
        height: 32,
        padding: '0 8px',
        boxSizing: 'border-box',
        flexShrink: 0,
        whiteSpace: 'nowrap',
        // Visual
        borderRadius: 'var(--radius-1)',
        backgroundColor: isHovered
            ? 'var(--olive-3)'
            : 'var(--effects-translucent)',
        border: isHovered
            ? '1px solid var(--olive-4)'
            : 'none',
        cursor: 'pointer',
      }}
    >
      {/* Icon slot — only rendered when an icon is provided */}
      {icon !== undefined && (
        <MaterialIcon
          name={icon}
          size={16}
          color={isActive ? 'var(--slate-12)' : 'var(--slate-11)'}
        />
      )}

      {/* Label — fills available space, left-aligned */}
      <span
        style={{
          flex: 1,
          fontSize: 13,
          fontWeight: 400,
          lineHeight: '20px',
          color: isActive ? 'var(--slate-12)' : 'var(--slate-11)',
          textAlign: 'left',
        }}
      >
        {label}
      </span>

      {/* Checkmark — always in the DOM so panel width stays stable.
          Use visibility rather than conditional render. */}
      <span
        style={{
          display: 'flex',
          alignItems: 'center',
          flexShrink: 0,
          visibility: isActive ? 'visible' : 'hidden',
        }}
      >
        <MaterialIcon name="check" size={16} color="var(--slate-11)" />
      </span>
    </button>
  );
}
