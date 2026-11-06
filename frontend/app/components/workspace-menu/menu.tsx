'use client';

import { useState, useEffect, useRef } from 'react';
import { Flex, Popover } from '@radix-ui/themes';
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

/** What counts as a control the user deliberately clicked into (not a container). */
const INTERACTIVE_SELECTOR =
  'button, a[href], input, select, textarea, [contenteditable="true"], [tabindex]:not([tabindex="-1"])';

/** Which sub-panel is currently open (at most one) */
type ActiveSubPanel = null | 'appearance' | 'language';

interface WorkspaceMenuProps {
  /** Whether the popup is visible */
  isOpen: boolean;
  /** Called when the popup should close */
  onClose: () => void;
  /** Called when the trigger asks to open the popup */
  onOpen?: () => void;
  /** Organisation details fetched at the page level */
  org: OrgInfo | null;
  /**
   * The trigger button. It becomes the popover's trigger, so the menu anchors to
   * it and focus returns to it on close.
   */
  trigger?: React.ReactElement;
  /** A trigger rendered elsewhere (no `trigger`): outside clicks on it are ignored. */
  triggerRef?: React.RefObject<HTMLElement | null>;
}

// ============================================
// WorkspaceMenu
// ============================================

/**
 * The workspace and account menu, opened from the sidebar footer button.
 *
 * A Radix Popover above the trigger: Radix owns placement, Escape, outside
 * click, moving focus in on open and back to the trigger on close. Composed of
 * discrete sections (settings, external links). The organisation is already
 * shown on the trigger, so it is not repeated here.
 */
export function WorkspaceMenu({ isOpen, onClose, onOpen, trigger, triggerRef }: WorkspaceMenuProps) {
  const isMobile = useIsMobile();
  /** The trigger element, so focus can return to it and its own drawer is not mistaken for a dialog. */
  const triggerElRef = useRef<HTMLButtonElement>(null);
  const [activePanel, setActivePanel] = useState<ActiveSubPanel>(null);
  // On phones a sub-panel is a bottom sheet portaled into the root theme,
  // which paints below this portal, so the menu card steps aside.
  const sheetOnly = isMobile && activePanel !== null;

  // ── Reset sub-panels when menu closes ──
  useEffect(() => {
    if (!isOpen) setActivePanel(null);
  }, [isOpen]);

  const togglePanel = (panel: 'appearance' | 'language') => {
    setActivePanel((prev) => (prev === panel ? null : panel));
  };

  /**
   * Interactions the popover must not treat as "outside": a phone sub-panel sheet
   * (portalled elsewhere, with its own backdrop and dismissal), a dialog opened
   * from the menu, and a trigger rendered outside the popover.
   */
  const keepOpenOnOutside = (event: { target: EventTarget | null; preventDefault: () => void }) => {
    const target = event.target;
    if (sheetOnly) {
      event.preventDefault();
      return;
    }
    // A dialog opened from the menu (portalled, so it does not hold the trigger) keeps it
    // open. The navigation drawer the trigger sits in is the menu's own surroundings: a tap
    // there is an ordinary outside tap and closes the menu.
    const dialog = target instanceof Element ? target.closest('[role="dialog"], [role="alertdialog"]') : null;
    const triggerEl = triggerElRef.current ?? triggerRef?.current ?? null;
    if (dialog && !(triggerEl && dialog.contains(triggerEl))) {
      event.preventDefault();
      return;
    }
    if (target instanceof Node && triggerRef?.current?.contains(target)) event.preventDefault();
  };

  const subPanels = (
    <>
      <AppearancePanel isOpen={activePanel === 'appearance'} onBack={() => setActivePanel(null)} />
      <LanguagePanel isOpen={activePanel === 'language'} onBack={() => setActivePanel(null)} />
    </>
  );

  return (
    <Popover.Root open={isOpen} onOpenChange={(next) => (next ? onOpen?.() : onClose())}>
      {trigger ? (
        <Popover.Trigger ref={triggerElRef}>{trigger}</Popover.Trigger>
      ) : (
        <Popover.Anchor>
          <span aria-hidden style={{ position: 'absolute', inset: 'var(--space-2)', pointerEvents: 'none' }} />
        </Popover.Anchor>
      )}
      <Popover.Content
        side="top"
        align="start"
        sideOffset={4}
        collisionPadding={8}
        onInteractOutside={keepOpenOnOutside}
        onEscapeKeyDown={(e) => {
          // The phone sheet handles Escape itself (back to the menu).
          if (sheetOnly) e.preventDefault();
        }}
        onCloseAutoFocus={(e) => {
          const returnTo = triggerElRef.current ?? triggerRef?.current ?? null;
          // No trigger of our own: hand focus back to the one rendered elsewhere.
          if (!trigger && returnTo) {
            e.preventDefault();
            returnTo.focus();
            return;
          }
          // After an outside click Radix leaves focus where the click landed. If that was
          // nothing focusable (focus fell to the body), the trigger takes it back.
          // A container (the drawer, a panel) is not a place the user chose either.
          // No trigger left on the page (the menu went with it): nothing to give focus back to.
          if (!returnTo?.isConnected) return;
          window.setTimeout(() => {
            // The trigger can still go before this tick, and the page with it.
            if (!returnTo?.isConnected) return;
            const active = document.activeElement;
            const landedNowhere =
              !active || active === document.body || !active.matches(INTERACTIVE_SELECTOR);
            if (landedNowhere) returnTo.focus();
          }, 0);
        }}
        style={
          sheetOnly
            ? {
                // Steps aside while the phone sheet is up; the popover stays open under it.
                width: 0,
                minWidth: 0,
                height: 0,
                padding: 0,
                border: 'none',
                boxShadow: 'none',
                background: 'none',
                overflow: 'visible',
              }
            : {
                width: overlayWidth(POPUP_WIDTH),
                maxWidth: 'none',
                minWidth: 0,
                padding: 'var(--space-4) var(--space-2)',
                borderRadius: 'var(--radius-1)',
                border: '1px solid var(--border-subtle)',
                backgroundColor: 'var(--frost-bg-strong)',
                backdropFilter: 'var(--frost-filter)',
                WebkitBackdropFilter: 'var(--frost-filter)',
                // The side sub-panels hang outside the box; Radix's popover scrolls by default.
                overflow: 'visible',
              }
        }
      >
        {sheetOnly ? (
          subPanels
        ) : (
          <Flex direction="column" gap="4">
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
          </Flex>
        )}
      </Popover.Content>
    </Popover.Root>
  );
}
