'use client';

import React from 'react';
import {
  Dialog,
  Flex,
  Text,
  Button,
  Box,
  TextField,
  Spinner,
  VisuallyHidden,
} from '@radix-ui/themes';
import { useChangeEmail } from '../hooks/use-change-email';

// ========================================
// Types
// ========================================

export interface ChangeEmailDialogProps {
  /** Controls open/close */
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The authenticated user's ID — needed to send the verification link. */
  userId: string | null;
  /** Called after the verification link is successfully sent. */
  onSuccess: () => void;
}

// ========================================
// Component
// ========================================

/**
 * ChangeEmailDialog — multi-step modal for changing the user's company email.
 *
 * Views:
 *  - "form"  — enter new email + "Validate"
 *  - "valid" — email is valid; user can send the verification link
 */
export function ChangeEmailDialog({
  open,
  onOpenChange,
  userId,
  onSuccess,
}: ChangeEmailDialogProps) {
  const {
    view,
    newEmail,
    emailError,
    checkedEmail,
    isLoading,
    setNewEmail,
    setEmailError,
    handleClose,
    handleOpenChange,
    handleValidateEmail,
    handleSendVerification,
  } = useChangeEmail({ userId, onOpenChange, onSuccess });

  // ── Shared dialog content style ──────────────────────────────────────────

  const contentStyle: React.CSSProperties = {
    width: 480,
    maxWidth: 480,
    minWidth: 480,
    padding: 'var(--space-5)',
    backgroundColor: 'var(--color-panel-solid)',
    borderRadius: 'var(--radius-5)',
    border: '1px solid var(--olive-a3)',
    boxShadow:
      '0 16px 36px -20px rgba(0, 6, 46, 0.2), 0 16px 64px rgba(0, 0, 85, 0.02), 0 12px 60px rgba(0, 0, 0, 0.15)',
    zIndex: 1000,
    overflow: 'hidden',
  };

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
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
          onClick={handleClose}
        />
      )}

      <Dialog.Content style={contentStyle}>
        <VisuallyHidden>
          <Dialog.Title>Change Email</Dialog.Title>
        </VisuallyHidden>

        {/* ── VIEW: form ──────────────────────────────────────────────── */}
        {view === 'form' && (
          <Flex direction="column" gap="4">
            <Text size="5" weight="bold" style={{ color: 'var(--gray-12)' }}>
              Change Email
            </Text>

            {/* Description */}
            <Flex direction="column" gap="3">
              <Text size="2" style={{ color: 'var(--gray-10)', lineHeight: '20px' }}>
                If you&apos;d like to change the email address for your account, we&apos;ll send a
                verification link to your new company email account. This change will apply across
                all workspaces that you are a member of.
              </Text>
              <Text size="2" style={{ color: 'var(--gray-10)', lineHeight: '20px' }}>
                Please check if the new company email address is valid.
              </Text>
            </Flex>

            {/* Email input */}
            <Flex direction="column" gap="1">
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                Enter the new email address
              </Text>
              <TextField.Root
                type="email"
                placeholder="eg: name@company.com"
                value={newEmail}
                onChange={(e) => {
                  setNewEmail(e.target.value);
                  if (emailError) setEmailError(undefined);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !isLoading) handleValidateEmail();
                }}
                color={emailError ? 'red' : undefined}
                autoFocus
              />
              {emailError && (
                <Text size="1" style={{ color: 'var(--red-a11)' }}>
                  {emailError}
                </Text>
              )}
            </Flex>

            {/* Buttons */}
            <Flex justify="end" gap="2" style={{ marginTop: 'var(--space-1)' }}>
              <Button
                variant="outline"
                color="gray"
                size="2"
                type="button"
                onClick={handleClose}
                disabled={isLoading}
                style={{ cursor: isLoading ? 'not-allowed' : 'pointer' }}
              >
                Cancel
              </Button>
              <Button
                variant="solid"
                size="2"
                type="button"
                onClick={handleValidateEmail}
                disabled={isLoading || !newEmail.trim()}
                style={{ cursor: isLoading ? 'not-allowed' : 'pointer', minWidth: 120 }}
              >
                {isLoading ? (
                  <Flex align="center" gap="2">
                    <Spinner size="1" />
                    <span>Validating…</span>
                  </Flex>
                ) : (
                  'Validate'
                )}
              </Button>
            </Flex>
          </Flex>
        )}

        {/* ── VIEW: valid ─────────────────────────────────────────────── */}
        {view === 'valid' && (
          <Flex direction="column" gap="4">
            <Text size="5" weight="bold" style={{ color: 'var(--gray-12)' }}>
              Change Email
            </Text>

            {/* Confirmation message */}
            <Text size="2" style={{ color: 'var(--gray-10)', lineHeight: '20px' }}>
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                {checkedEmail}
              </Text>{' '}
              is valid. Please proceed to receive the verification link.
            </Text>

            {/* Buttons */}
            <Flex justify="end" gap="2" style={{ marginTop: 'var(--space-1)' }}>
              <Button
                variant="outline"
                color="gray"
                size="2"
                type="button"
                onClick={handleClose}
                disabled={isLoading}
                style={{ cursor: isLoading ? 'not-allowed' : 'pointer' }}
              >
                Cancel
              </Button>
              <Button
                variant="solid"
                size="2"
                type="button"
                onClick={handleSendVerification}
                disabled={isLoading}
                style={{ cursor: isLoading ? 'not-allowed' : 'pointer', minWidth: 170 }}
              >
                {isLoading ? (
                  <Flex align="center" gap="2">
                    <Spinner size="1" />
                    <span>Sending…</span>
                  </Flex>
                ) : (
                  'Send verification link'
                )}
              </Button>
            </Flex>
          </Flex>
        )}
      </Dialog.Content>
    </Dialog.Root>
  );
}
