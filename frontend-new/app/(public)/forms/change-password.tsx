'use client';

import React, { useRef, useState } from 'react';
import { Box, Flex } from '@radix-ui/themes';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { validatePassword, PASSWORD_RULES } from '@/lib/utils/validators';
import type { JwtUser } from '@/lib/utils/auth-helpers';
import AuthTitleSection from '../components/auth-title-section';
import UserBadge from '../components/user-badge';
import { PasswordField, ErrorBanner } from './form-components';
import { AuthApi } from '../api';
import { toast } from '@/lib/store/toast-store';

// ─── Props ────────────────────────────────────────────────────────────────────

export interface ChangePasswordProps {
  /** The JWT reset token from the email link. */
  token: string;
  /** Decoded user info (for the top-right badge). */
  user?: JwtUser;
  /** Called after a successful password change. */
  onSuccess: () => void;
  /** Disable all inputs (e.g. when token is missing/invalid). */
  disabled?: boolean;
}

// ─── Component ────────────────────────────────────────────────────────────────

/**
 * ChangePassword — reset-password form.
 *
 * Composes: AuthTitleSection("Change Password") + UserBadge +
 * two PasswordField instances + ErrorBanner + Save button.
 *
 * Matches Figma node 5005-5571.
 */
export default function ChangePassword({
  token,
  user,
  onSuccess,
  disabled = false,
}: ChangePasswordProps) {
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [newPwError, setNewPwError] = useState('');
  const [confirmError, setConfirmError] = useState('');
  const [serverError, setServerError] = useState('');
  const confirmRef = useRef<HTMLInputElement>(null);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setNewPwError('');
    setConfirmError('');
    setServerError('');

    const pwError = validatePassword(newPassword);
    if (pwError) {
      setNewPwError(pwError);
      return;
    }
    if (newPassword !== confirmPassword) {
      setConfirmError('Passwords do not match.');
      return;
    }

    setLoading(true);
    try {
      await AuthApi.resetPasswordViaEmailLink(token, newPassword);
      onSuccess();
    } catch (error: unknown) {
      type HttpErr = { response?: { data?: { error?: { message?: string }; message?: string } }; message?: string };
      const msg = (
        (error as HttpErr)?.response?.data?.error?.message ||
        (error as HttpErr)?.response?.data?.message ||
        (error as HttpErr)?.message ||
        ''
      ).toLowerCase();

      if (msg.includes('blocked') || msg.includes('multiple incorrect')) {
        toast.error('Your account has been disabled.', {
          description:
            'You have entered incorrect credentials too many times',
          duration: null,
        });
      } else {
        setServerError(
          msg.includes('expired') || msg.includes('invalid token')
            ? 'This reset link has expired. Please request a new one from the sign-in page.'
            : msg || 'Failed to reset password. Please try again.',
        );
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <Box style={{ width: '100%', maxWidth: '440px', position: 'relative' }}>
      {/* ── User badge (top-right) ─────────────────────────────── */}
      {user && (user.email || user.name) && (
        <Box style={{ position: 'absolute', top: '-8px', right: 0 }}>
          <UserBadge user={user} />
        </Box>
      )}

      <AuthTitleSection title="Change Password" subtitle="" />

      {/* ── Form ─────────────────────────────────────────────── */}
      <form onSubmit={handleSave}>
        <Flex direction="column" gap="4">
          <PasswordField
            value={newPassword}
            onChange={(v) => {
              setNewPassword(v);
              setNewPwError('');
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                const err = validatePassword(newPassword);
                if (err) {
                  setNewPwError(err);
                  return;
                }
                setNewPwError('');
                confirmRef.current?.focus();
              }
            }}
            onBlur={() => {
              if (newPassword) {
                const err = validatePassword(newPassword);
                if (err) setNewPwError(err);
              }
            }}
            label="New Password*"
            placeholder="Enter your new password"
            error={newPwError}
            hint={newPwError ? undefined : PASSWORD_RULES}
            autoComplete="new-password"
            autoFocus
            disabled={disabled}
            id="new-password"
          />

          <PasswordField
            ref={confirmRef}
            value={confirmPassword}
            onChange={(v) => {
              setConfirmPassword(v);
              setConfirmError('');
            }}
            onBlur={() => {
              if (confirmPassword && confirmPassword !== newPassword) {
                setConfirmError('Passwords do not match.');
              }
            }}
            label="Confirm Password*"
            placeholder="Confirm your password"
            error={confirmError}
            autoComplete="new-password"
            disabled={disabled}
            id="confirm-password"
          />

          {serverError && <ErrorBanner message={serverError} />}

          <LoadingButton
            type="submit"
            size="3"
            disabled={disabled || !newPassword || !confirmPassword}
            loading={loading}
            loadingLabel="Saving…"
            style={{
              width: '100%',
              backgroundColor:
                !disabled && newPassword && confirmPassword
                  ? 'var(--accent-9)'
                  : undefined,
              color:
                !disabled && newPassword && confirmPassword ? 'white' : undefined,
              fontWeight: 500,
            }}
          >
            Save
          </LoadingButton>
        </Flex>
      </form>
    </Box>
  );
}
