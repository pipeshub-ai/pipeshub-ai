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

  if (!isOpen) return null;

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
        style={{
          position: 'absolute',
          bottom: 60, // above the footer button
          left: 8,
          width: overlayWidth(POPUP_WIDTH),
          borderRadius: 'var(--radius-1)',
          border: '1px solid var(--olive-3)',
          backgroundColor: 'var(--effects-translucent)',
          backdropFilter: 'blur(25px)',
          boxShadow: '0 8px 24px rgba(0, 0, 0, 0.15)',
          padding: '16px 8px',
          // The portal is a sibling of the root theme's stacking context
          // (z-index: 0), so any positive value paints above the app.
          zIndex: 50,
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
          fontFamily: 'Manrope, sans-serif',
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
