'use client';

import React, { createContext, useContext, useLayoutEffect, useRef, useState } from 'react';
import { Box, Button, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { MobileBottomSheet } from '@/app/components/ui/mobile-bottom-sheet';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';

// ============================================================
// SubPanel — shared floating container for expanded sub-panels
// ============================================================

/** Minimum gap between the floating panel and the viewport edges. */
const VIEWPORT_GAP = 8;

/**
 * The workspace-menu row that opens the panel with this title. On a phone the menu steps
 * aside while the sheet is up and its rows render again when it closes, so focus goes
 * back to the row by its marker rather than to the node that was clicked. Null once the
 * page itself is gone (the sheet can close as the page is torn down).
 */
export function findPanelOpener(title: string): HTMLElement | null {
  if (typeof document === 'undefined') return null;
  const rows = document.querySelectorAll<HTMLElement>('[data-opens-panel]');
  return Array.from(rows).find((row) => row.getAttribute('data-opens-panel') === title) ?? null;
}

/** True inside a SubPanel whose rows are a single choice (rendered as radios). */
const ChoiceGroupContext = createContext(false);

interface SubPanelProps {
  isOpen: boolean;
  /** Sheet title on mobile */
  title: string;
  /** Returns to the main menu (mobile back and close) */
  onBack: () => void;
  children: React.ReactNode;
  /**
   * The rows are a single choice (Appearance): wrap them in a radio group named
   * with this label, so the chosen option is announced as checked.
   */
  choiceGroupLabel?: string;
}

/**
 * Floating panel that appears to the right of the workspace menu.
 * On mobile it opens as a bottom sheet instead. Renders nothing when closed.
 */
export function SubPanel({ isOpen, title, onBack, children, choiceGroupLabel }: SubPanelProps) {
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
    const next = Math.max(0, Math.min(top - VIEWPORT_GAP, top + rect.height - (window.innerHeight - VIEWPORT_GAP)));
    // The browser snaps layout to 1/64px, so the measured top drifts by a hair on every pass and a
    // fractional shift never settles: React hits its update-depth limit and the sidebar crashes.
    setShiftUp((prev) => (Math.abs(next - prev) < 1 ? prev : Math.round(next)));
  }, [isOpen, isMobile, children, shiftUp]);

  if (!isOpen) return null;

  const rows = choiceGroupLabel ? (
    <ChoiceGroupContext.Provider value>
      <Box
        role="radiogroup"
        aria-label={choiceGroupLabel}
        style={{ display: 'flex', flexDirection: 'column', gap: 'calc(var(--space-1) / 2)' }}
      >
        {children}
      </Box>
    </ChoiceGroupContext.Provider>
  ) : (
    children
  );

  if (isMobile) {
    // The sheet is portalled outside the workspace menu; the menu ignores outside
    // clicks and focus while a sheet is open, so the sheet does not close it.
    return (
      <MobileBottomSheet
        open
        title={title}
        onBack={onBack}
        onOpenChange={onBack}
        returnFocusTo={() => findPanelOpener(title)}
      >
        {rows}
      </MobileBottomSheet>
    );
  }

  return (
    <Box
      ref={panelRef}
      style={{
        position: 'absolute',
        left: '100%',
        marginLeft: 'var(--space-2)',
        top: -shiftUp,
        maxHeight: `calc(100dvh - ${VIEWPORT_GAP * 2}px)`,
        overflowY: 'auto',
        borderRadius: 'var(--radius-1)',
        border: '1px solid var(--border-subtle)',
        backgroundColor: 'var(--color-panel-solid)',
        boxShadow: 'var(--shadow-5)',
        padding: 'var(--space-1)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'calc(var(--space-1) / 2)',
        width: 250,
      }}
    >
      {rows}
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
 * A row inside a SubPanel: a Radix ghost Button, so hover, press and the focus
 * ring are Radix's. Inside a choice group (SubPanel `choiceGroupLabel`) it is a
 * radio, checked when active.
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
  const inChoiceGroup = useContext(ChoiceGroupContext);
  return (
    <Button
      type="button"
      variant="ghost"
      color="gray"
      size="2"
      onClick={onClick}
      role={inChoiceGroup ? 'radio' : undefined}
      aria-checked={inChoiceGroup ? isActive : undefined}
      style={{
        margin: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'flex-start',
        gap: 'var(--space-2)',
        width: '100%',
        // Radix sizes a ghost button content-box; border-box keeps the row at 32px.
        boxSizing: 'border-box',
        height: 'var(--space-6)',
        padding: '0 var(--space-2)',
        flexShrink: 0,
        whiteSpace: 'nowrap',
        borderRadius: 'var(--radius-1)',
      }}
    >
      {/* Icon slot — only rendered when an icon is provided */}
      {icon !== undefined && (
        <MaterialIcon
          name={icon}
          size={16}
          color={isActive ? 'var(--text-primary)' : 'var(--text-secondary)'}
        />
      )}

      {/* Label — fills available space, left-aligned */}
      <Text
        as="span"
        size="2"
        weight="regular"
        style={{
          flex: 1,
          color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
          textAlign: 'left',
        }}
      >
        {label}
      </Text>

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
        <MaterialIcon name="check" size={16} color="var(--text-secondary)" />
      </span>
    </Button>
  );
}
