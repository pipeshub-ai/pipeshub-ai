'use client';

import React, { useLayoutEffect, useRef, useState } from 'react';
import { Box } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { MobileBottomSheet } from '@/app/components/ui/mobile-bottom-sheet';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';

// ============================================================
// SubPanel — shared floating container for expanded sub-panels
// ============================================================

/** Minimum gap between the floating panel and the viewport edges. */
const VIEWPORT_GAP = 8;

interface SubPanelProps {
  isOpen: boolean;
  /** Sheet title on mobile */
  title: string;
  /** Returns to the main menu (mobile back and close) */
  onBack: () => void;
  children: React.ReactNode;
}

/**
 * Floating panel that appears to the right of the workspace menu.
 * On mobile it opens as a bottom sheet instead. Renders nothing when closed.
 */
export function SubPanel({ isOpen, title, onBack, children }: SubPanelProps) {
  const isMobile = useIsMobile();
  const panelRef = useRef<HTMLDivElement>(null);
  const [shiftUp, setShiftUp] = useState(0);

  // The panel hangs from the menu's top edge; when it is taller than the space below,
  // lift it so its last row stays on screen (it scrolls once it fills the viewport).
  useLayoutEffect(() => {
    const el = panelRef.current;
    if (!isOpen || isMobile || !el) return;
    const rect = el.getBoundingClientRect();
    const top = rect.top + shiftUp;
    setShiftUp(Math.max(0, Math.min(top - VIEWPORT_GAP, top + rect.height - (window.innerHeight - VIEWPORT_GAP))));
  }, [isOpen, isMobile, children, shiftUp]);

  if (!isOpen) return null;

  if (isMobile) {
    return (
      <MobileBottomSheet open title={title} onBack={onBack} onOpenChange={onBack}>
        {children}
      </MobileBottomSheet>
    );
  }

  return (
    <Box
      ref={panelRef}
      style={{
        position: 'absolute',
        left: '100%',
        marginLeft: 8,
        top: -shiftUp,
        maxHeight: `calc(100dvh - ${VIEWPORT_GAP * 2}px)`,
        overflowY: 'auto',
        borderRadius: 'var(--radius-1)',
        border: '1px solid var(--olive-3)',
        backgroundColor: 'var(--color-background)',
        boxShadow: '0 8px 24px rgba(0, 0, 0, 0.15)',
        padding: 4,
        display: 'flex',
        flexDirection: 'column',
        gap: 2,
        fontFamily: 'Manrope, sans-serif',
        width: 250,
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
