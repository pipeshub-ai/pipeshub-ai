'use client';

import React, { useState } from 'react';
import { Flex, Text, TextField, IconButton } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

// ─── Props ────────────────────────────────────────────────────────────────────

export interface PasswordFieldProps {
  value: string;
  onChange: (value: string) => void;
  /** Field label. Defaults to "Password". */
  label?: string;
  placeholder?: string;
  /** Inline error shown below the field (e.g. "Incorrect password."). */
  error?: string;
  /** Hint text shown below the field when there's no error. */
  hint?: string;
  /** Show the "Forgot Password" link. */
  showForgotPassword?: boolean;
  onForgotPassword?: () => void;
  forgotLoading?: boolean;
  autoFocus?: boolean;
  autoComplete?: string;
  id?: string;
  /** Disable the field entirely. */
  disabled?: boolean;
  onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void;
  onBlur?: () => void;
}

// ─── Component ────────────────────────────────────────────────────────────────

/**
 * PasswordField — reusable labeled password input.
 *
 * Features: visibility toggle, inline error, optional hint text,
 * optional "Forgot Password" link (right-aligned on the same row as error).
 *
 * Used by: single-provider (password), multiple-providers, change-password (×2).
 */
const PasswordField = React.forwardRef<HTMLInputElement, PasswordFieldProps>(function PasswordField({
  value,
  onChange,
  label,
  placeholder,
  error,
  hint,
  showForgotPassword = false,
  onForgotPassword,
  forgotLoading = false,
  autoFocus = false,
  autoComplete = 'current-password',
  id = 'password-field',
  disabled = false,
  onKeyDown,
  onBlur,
}, ref) {
  const { t } = useTranslation();
  const resolvedLabel = label ?? t('auth.common.passwordLabel');
  const resolvedPlaceholder = placeholder ?? t('auth.common.passwordPlaceholder');
  const [visible, setVisible] = useState(false);

  return (
    <Flex direction="column" gap="1">
      <Text
        as="label"
        htmlFor={id}
        style={{
          color: 'var(--gray-12)',
          fontSize: '14px',
          fontWeight: 500,
          lineHeight: '20px',
        }}
      >
        {resolvedLabel}
      </Text>

      <TextField.Root
        ref={ref}
        id={id}
        type={visible ? 'text' : 'password'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        onBlur={onBlur}
        placeholder={resolvedPlaceholder}
        autoComplete={autoComplete}
        autoFocus={autoFocus}
        required
        disabled={disabled}
        size="3"
        color={error ? 'red' : undefined}
        style={{
          width: '100%',
          outline: error ? '1px solid var(--red-8)' : undefined,
          opacity: disabled ? 0.5 : undefined,
          cursor: disabled ? 'not-allowed' : undefined,
        }}
      >
        <TextField.Slot side="right">
          <IconButton
            type="button"
            size="1"
            variant="ghost"
            color="gray"
            onClick={() => setVisible((v) => !v)}
            aria-label={visible ? t('auth.common.hidePassword') : t('auth.common.showPassword')}
          >
            <span
              className="material-icons-outlined"
              style={{ fontSize: '16px', color: 'var(--gray-10)' }}
            >
              {visible ? 'visibility_off' : 'visibility'}
            </span>
          </IconButton>
        </TextField.Slot>
      </TextField.Root>

      {/* Row below field: left = error or hint, right = forgot password */}
      {(error || hint || showForgotPassword) && (
        <Flex align="center" justify="between" style={{ marginTop: '2px' }}>
          {error ? (
            <Text size="1" style={{ color: 'var(--red-11)', fontWeight: 500 }}>
              {error}
            </Text>
          ) : hint ? (
            <Text size="1" style={{ color: 'var(--gray-10)' }}>
              {hint}
            </Text>
          ) : (
            <span />
          )}
          {showForgotPassword && onForgotPassword && (
            <Text
              size="2"
              asChild
              style={{
                color: 'var(--accent-11)',
                fontWeight: 500,
                cursor: forgotLoading ? 'wait' : 'pointer',
                userSelect: 'none',
                flexShrink: 0,
              }}
              onClick={onForgotPassword}
            >
              <span>{forgotLoading ? t('auth.common.sending') : t('auth.common.forgotPassword')}</span>
            </Text>
          )}
        </Flex>
      )}
    </Flex>
  );
});

export default PasswordField;
