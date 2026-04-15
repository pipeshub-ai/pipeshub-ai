'use client';

import { useState, useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { Flex, Box, Text } from '@radix-ui/themes';
import { logoutAndRedirect } from '@/lib/store/auth-store';
import { UserAvatar } from '@/app/components/ui/user-avatar';
import type { OrgInfo } from './types';
import { POPUP_WIDTH } from './types';
import { Divider } from './menu-item';
import { SettingsSection } from './settings-section';
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
 * Composed of discrete sections (settings, external links, org),
 * with the organisation badge rendered directly here.
 */
export function WorkspaceMenu({ isOpen, onClose, org, triggerRef }: WorkspaceMenuProps) {
  const router = useRouter();
  const menuRef = useRef<HTMLDivElement>(null);
  const [activePanel, setActivePanel] = useState<ActiveSubPanel>(null);

  // ── Close on click outside ──
  useEffect(() => {
    if (!isOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (
        menuRef.current &&
        !menuRef.current.contains(e.target as Node) &&
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

  const orgLogoUrl = org?.logoUrl ?? null;

  const togglePanel = (panel: 'appearance' | 'language') => {
    setActivePanel((prev) => (prev === panel ? null : panel));
  };

  return (
    <Box
      ref={menuRef}
      style={{
        position: 'absolute',
        bottom: 60, // above the footer button
        left: 8,
        width: POPUP_WIDTH,
        borderRadius: 'var(--radius-1)',
        border: '1px solid var(--olive-3)',
        backgroundColor: 'var(--effects-translucent)',
        backdropFilter: 'blur(25px)',
        boxShadow: '0 8px 24px rgba(0, 0, 0, 0.15)',
        padding: '16px 8px',
        zIndex: 50,
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
        fontFamily: 'Manrope, sans-serif',
      }}
    >
      {/* ── Section 1: Settings ── */}
      <SettingsSection
        onWorkspaceSettings={() => {
          onClose();
          router.push('/workspace');
        }}
        onAppearanceToggle={() => togglePanel('appearance')}
        isAppearanceActive={activePanel === 'appearance'}
        onLanguageToggle={() => togglePanel('language')}
        isLanguageActive={activePanel === 'language'}
        onLogout={() => {
          onClose();
          logoutAndRedirect();
        }}
      />

      <Divider />

      {/* ── Section 2: External Links ── */}
      <ExternalLinksSection />

      <Divider />

      {/* ── Section 3: Current Organisation ── */}
      {org && (
        <Flex direction="column" gap="3">
          {/* Org badge */}
          <Flex
            align="center"
            gap="2"
            style={{
              height: 40,
              padding: '0 8px',
              // backgroundColor: 'var(--olive-2)',
              // border: '1px solid var(--olive-3)',
              borderRadius: 'var(--radius-1)',
              flexShrink: 0,
            }}
          >
          {/* Org avatar badge */}
          <UserAvatar
            fullName={org?.shortName || org?.registeredName}
            src={orgLogoUrl}
            size={24}
            radius="small"
          />

          {/* Org name */}
          <Text
            size="2"
            weight="medium"
            style={{
              flex: 1,
              color: 'var(--accent-12)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {org?.shortName || org?.registeredName}
          </Text>
        </Flex>
        </Flex>
      )}

      {/* ── Sub-panels — float to the right, top-aligned ── */}
      <AppearancePanel isOpen={activePanel === 'appearance'} />
      <LanguagePanel isOpen={activePanel === 'language'} />
    </Box>
  );
}
