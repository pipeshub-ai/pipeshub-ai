'use client';

import React from 'react';
import { Flex, Text, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

// ─── Props ────────────────────────────────────────────────────────────────────

export interface EmailFieldProps {
  value: string;
  onChange: (value: string) => void;
  /** Field label. Defaults to "Email". */
  label?: string;
  placeholder?: string;
  error?: string;
  autoFocus?: boolean;
  readOnly?: boolean;
  id?: string;
  onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void;
  onBlur?: () => void;
}

// ─── Component ────────────────────────────────────────────────────────────────

/**
 * EmailField — reusable labeled email input.
 *
 * Used by: email-step, single-provider (SSO), multiple-providers.
 */
const EmailField = React.forwardRef<HTMLInputElement, EmailFieldProps>(function EmailField({
  value,
  onChange,
  label,
  placeholder,
  error,
  autoFocus = false,
  readOnly = false,
  id = 'email-field',
  onKeyDown,
  onBlur,
}, ref) {
  const { t } = useTranslation();
  const resolvedLabel = label ?? t('auth.common.emailLabel');
  const resolvedPlaceholder = placeholder ?? t('auth.common.emailPlaceholder');

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
        type="text"
        inputMode="email"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        onBlur={onBlur}
        placeholder={resolvedPlaceholder}
        autoComplete="email"
        autoFocus={autoFocus}
        readOnly={readOnly}
        size="3"
        color={error ? 'red' : undefined}
        style={{
          width: '100%',
          outline: error ? '1px solid var(--red-8)' : undefined,
          opacity: readOnly ? 0.6 : undefined,
          cursor: readOnly ? 'default' : undefined,
        }}
      />
      {error && (
        <Text size="1" style={{ color: 'var(--red-11)', fontWeight: 500 }}>
          {error}
        </Text>
      )}
    </Flex>
  );
});

export default EmailField;
