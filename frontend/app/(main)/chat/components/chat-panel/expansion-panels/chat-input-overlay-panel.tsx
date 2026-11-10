'use client';

import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Flex, Theme } from '@radix-ui/themes';
import { useThemeAppearance } from '@/app/components/theme-provider';

interface ChatInputOverlayPanelProps {
  /** Whether the overlay panel is visible */
  open: boolean;
  /** Called on Escape key or backdrop click — parent decides what to do */
  onCollapse: () => void;
  /** Panel content — fills the panel inside its 16px padding */
  children: React.ReactNode;
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
            // Expanded view: larger than the inline panel (the composer's 800px x 380px)
            width: 'min(960px, calc(100% - 32px))',
            height: 'min(680px, 100dvh - 120px)',
            backdropFilter: 'var(--frost-filter)',
            WebkitBackdropFilter: 'var(--frost-filter)',
            backgroundColor: 'var(--frost-bg)',
            border: '1px solid var(--olive-3)',
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

        </Flex>
      </div>
    </Theme>
  );

  return createPortal(overlay, document.body);
}
