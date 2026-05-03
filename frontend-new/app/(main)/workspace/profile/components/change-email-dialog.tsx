'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
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
  const { t } = useTranslation();
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
          <Dialog.Title>{t('workspace.profile.changeEmail.title')}</Dialog.Title>
        </VisuallyHidden>

        {/* ── VIEW: form ──────────────────────────────────────────────── */}
        {view === 'form' && (
          <Flex direction="column" gap="4">
            <Text size="5" weight="bold" style={{ color: 'var(--gray-12)' }}>
              {t('workspace.profile.changeEmail.title')}
            </Text>

            {/* Description */}
            <Flex direction="column" gap="3">
              <Text size="2" style={{ color: 'var(--gray-10)', lineHeight: '20px' }}>
                {t('workspace.profile.changeEmail.description')}
              </Text>
              <Text size="2" style={{ color: 'var(--gray-10)', lineHeight: '20px' }}>
                {t('workspace.profile.changeEmail.validityNote')}
              </Text>
            </Flex>

            {/* Email input */}
            <Flex direction="column" gap="1">
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                {t('workspace.profile.changeEmail.emailLabel')}
              </Text>
              <TextField.Root
                type="email"
                placeholder={t('workspace.profile.changeEmail.emailPlaceholder')}
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
                {t('action.cancel')}
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
                    <span>{t('workspace.profile.changeEmail.validating')}</span>
                  </Flex>
                ) : (
                  t('workspace.profile.changeEmail.validate')
                )}
              </Button>
            </Flex>
          </Flex>
        )}

        {/* ── VIEW: valid ─────────────────────────────────────────────── */}
        {view === 'valid' && (
          <Flex direction="column" gap="4">
            <Text size="5" weight="bold" style={{ color: 'var(--gray-12)' }}>
              {t('workspace.profile.changeEmail.title')}
            </Text>

            {/* Confirmation message */}
            <Text size="2" style={{ color: 'var(--gray-10)', lineHeight: '20px' }}>
              {t('workspace.profile.changeEmail.validSuccess', { email: checkedEmail })}
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
                {t('action.cancel')}
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
                    <span>{t('workspace.profile.changeEmail.sending')}</span>
                  </Flex>
                ) : (
                  t('workspace.profile.changeEmail.sendVerification')
                )}
              </Button>
            </Flex>
          </Flex>
        )}
      </Dialog.Content>
    </Dialog.Root>
  );
}
