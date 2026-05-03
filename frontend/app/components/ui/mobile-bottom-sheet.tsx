'use client';

import React, { useEffect, useRef, useCallback, useState } from 'react';
import { createPortal } from 'react-dom';
import { Flex, Box, Text, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

interface MobileBottomSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Header title text */
  title: string;
  /** Optional back button — rendered to the left of the title when navigating sub-panels */
  onBack?: () => void;
  children: React.ReactNode;
}

/**
 * Custom bottom-sheet drawer for mobile.
 * Renders via portal to document.body. No third-party animation library.
 * Uses CSS keyframe animations (defined in globals.css) for entrance.
 *
 * Layout:
 *   Root panel:    [Title (left-aligned) ···  ×]
 *   Sub-panel:     [← Title (left-aligned) ···  ×]
 */
export function MobileBottomSheet({
  open,
  onOpenChange,
  title,
  onBack,
  children,
}: MobileBottomSheetProps) {
  const sheetRef = useRef<HTMLDivElement>(null);
  const [portalTarget, setPortalTarget] = useState<Element | null>(null);

  // Resolve portal target: the .radix-themes wrapper so CSS variables + theme work
  useEffect(() => {
    setPortalTarget(
      document.querySelector('.radix-themes') ?? document.body
    );
  }, []);

  // Lock body scroll while open
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  // Close on Escape key
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onOpenChange(false);
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open, onOpenChange]);

  // Auto-focus the first focusable element inside the sheet
  useEffect(() => {
    if (!open || !sheetRef.current) return;
    const el = sheetRef.current.querySelector<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    el?.focus();
  }, [open]);

  const handleBackdropClick = useCallback(() => {
    onOpenChange(false);
  }, [onOpenChange]);

  if (!open || !portalTarget) return null;

  return createPortal(
    <>
      {/* Backdrop */}
      <div
        onClick={handleBackdropClick}
        style={{
          position: 'fixed',
          inset: 0,
          backgroundColor: 'rgba(0, 0, 0, 0.6)',
          zIndex: 9990,
          animation: 'mobileSheetBackdropIn 0.2s ease-out',
        }}
      />

      {/* Sheet */}
      <div
        ref={sheetRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        style={{
          position: 'fixed',
          bottom: 0,
          left: '4px',
          right: '4px',
          zIndex: 9991,
          display: 'flex',
          flexDirection: 'column',
          backdropFilter: 'blur(8px)',
          WebkitBackdropFilter: 'blur(8px)',
          backgroundColor: 'var(--effects-translucent)',
          border: '1px solid var(--olive-3)',
          borderBottom: 'none',
          borderTopLeftRadius: 'var(--radius-1)',
          borderTopRightRadius: 'var(--radius-1)',
          maxHeight: '60dvh',
          overflow: 'hidden',
          outline: 'none',
          animation: 'mobileSheetSlideUp 0.25s ease-out',
        }}
      >
        {/* Header: [← title]  [×] */}
        <Flex
          align="center"
          justify="between"
          style={{
            padding: 'var(--space-3) var(--space-4)',
            flexShrink: 0,
          }}
        >
          <Flex align="center" gap="1" style={{ flex: 1, minWidth: 0 }}>
            {onBack && (
              <IconButton
                variant="ghost"
                color="gray"
                size="2"
                onClick={onBack}
                aria-label="Go back"
                style={{ margin: 0, flexShrink: 0 }}
              >
                <MaterialIcon name="chevron_left" size={20} color="var(--gray-11)" />
              </IconButton>
            )}
            <Text
              size="3"
              weight="medium"
              style={{
                color: 'var(--gray-12)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {title}
            </Text>
          </Flex>

          <IconButton
            variant="ghost"
            color="gray"
            size="2"
            onClick={() => onOpenChange(false)}
            aria-label="Close"
            style={{ margin: 0, flexShrink: 0 }}
          >
            <MaterialIcon name="close" size={18} color="var(--gray-11)" />
          </IconButton>
        </Flex>

        {/* Scrollable content */}
        <Box
          className="no-scrollbar"
          style={{
            overflowY: 'auto',
            flex: 1,
            padding: '0 var(--space-4) var(--space-6)',
          }}
        >
          {children}
        </Box>
      </div>
    </>,
    portalTarget,
  );
}
