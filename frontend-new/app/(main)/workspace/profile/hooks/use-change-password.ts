'use client';

import { useState, useCallback } from 'react';
import { ProfileApi } from '../api';
import { validatePassword } from '@/lib/utils/validators';

// ========================================
// Types
// ========================================

export interface FormState {
  currentPassword: string;
  newPassword: string;
  confirmPassword: string;
}

export interface FormErrors {
  currentPassword?: string;
  newPassword?: string;
  confirmPassword?: string;
}

export type DialogView = 'form' | 'error';

export interface UseChangePasswordOptions {
  onOpenChange: (open: boolean) => void;
  onSuccess: () => void;
}

// ========================================
// Hook
// ========================================

export function useChangePassword({ onOpenChange, onSuccess }: UseChangePasswordOptions) {
  const [view, setView] = useState<DialogView>('form');
  const [form, setForm] = useState<FormState>({
    currentPassword: '',
    newPassword: '',
    confirmPassword: '',
  });
  const [errors, setErrors] = useState<FormErrors>({});
  const [showNewPassword, setShowNewPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [apiErrorMessage, setApiErrorMessage] = useState<string>(
    'Could not update your password. Please try again.'
  );

  const resetForm = useCallback(() => {
    setForm({ currentPassword: '', newPassword: '', confirmPassword: '' });
    setErrors({});
    setShowNewPassword(false);
    setShowConfirmPassword(false);
    setView('form');
    setApiErrorMessage('Could not update your password. Please try again.');
  }, []);

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

  const validate = useCallback((): boolean => {
    const newErrors: FormErrors = {};

    if (!form.currentPassword) {
      newErrors.currentPassword = 'Current password is required';
    }

    if (!form.newPassword) {
      newErrors.newPassword = 'New password is required';
    } else {
      const pwError = validatePassword(form.newPassword);
      if (pwError) newErrors.newPassword = pwError;
    }

    if (!form.confirmPassword) {
      newErrors.confirmPassword = 'Please confirm your new password';
    } else if (form.confirmPassword !== form.newPassword) {
      newErrors.confirmPassword = "This doesn't match with your new password";
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }, [form]);

  const handleSave = useCallback(async () => {
    if (!validate()) return;

    setIsLoading(true);
    try {
      await ProfileApi.changePassword({
        currentPassword: form.currentPassword,
        newPassword: form.newPassword,
      });
      resetForm();
      onOpenChange(false);
      onSuccess();
    } catch (err: unknown) {
      type ApiErr = { response?: { data?: { error?: { message?: string }; message?: string } } };
      const data = (err as ApiErr)?.response?.data;
      const message =
        data?.error?.message ??
        data?.message ??
        'Could not update your password. Please try again.';
      setApiErrorMessage(message);
      setView('error');
    } finally {
      setIsLoading(false);
    }
  }, [form, validate, resetForm, onOpenChange, onSuccess]);

  const handleTryAgain = useCallback(() => {
    setView('form');
    setErrors({});
  }, []);

  return {
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
  };
}
