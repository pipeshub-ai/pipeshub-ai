'use client';

import React, { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Dialog, Flex, Text, TextField, Button, VisuallyHidden } from '@radix-ui/themes';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';

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
   * Portal target for Radix's overlay + `Dialog.Content` — same contract as
   * {@link ConfirmationDialog}'s `container` prop. Pass the host from
   * `useWorkspaceDrawerNestedModalHost` when this dialog opens from inside
   * `WorkspaceRightPanel` (z-index ~9201): without it, the dialog portals to
   * `document.body` BEFORE the drawer's own portal, so the drawer paints on top.
   */
  container?: HTMLElement | null;
  /** A warning icon before the heading (org deletion, ORGDEL). */
  headingIcon?: string;
  /** An icon on the destructive button. */
  confirmIcon?: string;
  /** On a phone, open as a bottom sheet with full-width buttons. */
  phoneSheet?: boolean;
  /** Wider content (two columns of what is erased and kept). */
  maxWidth?: string;
  /** Shown under the box while what is typed is not the keyword; a generic line when omitted. */
  mismatchHint?: string;
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
  confirmLoadingLabel = '…',
  container,
  headingIcon,
  confirmIcon,
  phoneSheet = false,
  maxWidth = '37.5rem',
  mismatchHint,
}: DestructiveTypedConfirmationDialogProps) {
  const { t } = useTranslation();
  const [input, setInput] = useState('');
  const hintId = useId();
  const isPhone = useIsMobile();
  const sheet = phoneSheet && isPhone;

  useEffect(() => {
    setInput('');
  }, [open, confirmationKeyword]);

  const matches = confirmationKeyword.length > 0 && input === confirmationKeyword;
  // An empty box is not a mistake yet; only text that is not the keyword gets the hint.
  const mismatch = input.length > 0 && !matches;

  const handleCancel = () => {
    if (!isLoading) onOpenChange(false);
  };

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(v) => {
        if (!isLoading) onOpenChange(v);
      }}
    >
      <Dialog.Content
        container={container ?? undefined}
        style={{
          maxWidth: sheet ? '100vw' : maxWidth,
          padding: 'var(--space-5) 0',
          borderRadius: sheet ? 'var(--radius-5) var(--radius-5) 0 0' : 'var(--radius-5)',
          ...(sheet
            ? {
                position: 'fixed',
                left: 0,
                right: 0,
                bottom: 0,
                width: '100vw',
                maxHeight: '92dvh',
                overflowY: 'auto',
                margin: 0,
              }
            : {}),
          border: '1px solid var(--olive-a3)',
          boxShadow:
            '0 16px 36px -20px rgba(0, 6, 46, 0.2), 0 16px 64px rgba(0, 0, 85, 0.02), 0 12px 60px rgba(0, 0, 0, 0.15)',
          ...(sheet ? {} : { overflow: 'hidden' }),
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
            <Flex align="center" gap="2">
              {headingIcon && <MaterialIcon name={headingIcon} size={22} color="var(--red-11)" />}
              <Text size="5" weight="bold" style={{ color: 'var(--slate-12)' }}>
                {heading}
              </Text>
            </Flex>
            <Flex direction="column" gap="2">
              {body}
            </Flex>
          </Flex>

          <Flex
            direction="column"
            gap="2"
            style={{ padding: '0 var(--space-5)' }}
          >
            <Text as="label" htmlFor={`${hintId}-input`} size="2" style={{ color: 'var(--slate-11)' }}>
              {confirmInputLabel}
            </Text>
            <TextField.Root
              size="2"
              // The accent focus ring reads as "correct": keep it grey until the text matches.
              color={matches ? undefined : 'gray'}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={isLoading || confirmationKeyword.length === 0}
              autoComplete="off"
              id={`${hintId}-input`}
              aria-invalid={mismatch || undefined}
              aria-describedby={mismatch ? hintId : undefined}
              data-state={matches ? 'match' : mismatch ? 'mismatch' : 'empty'}
              data-testid="typed-confirm-input"
              style={
                matches ? { boxShadow: 'inset 0 0 0 1px var(--accent-8), 0 0 0 1px var(--accent-8)' } : undefined
              }
            >
              {matches && (
                <TextField.Slot side="right">
                  <MaterialIcon name="check_circle" size={16} color="var(--accent-11)" />
                </TextField.Slot>
              )}
            </TextField.Root>
            {mismatch && (
              <Flex align="center" gap="1" id={hintId} data-testid="typed-confirm-mismatch">
                <MaterialIcon name="info" size={14} color="var(--slate-11)" />
                <Text size="1" style={{ color: 'var(--slate-11)' }}>
                  {mismatchHint ?? t('dialog.typedConfirmMismatch')}
                </Text>
              </Flex>
            )}
          </Flex>

          <Flex
            justify="end"
            gap="2"
            direction={sheet ? 'column-reverse' : 'row'}
            style={{ padding: '0 var(--space-5)' }}
          >
            <Button
              variant="outline"
              color="gray"
              size={sheet ? '3' : '2'}
              onClick={handleCancel}
              disabled={isLoading}
              style={{ cursor: isLoading ? 'not-allowed' : 'pointer', ...(sheet ? { width: '100%' } : {}) }}
            >
              {cancelLabel}
            </Button>
            <LoadingButton
              variant="solid"
              color="red"
              size={sheet ? '3' : '2'}
              onClick={onConfirm}
              disabled={!matches}
              loading={isLoading}
              loadingLabel={confirmLoadingLabel}
              style={sheet ? { width: '100%' } : undefined}
            >
              {confirmIcon && <MaterialIcon name={confirmIcon} size={16} />}
              {primaryButtonText}
            </LoadingButton>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
