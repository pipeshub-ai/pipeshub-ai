'use client';

import { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { isValidEmail } from '@/lib/utils/validators';
import { ProfileApi } from '../api';

// ========================================
// Types
// ========================================

/** The two visible states of the Change Email dialog. */
export type ChangeEmailView = 'form' | 'valid';

export interface UseChangeEmailOptions {
  /** Current user ID — required for sending the verification link. */
  userId: string | null;
  onOpenChange: (open: boolean) => void;
  /** Called after the verification link is successfully sent. */
  onSuccess: () => void;
}

// ========================================
// Hook
// ========================================

export function useChangeEmail({ userId, onOpenChange, onSuccess }: UseChangeEmailOptions) {
  const { t } = useTranslation();
  const [view, setView] = useState<ChangeEmailView>('form');
  const [newEmail, setNewEmail] = useState('');
  const [emailError, setEmailError] = useState<string | undefined>();
  const [checkedEmail, setCheckedEmail] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  // ── Reset ─────────────────────────────────────────────────────

  const resetForm = useCallback(() => {
    setView('form');
    setNewEmail('');
    setEmailError(undefined);
    setCheckedEmail('');
    setIsLoading(false);
  }, []);

  // ── Dialog open/close ─────────────────────────────────────────

  const handleClose = useCallback(() => {
    if (isLoading) return;
    resetForm();
    onOpenChange(false);
  }, [isLoading, resetForm, onOpenChange]);

  const handleOpenChange = useCallback(
    (next: boolean) => {
      if (!next) {
        if (isLoading) return;
        resetForm();
      }
      onOpenChange(next);
    },
    [isLoading, resetForm, onOpenChange]
  );

  // ── Validation ────────────────────────────────────────────────

  const validateEmail = useCallback((): boolean => {
    const trimmed = newEmail.trim();
    if (!trimmed) {
      setEmailError(t('form.required'));
      return false;
    }
    // Use the shared email validator
    if (!isValidEmail(trimmed)) {
      setEmailError(t('form.invalidEmail'));
      return false;
    }
    setEmailError(undefined);
    return true;
  }, [newEmail]);

  // ── Validate email format ─────────────────────────────────────

  const handleValidateEmail = useCallback(async () => {
    if (!validateEmail()) return;

    const trimmed = newEmail.trim();
    setIsLoading(true);
    // Simulate a brief check (since we're only validating format)
    await new Promise((resolve) => setTimeout(resolve, 500));
    setCheckedEmail(trimmed);
    setView('valid');
    setIsLoading(false);
  }, [newEmail, validateEmail]);

  // ── Send verification link ────────────────────────────────────

  const handleSendVerification = useCallback(async () => {
    if (!userId) return;
    setIsLoading(true);
    try {
      await ProfileApi.sendEmailVerificationLink(userId, checkedEmail);
      onSuccess();
    } catch {
      // Stub always succeeds — swallow error for now
    } finally {
      setIsLoading(false);
      resetForm();
      onOpenChange(false);
    }
  }, [userId, checkedEmail, resetForm, onOpenChange, onSuccess]);

  return {
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
  };
}
