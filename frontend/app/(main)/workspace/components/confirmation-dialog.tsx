'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { AlertDialog, Box, Button, Flex, Text } from '@radix-ui/themes';
import { AlertDialog as AlertDialogPrimitive } from 'radix-ui';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { useFocusReturn } from '@/lib/hooks/use-focus-return';

// ========================================
// Types
// ========================================

export interface ConfirmationDialogProps {
  /** Controls open/close */
  open: boolean;
  onOpenChange: (open: boolean) => void;

  /** Dialog title (e.g. "Remove user?") */
  title: string;

  /** Descriptive message body */
  message: React.ReactNode;

  /** Label for the confirm button (e.g. "Remove") */
  confirmLabel: string;

  /** Label for the cancel button */
  cancelLabel?: string;

  /** Visual style of the confirm button */
  confirmVariant?: 'danger' | 'primary' | 'success';

  /** Whether the confirm action is in-progress */
  isLoading?: boolean;

  /** Label shown on the confirm button while loading (default: "Removing...") */
  confirmLoadingLabel?: string;

  /** When true, the confirm button is hidden (informational dialog only) */
  hideConfirm?: boolean;

  /** Callback when confirm is clicked */
  onConfirm: () => void;

  /**
   * Portal container for overlay + content. Pass the host from
   * {@link useWorkspaceDrawerNestedModalHost} when this dialog must stack above
   * {@link WorkspaceRightPanel} (z-index ~9201); otherwise omit for default body portal.
   */
  container?: HTMLElement | null;

  /**
   * `compact` is the short "are you sure?" confirm: 380px wide, a 16px title
   * and one line or two of consequence. `default` keeps the wider dialog.
   */
  size?: 'default' | 'compact';

  /**
   * When true, closing returns focus to the element that opened the dialog,
   * or to its menu's trigger when it was opened from a menu item.
   */
  returnFocus?: boolean;

  /** With `returnFocus`: where focus goes when the opener is gone. */
  returnFocusFallback?: () => HTMLElement | null;
}

// 380px: the width of the short confirm.
const COMPACT_MAX_WIDTH = 'calc(var(--space-9) * 6 - var(--space-1))';

// ========================================
// Component
// ========================================

/**
 * ConfirmationDialog — reusable modal for destructive or important actions.
 *
 * When opened from inside {@link WorkspaceRightPanel}, pass `container` from
 * `useWorkspaceDrawerNestedModalHost(open)` so the overlay sits above the drawer
 * (same pattern as {@link InstanceManagementPanel} delete confirmation).
 */
export function ConfirmationDialog({
  open,
  onOpenChange,
  title,
  message,
  confirmLabel,
  cancelLabel,
  confirmVariant = 'danger',
  isLoading = false,
  confirmLoadingLabel,
  hideConfirm = false,
  onConfirm,
  container,
  size = 'default',
  returnFocus = false,
  returnFocusFallback,
}: ConfirmationDialogProps) {
  const { t } = useTranslation();
  const resolvedCancelLabel = cancelLabel ?? t('common.cancel');
  const resolvedConfirmLoadingLabel = confirmLoadingLabel ?? t('common.confirm');
  const focusReturn = useFocusReturn({ fallback: returnFocusFallback });
  const compact = size === 'compact';
  return (
    <AlertDialog.Root open={open} onOpenChange={(v) => !isLoading && onOpenChange(v)}>
      <AlertDialog.Content
        container={container ?? undefined}
        {...(returnFocus ? focusReturn : {})}
        style={{
          maxWidth: compact ? COMPACT_MAX_WIDTH : '37.5rem',
          padding: 'var(--space-5)',
          borderRadius: 'var(--radius-5)',
          border: '1px solid var(--border-subtle)',
          boxShadow: 'var(--shadow-6)',
        }}
      >
        {compact ? (
          <CompactHeader title={title} message={message} />
        ) : (
          <>
            <AlertDialog.Title style={{ color: 'var(--text-primary)' }}>{title}</AlertDialog.Title>
            {typeof message === 'string' ? (
              <AlertDialog.Description
                size="2"
                style={{ color: 'var(--text-secondary)', lineHeight: 'var(--line-height-2)', marginTop: 'var(--space-2)' }}
              >
                {message}
              </AlertDialog.Description>
            ) : (
              <Box style={{ marginTop: 'var(--space-2)', color: 'var(--text-secondary)', lineHeight: 'var(--line-height-2)' }}>{message}</Box>
            )}
          </>
        )}
        <Flex justify="end" gap="2" mt="4">
          <AlertDialog.Cancel>
            <Button
              type="button"
              variant="outline"
              color="gray"
              size="2"
              disabled={isLoading}
              style={{ cursor: isLoading ? 'not-allowed' : 'pointer' }}
            >
              {hideConfirm ? t('common.close') : resolvedCancelLabel}
            </Button>
          </AlertDialog.Cancel>
          {!hideConfirm && (
            <LoadingButton
              type="button"
              variant="solid"
              // 'success' keeps its name for callers but draws the brand accent:
              // one green for every confirm.
              color={confirmVariant === 'danger' ? 'red' : undefined}
              size="2"
              onClick={(e) => {
                e.preventDefault();
                onConfirm();
              }}
              loading={isLoading}
              loadingLabel={resolvedConfirmLoadingLabel}
            >
              {confirmLabel}
            </LoadingButton>
          )}
        </Flex>
      </AlertDialog.Content>
    </AlertDialog.Root>
  );
}

/**
 * The compact title and message: a 16px medium title with the message right
 * under it. The message is always the dialog's description, so screen
 * readers read it with the title, whether it is plain text or markup.
 */
function CompactHeader({ title, message }: { title: string; message: React.ReactNode }) {
  const messageStyle: React.CSSProperties = {
    marginTop: 0,
    color: 'var(--text-secondary)',
    lineHeight: 'var(--line-height-2)',
  };
  return (
    <>
      <AlertDialog.Title>
        <Text size="3" weight="medium" style={{ color: 'var(--text-primary)' }}>
          {title}
        </Text>
      </AlertDialog.Title>
      {/* The primitive's Description, so markup can sit in a div (a paragraph
          cannot hold block content) and still describe the dialog. */}
      <AlertDialogPrimitive.Description asChild>
        <Text as={typeof message === 'string' ? 'p' : 'div'} size="2" style={messageStyle}>
          {message}
        </Text>
      </AlertDialogPrimitive.Description>
    </>
  );
}
