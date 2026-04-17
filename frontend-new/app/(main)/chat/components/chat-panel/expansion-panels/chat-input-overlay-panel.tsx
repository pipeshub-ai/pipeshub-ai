'use client';

import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Box, Flex, Theme } from '@radix-ui/themes';
import { useThemeAppearance } from '@/app/components/theme-provider';

interface ChatInputOverlayPanelProps {
  /** Whether the overlay panel is visible */
  open: boolean;
  /** Called on Escape key or backdrop click — parent decides what to do */
  onCollapse: () => void;
  /** Panel content — fills the 568×480 content area */
  children: React.ReactNode;
  /** Whether to show the bottom gradient fade (default: true) */
  showScrollFade?: boolean;
}

/**
 * Reusable overlay container for expansion panel content.
 *
 * Renders a centered 600×512 floating panel with backdrop blur, shadow,
 * and an optional bottom gradient fade. Content is passed via `children`
 * and fills the available space.
 *
 * This is the "overlay" counterpart to `ChatInputExpansionPanel`:
 * - `ChatInputExpansionPanel` → renders content inline within chat input
 * - `ChatInputOverlayPanel`  → renders content as a centered floating overlay
 *
 * The content component is identical in both — only the container changes.
 * The expand/collapse toggle in the content component switches between them.
 *
 * Dismissal: Escape key or backdrop click calls `onCollapse`.
 * Rendered via React Portal to avoid z-index stacking issues.
 */
export function ChatInputOverlayPanel({
  open,
  onCollapse,
  children,
  showScrollFade = true,
}: ChatInputOverlayPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const { appearance } = useThemeAppearance();
  const [contentLeft, setContentLeft] = useState(0);

  // Measure the main content area's left offset (accounts for sidebar width)
  useEffect(() => {
    if (!open) return;

    function measure() {
      const contentArea = document.querySelector('[data-main-content]');
      if (contentArea) {
        setContentLeft(contentArea.getBoundingClientRect().left);
      }
    }

    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [open]);

  // Collapse on Escape key
  useEffect(() => {
    if (!open) return;

    function handleEscape(e: KeyboardEvent) {
      if (e.key === 'Escape') onCollapse();
    }

    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [open, onCollapse]);

  // Body scroll lock
  useEffect(() => {
    if (!open) return;

    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, [open]);

  if (!open) return null;

  const overlay = (
    <Theme
      accentColor="jade"
      grayColor="olive"
      appearance={appearance}
      radius="medium"
    >
      <div
        onClick={(e: React.MouseEvent) => {
          // Collapse on backdrop click (only if clicking the scrim itself)
          if (e.target === e.currentTarget) onCollapse();
        }}
        style={{
          position: 'fixed',
          top: 0,
          left: contentLeft,
          right: 0,
          bottom: 40,
          zIndex: 50,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {/* Panel container — stop propagation to prevent backdrop click */}
        <Flex
          ref={panelRef}
          direction="column"
          onClick={(e: React.MouseEvent) => e.stopPropagation()}
          style={{
            width: '37.5rem',
            height: '512px',
            backdropFilter: 'blur(25px)',
            WebkitBackdropFilter: 'blur(25px)',
            backgroundColor: 'var(--color-surface)',
            border: '1px solid var(--slate-a3)',
            borderRadius: 'var(--radius-2)',
            boxShadow: '0px 20px 48px 0px var(--black-a6)',
            overflow: 'hidden',
            padding: 'var(--space-4)',
            position: 'relative',
          }}
        >
          {/* Content wrapper */}
          <Flex
            direction="column"
            style={{
              flex: 1,
              overflow: 'hidden',
            }}
          >
            {children}
          </Flex>

          {/* Bottom gradient fade (scroll indicator) */}
          {showScrollFade && (
            <Box
              style={{
                position: 'absolute',
                bottom: 'var(--space-4)',
                left: 'var(--space-4)',
                right: 'var(--space-4)',
                height: 'var(--space-8)', /* was: 44px, delta: +4px */
                background:
                  'linear-gradient(to bottom, transparent, var(--color-background))',
                pointerEvents: 'none',
                borderRadius: '0 0 var(--radius-2) var(--radius-2)',
              }}
            />
          )}
        </Flex>
      </div>
    </Theme>
  );

  return createPortal(overlay, document.body);
}
