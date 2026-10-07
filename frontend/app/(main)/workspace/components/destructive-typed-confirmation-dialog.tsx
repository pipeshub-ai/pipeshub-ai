'use client';

import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Dialog, Flex, Text, TextField, Button, Box, VisuallyHidden } from '@radix-ui/themes';
import { LoadingButton } from '@/app/components/ui/loading-button';

export interface DestructiveTypedConfirmationDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  heading: string;
  body: React.ReactNode;
  confirmationKeyword: string;
  confirmInputLabel: string;
  primaryButtonText: string;
  cancelLabel?: string;
  onConfirm: () => void;
  isLoading?: boolean;
  confirmLoadingLabel?: string;
  /**
   * Portal target for the overlay + `Dialog.Content` — same contract as
   * {@link ConfirmationDialog}'s `container` prop. Pass the host from
   * `useWorkspaceDrawerNestedModalHost` when this dialog opens from inside
   * `WorkspaceRightPanel` (z-index ~9201): without it, both the manual
   * overlay below and `Dialog.Content`'s own portal render at their default
   * location (`document.body`, appended BEFORE the drawer's own portal),
   * so the drawer paints on top of them instead of the other way round.
   */
  container?: HTMLElement | null;
}

export function DestructiveTypedConfirmationDialog({
  open,
  onOpenChange,
  heading,
  body,
  confirmationKeyword,
  confirmInputLabel,
  primaryButtonText,
  cancelLabel = 'Cancel',
  onConfirm,
  isLoading = false,
  confirmLoadingLabel = 'ΓÇª',
  container,
}: DestructiveTypedConfirmationDialogProps) {
  const [input, setInput] = useState('');

  useEffect(() => {
    setInput('');
  }, [open, confirmationKeyword]);

  const matches = confirmationKeyword.length > 0 && input === confirmationKeyword;

  const handleCancel = () => {
    if (!isLoading) onOpenChange(false);
  };

  const overlay = open && (
    <Box
      data-testid="destructive-dialog-overlay"
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(28, 32, 36, 0.5)',
        zIndex: 999,
        cursor: isLoading ? 'not-allowed' : 'pointer',
      }}
      onClick={handleCancel}
    />
  );

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(v) => {
        if (!isLoading) onOpenChange(v);
      }}
    >
      {/*
       * Portaled to the same `container` as `Dialog.Content` below (default:
       * document.body) — a plain sibling render here would sit wherever this
       * component lives in the tree, which is the wrong stacking context
       * when that's nested inside `WorkspaceRightPanel`.
       */}
      {overlay && typeof document !== 'undefined' && createPortal(overlay, container ?? document.body)}
      <Dialog.Content
        container={container ?? undefined}
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
        onClick={(e) => e.stopPropagation()}
      >
        <VisuallyHidden>
          <Dialog.Title>{heading}</Dialog.Title>
        </VisuallyHidden>

        <Flex direction="column" gap="4">
          <Flex
            direction="column"
            gap="3"
            style={{ padding: '0 var(--space-5)' }}
          >
            <Text size="5" weight="bold" style={{ color: 'var(--slate-12)' }}>
              {heading}
            </Text>
            <Flex direction="column" gap="2">
              {body}
            </Flex>
          </Flex>

          <Flex
            direction="column"
            gap="2"
            style={{ padding: '0 var(--space-5)' }}
          >
            <Text size="2" style={{ color: 'var(--slate-11)' }}>
              {confirmInputLabel}
            </Text>
            <TextField.Root
              size="2"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={isLoading || confirmationKeyword.length === 0}
              autoComplete="off"
              style={{
                ...(matches
                  ? { borderColor: 'var(--accent-8)', boxShadow: '0 0 0 1px var(--accent-8)' }
                  : {}),
              }}
            />
          </Flex>

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
              color="red"
              size="2"
              onClick={onConfirm}
              disabled={!matches}
              loading={isLoading}
              loadingLabel={confirmLoadingLabel}
            >
              {primaryButtonText}
            </LoadingButton>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
