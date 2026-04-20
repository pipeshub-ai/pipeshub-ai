'use client';

import React from 'react';
import { Dialog, Flex, Text, Button, Box, VisuallyHidden } from '@radix-ui/themes';
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
  message: string;

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

  /** Callback when confirm is clicked */
  onConfirm: () => void;
}

// ========================================
// Component
// ========================================

/**
 * ConfirmationDialog — reusable modal for confirming destructive or important actions.
 *
 * Used for:
 * - Remove user from workspace
 * - Delete group/team
 * - Cancel invite
 * - Any action requiring user confirmation
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
  onConfirm,
}: ConfirmationDialogProps) {
  const handleCancel = () => {
    if (!isLoading) onOpenChange(false);
  };

  return (
    <Dialog.Root open={open} onOpenChange={(v) => !isLoading && onOpenChange(v)}>
      {/* Dark overlay */}
      {open && (
        <Box
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(28, 32, 36, 0.5)',
            zIndex: 999,
            cursor: isLoading ? 'not-allowed' : 'pointer',
          }}
          onClick={handleCancel}
        />
      )}
      <Dialog.Content
        style={{
          maxWidth: '37.5rem',
          padding: 'var(--space-5) 0',
          backgroundColor: 'var(--color-panel-solid)',
          borderRadius: 'var(--radius-5)',
          border: '1px solid var(--olive-a3)',
          boxShadow:
            '0 16px 36px -20px rgba(0, 6, 46, 0.2), 0 16px 64px rgba(0, 0, 85, 0.02), 0 12px 60px rgba(0, 0, 0, 0.15)',
          zIndex: 1000,
          overflow: 'hidden',
        }}
      >
        <VisuallyHidden>
          <Dialog.Title>{title}</Dialog.Title>
        </VisuallyHidden>

        <Flex direction="column" gap="4">
          {/* Title + Message */}
          <Flex
            direction="column"
            gap="3"
            style={{ padding: '0 var(--space-5)' }}
          >
            <Text size="5" weight="bold" style={{ color: 'var(--slate-12)' }}>
              {title}
            </Text>
            <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: '20px' }}>
              {message}
            </Text>
          </Flex>

          {/* Action buttons */}
          <Flex
            justify="end"
            gap="2"
            style={{ padding: '0 var(--space-5)' }}
          >
            <Button
              variant="outline"
              color="gray"
              size="2"
              onClick={handleCancel}
              disabled={isLoading}
              style={{ cursor: isLoading ? 'not-allowed' : 'pointer' }}
            >
              {cancelLabel}
            </Button>
            <LoadingButton
              variant="solid"
              color={confirmVariant === 'danger' ? 'red' : undefined}
              size="2"
              onClick={onConfirm}
              loading={isLoading}
              loadingLabel={confirmLoadingLabel}
            >
              {confirmLabel}
            </LoadingButton>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
