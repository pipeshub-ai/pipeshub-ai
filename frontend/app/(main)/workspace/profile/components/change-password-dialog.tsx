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
  IconButton,
  VisuallyHidden,
} from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { useChangePassword } from '../hooks/use-change-password';
import { validatePassword } from '@/lib/utils/validators';

// ========================================
// Types
// ========================================

export interface ChangePasswordDialogProps {
  /** Controls open/close */
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called after a successful password change (before dialog closes) */
  onSuccess: () => void;
}

// ========================================
// Component
// ========================================

/**
 * ChangePasswordDialog — inline modal for changing the user's account password.
 *
 * Views:
 *  - "form"  — three password fields with validation
 *  - "error" — API error state with Try Again / Cancel actions
 */
export function ChangePasswordDialog({
  open,
  onOpenChange,
  onSuccess,
}: ChangePasswordDialogProps) {
  const {
    view,
    form,
    errors,
    showNewPassword,
    showConfirmPassword,
    isLoading,
    apiErrorMessage,
    setForm,
    setErrors,
    setShowNewPassword,
    setShowConfirmPassword,
    handleClose,
    handleOpenChange,
    handleSave,
    handleTryAgain,
  } = useChangePassword({ onOpenChange, onSuccess });

  const { t } = useTranslation();
  const isFormValid =
    form.currentPassword.trim() !== '' &&
    form.newPassword.trim() !== '' &&
    form.confirmPassword.trim() !== '' &&
    validatePassword(form.newPassword) === null &&
    form.confirmPassword === form.newPassword;

  const isSaveDisabled = isLoading || !isFormValid;

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      {/* Dark overlay — matches ConfirmationDialog pattern */}
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

      <Dialog.Content
        style={{
          width: '37.5rem',
          minWidth: 480,
          padding: 'var(--space-5)',
          background: 'var(--slate-2)',
          borderRadius: 'var(--radius-5)',
          border: '1px solid var(--olive-a3)',
          boxShadow: '0 16px 36px -20px var(--slate-a7, rgba(217, 237, 255, 0.25)), 0 16px 64px 0 var(--slate-a2, rgba(216, 244, 246, 0.04)), 0 12px 60px 0 var(--black-a3, rgba(0, 0, 0, 0.15))',
          zIndex: 1000,
          overflow: 'hidden',
        }}
      >
        <VisuallyHidden>
          <Dialog.Title>{t('workspace.profile.changePassword.title')}</Dialog.Title>
        </VisuallyHidden>

        {view === 'form' ? (
          /* ── Form view ─── */
          <Flex direction="column" gap="4">
            <Text size="5" weight="bold" style={{ color: 'var(--gray-12)' }}>
              {t('workspace.profile.changePassword.title')}
            </Text>

            {/* Current Password */}
            <Flex direction="column" gap="1">
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                {t('workspace.profile.changePassword.currentPassword')}
              </Text>
              <TextField.Root
                type="password"
                placeholder={t('workspace.profile.changePassword.currentPasswordPlaceholder')}
                value={form.currentPassword}
                onChange={(e) => {
                  const value = e.target.value;
                  setForm((f) => ({ ...f, currentPassword: value }));
                  if (errors.currentPassword)
                    setErrors((prev) => ({ ...prev, currentPassword: undefined }));
                }}
                color={errors.currentPassword ? 'red' : undefined}
              />
              {errors.currentPassword && (
                <Text size="1" style={{ color: 'var(--red-a11)' }}>
                  {errors.currentPassword}
                </Text>
              )}
            </Flex>

            {/* New Password */}
            <Flex direction="column" gap="1">
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                {t('workspace.profile.changePassword.newPassword')}
              </Text>
              <Text size="1" style={{ color: 'var(--gray-10)', fontWeight: 300 }}>
                {t('workspace.profile.changePassword.passwordRequirement')}
              </Text>
              <TextField.Root
                type={showNewPassword ? 'text' : 'password'}
                placeholder={t('workspace.profile.changePassword.newPasswordPlaceholder')}
                value={form.newPassword}
                onChange={(e) => {
                  const value = e.target.value;
                  setForm((f) => ({ ...f, newPassword: value }));
                  // Inline strength validation
                  const pwError = value ? validatePassword(value) : null;
                  setErrors((prev) => ({ ...prev, newPassword: pwError ?? undefined }));
                  // Re-check confirm match if already filled
                  if (form.confirmPassword) {
                    setErrors((prev) => ({
                      ...prev,
                      confirmPassword:
                        form.confirmPassword !== value
                          ? t('workspace.profile.changePassword.mismatchError')
                          : undefined,
                    }));
                  }
                }}
                color={errors.newPassword ? 'red' : undefined}
              >
                <TextField.Slot side="right">
                  <IconButton
                    variant="ghost"
                    color="gray"
                    size="1"
                    type="button"
                    onClick={() => setShowNewPassword((v) => !v)}
                    style={{ cursor: 'pointer' }}
                  >
                    <MaterialIcon
                      name={showNewPassword ? 'visibility' : 'visibility_off'}
                      size={16}
                      color="var(--gray-9)"
                    />
                  </IconButton>
                </TextField.Slot>
              </TextField.Root>
              {errors.newPassword && (
                <Text size="1" style={{ color: 'var(--red-a11)' }}>
                  {errors.newPassword}
                </Text>
              )}
            </Flex>

            {/* Confirm New Password */}
            <Flex direction="column" gap="1">
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                {t('workspace.profile.changePassword.confirmPassword')}
              </Text>
              <TextField.Root
                type={showConfirmPassword ? 'text' : 'password'}
                placeholder={t('workspace.profile.changePassword.confirmPasswordPlaceholder')}
                value={form.confirmPassword}
                onChange={(e) => {
                  const value = e.target.value;
                  setForm((f) => ({ ...f, confirmPassword: value }));
                  // Inline match validation
                  if (value && value !== form.newPassword) {
                    setErrors((prev) => ({
                      ...prev,
                      confirmPassword: t('workspace.profile.changePassword.mismatchError'),
                    }));
                  } else {
                    setErrors((prev) => ({ ...prev, confirmPassword: undefined }));
                  }
                }}
                color={errors.confirmPassword ? 'red' : undefined}
              >
                <TextField.Slot side="right">
                  <IconButton
                    variant="ghost"
                    color="gray"
                    size="1"
                    type="button"
                    onClick={() => setShowConfirmPassword((v) => !v)}
                    style={{ cursor: 'pointer' }}
                  >
                    <MaterialIcon
                      name={showConfirmPassword ? 'visibility' : 'visibility_off'}
                      size={16}
                      color="var(--gray-9)"
                    />
                  </IconButton>
                </TextField.Slot>
              </TextField.Root>
              {errors.confirmPassword && (
                <Text size="1" style={{ color: 'var(--red-a11)' }}>
                  {errors.confirmPassword}
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
              <LoadingButton
                variant="solid"
                size="2"
                type="button"
                onClick={handleSave}
                disabled={!isFormValid}
                loading={isLoading}
                loadingLabel={t('action.saving')}
                style={{ background: isSaveDisabled ? 'var(--gray-6)' : 'var(--emerald-9)' }}
              >
                {t('action.save')}
              </LoadingButton>
            </Flex>
          </Flex>
        ) : (
          /* ── Error view ─── */
          <Flex direction="column" gap="4">
            {/* Icon row — matches avatar cell squarish style */}
            <Flex
              align="center"
              justify="center"
              style={{
                width: 32,
                height: 32,
                borderRadius: 'var(--radius-2)',
                backgroundColor: 'var(--red-3)',
                flexShrink: 0,
              }}
            >
              <MaterialIcon name="error_outline" size={18} color="var(--red-9)" />
            </Flex>

            <Flex direction="column" gap="2">
              <Text size="4" weight="bold" style={{ color: 'var(--gray-12)' }}>
                {t('workspace.profile.changePassword.updateError')}
              </Text>
              <Text
                size="2"
                style={{ color: 'var(--gray-10)', lineHeight: '20px' }}
              >
                {apiErrorMessage}
              </Text>
            </Flex>

            {/* Buttons */}
            <Flex justify="end" gap="2">
              <Button
                variant="outline"
                color="gray"
                size="2"
                type="button"
                onClick={handleClose}
                style={{ cursor: 'pointer' }}
              >
                {t('action.cancel')}
              </Button>
              <Button
                variant="solid"
                size="2"
                type="button"
                onClick={handleTryAgain}
                style={{ cursor: 'pointer' }}
              >
                {t('message.tryAgain')}
              </Button>
            </Flex>
          </Flex>
        )}
      </Dialog.Content>
    </Dialog.Root>
  );
}
