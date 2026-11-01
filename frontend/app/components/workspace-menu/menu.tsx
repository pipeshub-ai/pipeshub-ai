'use client';

import { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { Theme, Box } from '@radix-ui/themes';
import { logoutFromWorkspaceMenu, SettingsSection } from '@/config';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import type { OrgInfo } from './types';
import { overlayWidth } from '@/app/components/ui/responsive';
import { POPUP_WIDTH } from './types';
import { Divider } from './menu-item';
import { ExternalLinksSection } from './external-links-section';
import { LanguagePanel } from './language-panel';
import { AppearancePanel } from './appearance-panel';

/** Matches the .workspace-menu-card.is-closing animation in globals.css */
const MENU_EXIT_MS = 120;

// ============================================
// Types
// ============================================

/** Which sub-panel is currently open (at most one) */
type ActiveSubPanel = null | 'appearance' | 'language';

interface WorkspaceMenuProps {
  /** Whether the popup is visible */
  isOpen: boolean;
  /** Called when the popup should close */
  onClose: () => void;
  /** Organisation details fetched at the page level */
  org: OrgInfo | null;
  /** Ref to the trigger element so click-outside ignores it */
  triggerRef?: React.RefObject<HTMLElement | null>;
}

// ============================================
// WorkspaceMenu
// ============================================

/**
 * Floating popup triggered from the sidebar footer button.
 *
 * Composed of discrete sections (settings, external links). The
 * organisation is already shown on the trigger, so it is not repeated here.
 */
export function WorkspaceMenu({ isOpen, onClose, triggerRef }: WorkspaceMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const isMobile = useIsMobile();
  const [activePanel, setActivePanel] = useState<ActiveSubPanel>(null);
  // Stays mounted for the exit animation after isOpen turns false
  const [isMounted, setIsMounted] = useState(isOpen);

  useEffect(() => {
    if (isOpen) {
      setIsMounted(true);
      return;
    }
    const timer = setTimeout(() => setIsMounted(false), MENU_EXIT_MS);
    return () => clearTimeout(timer);
  }, [isOpen]);

  // ── Close on click outside ──
  useEffect(() => {
    if (!isOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (
        menuRef.current &&
        !menuRef.current.contains(e.target as Node) &&
        // The mobile sub-panel sheet is portaled outside the menu
        !(e.target as Element).closest?.('[role="dialog"]') &&
        !triggerRef?.current?.contains(e.target as Node)
      ) {
        onClose();
      }
    };
    // Delay so the opening click doesn't immediately close
    const timer = setTimeout(() => {
      document.addEventListener('mousedown', handleClickOutside);
    }, 0);
    return () => {
      clearTimeout(timer);
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen, onClose]);

  // ── Close on Escape ──
  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose]);

  // ── Reset sub-panels when menu closes ──
  useEffect(() => {
    if (!isOpen) setActivePanel(null);
  }, [isOpen]);

  if (!isMounted) return null;

  const togglePanel = (panel: 'appearance' | 'language') => {
    setActivePanel((prev) => (prev === panel ? null : panel));
  };

  const subPanels = (
    <>
      <AppearancePanel isOpen={activePanel === 'appearance'} onBack={() => setActivePanel(null)} />
      <LanguagePanel isOpen={activePanel === 'language'} onBack={() => setActivePanel(null)} />
    </>
  );

  return createPortal(
    <Theme>
      {/* On phones a sub-panel is a bottom sheet portaled into the root theme,
          which paints below this portal, so the menu card steps aside. */}
      {isMobile && activePanel ? subPanels : (
      <Box
        ref={menuRef}
        className={isOpen ? 'workspace-menu-card' : 'workspace-menu-card is-closing'}
        style={{
          position: 'absolute',
          bottom: 58, // 8px above the 42px footer button (8px footer inset)
          left: 8,
          width: overlayWidth(POPUP_WIDTH),
          borderRadius: 'var(--radius-1)',
          border: '1px solid var(--olive-3)',
          backgroundColor: 'var(--frost-bg-strong)',
          backdropFilter: 'var(--frost-filter)',
          boxShadow: '0 8px 24px rgba(0, 0, 0, 0.15)',
          padding: 8,
          // The portal is a sibling of the root theme's stacking context
          // (z-index: 0), so any positive value paints above the app.
          zIndex: 50,
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
          fontFamily: 'Manrope, sans-serif',
          pointerEvents: isOpen ? undefined : 'none',
        }}
      >
        {/* ── Section 1: Settings ── */}
        <SettingsSection
          onWorkspaceSettings={onClose}
          onAppearanceToggle={() => togglePanel('appearance')}
          isAppearanceActive={activePanel === 'appearance'}
          onLanguageToggle={() => togglePanel('language')}
          isLanguageActive={activePanel === 'language'}
          onLogout={() => {
            onClose();
            logoutFromWorkspaceMenu();
          }}
        />

        <Divider />

        {/* ── Section 2: External Links ── */}
        <ExternalLinksSection />

        {/* ── Sub-panels — float to the right, top-aligned ── */}
        {subPanels}
      </Box>
      )}
    </Theme>,
    document.body
  );
}
