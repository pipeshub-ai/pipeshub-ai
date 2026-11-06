'use client';

import { useTranslation } from 'react-i18next';

import React, { useLayoutEffect, useRef } from 'react';
import { Dialog as DialogPrimitive } from 'radix-ui';
import { Flex, Box, Text, IconButton, Theme } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

interface MobileBottomSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Header title text */
  title: string;
  /** Optional back button — rendered to the left of the title when navigating sub-panels */
  onBack?: () => void;
  children: React.ReactNode;
  /**
   * Where focus goes when the sheet closes. Defaults to the control that had focus when it
   * opened; pass this when that control re-renders while the sheet is up (the workspace menu).
   */
  returnFocusTo?: () => HTMLElement | null;
}

/**
 * Bottom-sheet drawer for mobile, on a Radix Dialog: it takes focus when it opens,
 * keeps Tab inside, hides the page behind from assistive technology, closes on
 * Escape and on the backdrop, and hands focus back to the control that opened it.
 * The look is drawn here; entrance uses CSS keyframes (defined in globals.css).
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
  returnFocusTo,
}: MobileBottomSheetProps) {
  const { t } = useTranslation();
  /** The control that had focus when the sheet opened. */
  const openerRef = useRef<HTMLElement | null>(null);

  // Read before Radix moves focus into the sheet (layout effects run first).
  useLayoutEffect(() => {
    if (!open) return;
    const active = document.activeElement;
    openerRef.current = active instanceof HTMLElement && active !== document.body ? active : null;
  }, [open]);

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        {/* Theme wrappers give the portalled parts the app's theme variables, as Radix
            Themes' own Dialog does. */}
        <Theme asChild>
          <DialogPrimitive.Overlay
            style={{
              position: 'fixed',
              inset: 0,
              backgroundColor: 'var(--black-a8)',
              zIndex: 9990,
              animation: 'mobileSheetBackdropIn 0.2s ease-out',
            }}
          />
        </Theme>
        <Theme asChild>
          <DialogPrimitive.Content
            aria-modal="true"
            aria-describedby={undefined}
            onCloseAutoFocus={(e) => {
              e.preventDefault();
              const target = returnFocusTo?.() ?? openerRef.current;
              openerRef.current = null;
              if (target?.isConnected) target.focus();
            }}
            style={{
              position: 'fixed',
              bottom: 0,
              left: '4px',
              right: '4px',
              zIndex: 9991,
              display: 'flex',
              flexDirection: 'column',
              // A sheet is a panel: same frost as the side panels
              backdropFilter: 'var(--frost-filter)',
              WebkitBackdropFilter: 'var(--frost-filter)',
              backgroundColor: 'var(--frost-bg)',
              border: '1px solid var(--border-subtle)',
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
                className="touch-hit"
                variant="ghost"
                color="gray"
                size="2"
                onClick={onBack}
                aria-label={t('common.back')}
                style={{ margin: 0, flexShrink: 0 }}
              >
                <MaterialIcon name="chevron_left" size={20} color="var(--gray-11)" />
              </IconButton>
            )}
            <DialogPrimitive.Title asChild>
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
            </DialogPrimitive.Title>
          </Flex>

          <IconButton
            className="touch-hit"
            variant="ghost"
            color="gray"
            size="2"
            onClick={() => onOpenChange(false)}
            aria-label={t('common.close')}
            style={{ margin: 0, flexShrink: 0 }}
          >
            <MaterialIcon name="close" size={18} color="var(--gray-11)" />
          </IconButton>
        </Flex>

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
          </DialogPrimitive.Content>
        </Theme>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
