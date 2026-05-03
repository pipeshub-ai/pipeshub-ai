'use client';

import React from 'react';
import { AlertDialog, Box, Button, Flex } from '@radix-ui/themes';
import { LoadingButton } from '@/app/components/ui/loading-button';

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
  confirmVariant?: 'danger' | 'primary';

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
}

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
  cancelLabel = 'Cancel',
  confirmVariant = 'danger',
  isLoading = false,
  confirmLoadingLabel = 'Removing...',
  hideConfirm = false,
  onConfirm,
  container,
}: ConfirmationDialogProps) {
  return (
    <AlertDialog.Root open={open} onOpenChange={(v) => !isLoading && onOpenChange(v)}>
      <AlertDialog.Content
        container={container ?? undefined}
        style={{
          maxWidth: '37.5rem',
          padding: 'var(--space-5)',
          backgroundColor: 'var(--color-panel-solid)',
          borderRadius: 'var(--radius-5)',
          border: '1px solid var(--olive-a3)',
          boxShadow:
            '0 16px 36px -20px rgba(0, 6, 46, 0.2), 0 16px 64px rgba(0, 0, 85, 0.02), 0 12px 60px rgba(0, 0, 0, 0.15)',
        }}
      >
        <AlertDialog.Title style={{ color: 'var(--slate-12)' }}>{title}</AlertDialog.Title>
        {typeof message === 'string' ? (
          <AlertDialog.Description
            size="2"
            style={{ color: 'var(--slate-12)', lineHeight: '20px', marginTop: 8 }}
          >
            {message}
          </AlertDialog.Description>
        ) : (
          <Box style={{ marginTop: 8, color: 'var(--slate-12)', lineHeight: '20px' }}>{message}</Box>
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
              {hideConfirm ? 'Close' : cancelLabel}
            </Button>
          </AlertDialog.Cancel>
          {!hideConfirm && (
            <LoadingButton
              type="button"
              variant="solid"
              color={confirmVariant === 'danger' ? 'red' : undefined}
              size="2"
              onClick={(e) => {
                e.preventDefault();
                onConfirm();
              }}
              loading={isLoading}
              loadingLabel={confirmLoadingLabel}
            >
              {confirmLabel}
            </LoadingButton>
          )}
        </Flex>
      </AlertDialog.Content>
    </AlertDialog.Root>
  );
}
